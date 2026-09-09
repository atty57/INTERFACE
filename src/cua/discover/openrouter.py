"""An OpenRouter planner, for running discovery on a key you already have.

The first-class path is ``ClaudePlanner``: the Anthropic SDK against Claude Opus 5. This is
an alternative decision source for the same loop — same digest, same untrusted-data
envelope, same policy gate, same Recorder — reached with ``--planner openrouter``.
``provenance.recorded_by`` records which one produced an artifact.

Written against the stdlib rather than another SDK: it is one POST, and a discovery run is
not worth a dependency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from ..surface.base import ElementDigest
from . import prompts
from .loop import ToolCall

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"


class OpenRouterUnavailable(Exception):
    """No key, or the endpoint refused the request. Fail loudly; do not silently degrade."""


class OpenRouterPlanner:
    def __init__(
        self, system: str, model: str = DEFAULT_MODEL, max_tokens: int = 2000
    ) -> None:
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise OpenRouterUnavailable("OPENROUTER_API_KEY is not set")
        self.model = model
        self.max_tokens = max_tokens
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        self._pending_call_id: str | None = None
        self._tokens = 0

    @property
    def tokens(self) -> int:
        return self._tokens

    def decide(self, observation: str, digest: ElementDigest) -> ToolCall:
        del digest  # the model reads the digest through the observation text
        if self._pending_call_id is None:
            self.messages.append({"role": "user", "content": observation})
        else:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": self._pending_call_id,
                    "content": observation,
                }
            )
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self.messages,
            "tools": [_as_function(tool) for tool in prompts.TOOLS],
            "tool_choice": "auto",
        }
        body = self._post(payload)
        usage = body.get("usage") or {}
        self._tokens += int(usage.get("total_tokens", 0))
        message = body["choices"][0]["message"]
        self.messages.append(message)

        calls = message.get("tool_calls") or []
        if not calls:
            self._pending_call_id = None
            return ToolCall(name="stuck", args={"reason": "the model answered without acting"})
        call = calls[0]
        self._pending_call_id = call.get("id")
        # Arguments arrive as a JSON string; parse it, never string-match it.
        return ToolCall(name=call["function"]["name"], args=json.loads(call["function"]["arguments"] or "{}"))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-Title": "cua discovery",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return dict(json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as failure:
            detail = failure.read().decode("utf-8", "replace")[:400]
            raise OpenRouterUnavailable(f"HTTP {failure.code}: {detail}") from failure
        except urllib.error.URLError as failure:
            raise OpenRouterUnavailable(str(failure)) from failure


def _as_function(tool: dict[str, Any]) -> dict[str, Any]:
    """Anthropic tool shape to the OpenAI-compatible function shape OpenRouter expects."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }
