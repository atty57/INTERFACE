"""The discovery loop: observe, decide, act, against a live surface, with a model in it.

This is the one part of the system that cannot be faked, and it is deliberately not in the
test suite: a real model against a live surface is non-deterministic, and a discovery test
with a mocked model would prove nothing while suggesting the loop was verified. It is
exercised once, to produce evidence.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, cast

from pydantic import BaseModel

from ..artifact.models import Provenance
from ..evidence.bus import EvidenceBus
from ..policy.gate import ConfirmationRequired, PolicyDenied
from ..surface.base import Action, ElementDigest, LocatorUnresolved, SurfaceError
from ..surface.web import WebSurface
from . import prompts
from .recorder import Recorder, probe_checkpoint
from .stuck import Budget, StuckDetector

MODEL = "claude-opus-5"


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any] = {}


class Planner(Protocol):
    """What the loop needs from a model. Kept narrow so the loop is readable."""

    def decide(self, observation: str, digest: ElementDigest) -> ToolCall: ...

    @property
    def tokens(self) -> int: ...


class ClaudePlanner:
    """Claude Opus 5 over the Messages API, with the digest arriving as tool results."""

    def __init__(self, system: str, model: str = MODEL, max_tokens: int = 4000) -> None:
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.system = system
        self.messages: list[dict[str, Any]] = []
        self._pending_tool_use_id: str | None = None
        self._tokens = 0

    @property
    def tokens(self) -> int:
        return self._tokens

    def decide(self, observation: str, digest: ElementDigest) -> ToolCall:
        del digest  # the model reads the digest through the observation text, not the object
        if self._pending_tool_use_id is None:
            self.messages.append({"role": "user", "content": observation})
        else:
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": self._pending_tool_use_id,
                            "content": observation,
                        }
                    ],
                }
            )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system,
            tools=cast(Any, prompts.TOOLS),
            messages=cast(Any, self.messages),
        )
        self._tokens += response.usage.input_tokens + response.usage.output_tokens
        self.messages.append({"role": "assistant", "content": response.content})
        for block in response.content:
            if block.type == "tool_use":
                self._pending_tool_use_id = block.id
                # Tool inputs are JSON round-tripped, never string-matched.
                return ToolCall(name=block.name, args=json.loads(json.dumps(block.input)))
        self._pending_tool_use_id = None
        return ToolCall(name="stuck", args={"reason": "the model answered without acting"})


class DiscoveryOutcome(BaseModel):
    status: str  # done | stuck | budget
    trigger: str = ""
    reason: str = ""
    steps_taken: int = 0
    final_values: dict[str, str] = {}


class DiscoveryEngine:
    def __init__(
        self,
        surface: WebSurface,
        planner: Planner,
        recorder: Recorder,
        evidence: EvidenceBus,
        budget: Budget | None = None,
    ) -> None:
        self.surface = surface
        self.planner = planner
        self.recorder = recorder
        self.evidence = evidence
        self.detector = StuckDetector(budget or Budget())

    def run(self, goal: str, target_url: str) -> DiscoveryOutcome:
        self._act_and_record(Action(kind="navigate", url=target_url), note="")
        note = "opened the target application"

        while True:
            self.surface.page.wait_for_timeout(250)
            digest = self.surface.snapshot()
            self.detector.observe(digest.fingerprint())
            stopped = self.detector.trigger()
            if stopped:
                trigger, reason = stopped
                self.evidence.log("stopped", trigger=trigger, reason=reason)
                return DiscoveryOutcome(
                    status="stuck" if trigger == "no_progress" else "budget",
                    trigger=trigger,
                    reason=reason,
                    steps_taken=self.detector.steps,
                )

            observation = prompts.observation(
                goal, digest, self.detector.steps + 1, self.detector.budget.max_steps, note
            )
            call = self.planner.decide(observation, digest)
            self.detector.spend(self.planner.tokens - self.detector.tokens)
            self.detector.steps += 1
            self.evidence.log("decision", tool=call.name, args=call.args)

            if call.name == "done":
                values = self.surface.label_value_pairs()
                self.evidence.screenshot(self.surface.page, f"step-{self.detector.steps}-done")
                return DiscoveryOutcome(
                    status="done",
                    trigger="explicit",
                    reason=str(call.args.get("summary", "")),
                    steps_taken=self.detector.steps,
                    final_values=values,
                )
            if call.name == "stuck":
                self.evidence.screenshot(self.surface.page, f"step-{self.detector.steps}-stuck")
                return DiscoveryOutcome(
                    status="stuck",
                    trigger="explicit",
                    reason=str(call.args.get("reason", "")),
                    steps_taken=self.detector.steps,
                )

            action = _to_action(call, digest_size=len(digest.entries))
            if action is None:
                note = f"'{call.name}' is not a tool you can use here."
                continue
            note = self._act_and_record(action, note)
            self.evidence.screenshot(self.surface.page, f"step-{self.detector.steps}")

    def _act_and_record(self, action: Action, note: str) -> str:
        """Act through the gate. A denial is logged and the loop continues — it is data."""
        before_text = self.surface.page_text()
        url_before = self.surface.page.url
        label = ""
        try:
            entry = next(
                (e for e in self.surface.snapshot().entries if e.index == action.index), None
            )
            label = entry.accessible_name if entry else ""
            effect = self.surface.act(action)
        except PolicyDenied as denied:
            return f"REFUSED by policy: {denied}. Do not retry it; find another route or stop."
        except ConfirmationRequired as confirm:
            return f"REFUSED, needs human confirmation: {confirm}. Do not retry it."
        except (LocatorUnresolved, SurfaceError) as failure:
            return f"That did not work: {failure}"

        self.surface.page.wait_for_timeout(400)
        after_text = self.surface.page_text()
        checkpoint = probe_checkpoint(
            action, after_text, before_text, url_before, self.surface.page.url
        )
        if action.index is not None and entry is not None:
            action = action.model_copy(update={"target": entry.to_descriptor(), "index": None})
        recorded = self.recorder.record(
            action, label, checkpoint, url_before, self.surface.page.url, effect.tier
        )
        self.evidence.log(
            "acted",
            kind=action.kind,
            label=label,
            recorded=recorded,
            checkpoint=checkpoint.model_dump() if checkpoint else None,
        )
        if checkpoint is None:
            return "That action did not change the screen in a way worth recording."
        return f"Verified: {checkpoint.kind} '{checkpoint.matcher}'."


def _to_action(call: ToolCall, digest_size: int) -> Action | None:
    if call.name == "navigate":
        return Action(kind="navigate", url=str(call.args.get("url", "")))
    index = call.args.get("index")
    if not isinstance(index, int) or not 0 <= index < digest_size:
        return None
    if call.name == "click":
        return Action(kind="click", index=index)
    if call.name == "type":
        return Action(kind="type", index=index, value=str(call.args.get("value", "")))
    return None


def default_provenance(run_id: str, evidence: EvidenceBus, model: str = MODEL) -> Provenance:
    return Provenance(recorded_by=model, run_id=run_id, trace_ref=evidence.ref)
