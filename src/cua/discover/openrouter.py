"""An OpenRouter planner, for running discovery on a key you already have.

The first-class path is ``ClaudePlanner``: the Anthropic SDK against Claude Opus 5. This is
an alternative decision source for the same loop — same digest, same untrusted-data
envelope, same policy gate, same Recorder — reached with ``--planner openrouter``.
``provenance.recorded_by`` records which one produced an artifact.

Written against the stdlib rather than another SDK: it is one POST, and a discovery run is
not worth a dependency.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from ..surface.base import ElementDigest
from . import prompts
from .loop import ToolCall

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
RETRIES = 4
NOT_EXECUTED = (
    "not executed: this loop performs one action per turn, against a freshly observed "
    "screen, because acting changes the digest indices. Re-issue it if you still want it."
)
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
        self._pending_call_ids: list[str] = []
        self._tokens = 0

    @property
    def tokens(self) -> int:
        return self._tokens

    def decide(
        self, observation: str, digest: ElementDigest, screenshot: bytes | None = None
    ) -> ToolCall:
        del digest  # the model reads the digest through the observation text
        if not self._pending_call_ids:
            self.messages.append({"role": "user", "content": observation})
        else:
            # Every tool call the model made must be answered, or the conversation is
            # malformed. The first gets the new screen; the rest are told plainly that they
            # did not run, so the model's picture of what happened stays true.
            for position, call_id in enumerate(self._pending_call_ids):
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": observation if position == 0 else NOT_EXECUTED,
                    }
                )
        _drop_old_images(self.messages)
        if screenshot:
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "The screen, for layout reasoning only."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(screenshot).decode()
                            },
                        },
                    ],
                }
            )
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self.messages,
            "tools": [_as_function(tool) for tool in prompts.TOOLS],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
        body = self._post(payload)
        usage = body.get("usage") or {}
        self._tokens += int(usage.get("total_tokens", 0))
        message = body["choices"][0]["message"]
        self.messages.append(message)

        calls = message.get("tool_calls") or []
        if not calls:
            self._pending_call_ids = []
            return ToolCall(name="stuck", args={"reason": "the model answered without acting"})
        call = calls[0]
        self._pending_call_ids = [c.get("id") for c in calls if c.get("id")]
        # Arguments arrive as a JSON string; parse it, never string-match it.
        return ToolCall(name=call["function"]["name"], args=json.loads(call["function"]["arguments"] or "{}"))

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        last: Exception | None = None
        for attempt in range(RETRIES):
            request = urllib.request.Request(
                ENDPOINT,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "cua discovery",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return dict(json.loads(response.read().decode("utf-8")))
            except urllib.error.HTTPError as failure:
                detail = failure.read().decode("utf-8", "replace")[:400]
                if failure.code < 500:
                    raise OpenRouterUnavailable(f"HTTP {failure.code}: {detail}") from failure
                last = OpenRouterUnavailable(f"HTTP {failure.code}: {detail}")
            except (urllib.error.URLError, http.client.HTTPException, OSError) as failure:
                last = OpenRouterUnavailable(str(failure))
            time.sleep(2 ** attempt)
        raise last or OpenRouterUnavailable("no response")


def _drop_old_images(messages: list[dict[str, Any]]) -> None:
    """Only the current screen needs a picture.

    Re-sending every earlier screenshot grows the request without bound, which is both
    expensive and the fastest way to have a long run die mid-flight.
    """
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            block.get("type") == "image_url" for block in content if isinstance(block, dict)
        ):
            message["content"] = "(an earlier screen, no longer shown)"


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
