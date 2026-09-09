"""Escalation and control transfer. The session is the shared state; only the lease moves.

Nothing is serialized or migrated across the handoff. Because the browser is local and
visible, "the human takes control of the live session" is physically true: cookies,
navigation state and half-filled forms all survive, because nothing was torn down.

The operator console is a mock surface over a real mechanism — the lease transitions, the
event capture, and the re-anchor below are what a production console would drive.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..artifact.models import CapabilityArtifact, Step
from ..evidence.bus import EvidenceBus
from ..replay.detectors import evaluate
# What the escalator hands back to the engine: the step is done, or it should be repeated.
from ..replay.engine import RESUME, RETRY
from ..session.human_events import HumanActivityRecorder
from ..session.lease import Holder

State = Literal["open", "claimed", "resolved", "aborted", "abandoned"]


class InterventionRequest(BaseModel):
    """Everything an operator needs to act without reconstructing context (brief 3.6)."""

    id: str = Field(default_factory=lambda: f"esc-{uuid.uuid4().hex[:8]}")
    capability_id: str
    goal: str
    step_id: str
    why_stopped: str
    expected: str
    observed: str
    last_good_checkpoint: str = ""
    screenshot_ref: str = ""
    proposed_action: str = ""
    params_summary: dict[str, str] = Field(default_factory=dict)
    state: State = "open"
    created_at: float = Field(default_factory=time.time)
    human_actions: list[dict[str, Any]] = Field(default_factory=list)
    resolution: str = ""


# An operator function stands in for a person at the console. The demo supplies one; the
# HTTP console supplies none and the broker waits for a real claim instead.
Operator = Callable[[InterventionRequest, Any, Any], str]


class EscalationBroker:
    def __init__(
        self,
        evidence: EvidenceBus,
        operator: Operator | None = None,
        wait_timeout_s: float = 120.0,
        keep_open: bool = False,
    ) -> None:
        self.evidence = evidence
        self.operator = operator
        self.wait_timeout_s = wait_timeout_s
        self.keep_open = keep_open
        self.queue: list[InterventionRequest] = []
        self._session: Any = None
        self._surface: Any = None
        self._artifact: CapabilityArtifact | None = None
        self._step: Step | None = None
        self._params: dict[str, Any] = {}
        self._recorder: HumanActivityRecorder | None = None

    # --- the hook the replay engine calls -------------------------------------------

    def escalator(self, session: Any, surface: Any) -> Callable[..., str | None]:
        self._session = session
        self._surface = surface

        def hook(
            artifact: CapabilityArtifact, step: Step, expected: str, observed: str
        ) -> str | None:
            return self.raise_intervention(artifact, step, expected, observed)

        return hook

    def raise_intervention(
        self,
        artifact: CapabilityArtifact,
        step: Step,
        expected: str,
        observed: str,
        params: dict[str, Any] | None = None,
    ) -> str | None:
        self._artifact, self._step = artifact, step
        self._params = params or {}
        shot = self.evidence.screenshot(self._session.page, f"escalation-{step.id}")
        request = InterventionRequest(
            capability_id=artifact.capability_id,
            goal=artifact.provenance.goal,
            step_id=step.id,
            why_stopped="automation could not verify the expected state",
            expected=expected,
            observed=observed,
            last_good_checkpoint=_last_good(artifact, step),
            screenshot_ref=str(shot).replace("\\", "/") if shot else "",
            proposed_action=_describe(step),
            params_summary={k: "(redacted)" for k in self._params},
        )
        self.queue.append(request)
        # Nobody holds the lease while the request is open, so Surface.act() now raises.
        self._session.lease.transfer(Holder.NONE)
        self.evidence.log("intervention_raised", **request.model_dump(exclude={"human_actions"}))

        if self.operator is not None:
            self.claim(request.id)
            signal = self.operator(request, self._session, self._surface)
            return self.done(request.id) if signal != "abort" else self.abort(request.id)
        return self._await_operator(request)

    # --- the console's three verbs ----------------------------------------------------

    def open_requests(self) -> list[InterventionRequest]:
        return [r for r in self.queue if r.state in ("open", "claimed")]

    def find(self, request_id: str) -> InterventionRequest | None:
        return next((r for r in self.queue if r.id == request_id), None)

    def claim(self, request_id: str) -> InterventionRequest | None:
        request = self.find(request_id)
        if request is None or request.state not in ("open", "claimed"):
            return None
        request.state = "claimed"
        self._session.lease.transfer(Holder.HUMAN)
        self._recorder = HumanActivityRecorder(self._session, self.evidence)
        self._recorder.start()
        self.evidence.log("lease_claimed", request=request.id, holder="human")
        return request

    def done(self, request_id: str) -> str | None:
        """Handback: re-anchor before acting. Never resume blindly on an unknown screen."""
        request = self.find(request_id)
        if request is None:
            return None
        request.human_actions = self._recorder.stop() if self._recorder else []
        self._session.lease.transfer(Holder.AUTOMATION)
        anchor = self._re_anchor()
        if anchor is None:
            request.state = "open"
            self._session.lease.transfer(Holder.NONE)
            self.evidence.log("re_anchor_failed", request=request.id)
            return None
        request.state = "resolved"
        request.resolution = anchor
        self.evidence.log(
            "intervention_resolved",
            request=request.id,
            resolution=anchor,
            human_actions=request.human_actions,
        )
        return anchor

    def abort(self, request_id: str) -> str | None:
        request = self.find(request_id)
        if request is None:
            return None
        request.human_actions = self._recorder.stop() if self._recorder else []
        request.state = "aborted"
        self._session.lease.transfer(Holder.AUTOMATION)
        self.evidence.log("intervention_aborted", request=request.id)
        return request.id

    # --- internals ---------------------------------------------------------------------

    def _await_operator(self, request: InterventionRequest) -> str | None:
        deadline = time.monotonic() + self.wait_timeout_s
        while time.monotonic() < deadline:
            if request.state == "resolved":
                return request.resolution
            if request.state == "aborted":
                return request.id
            time.sleep(0.25)
        request.state = "abandoned"
        self._session.lease.transfer(Holder.AUTOMATION)
        self.evidence.log("intervention_abandoned", request=request.id)
        return request.id

    def _re_anchor(self) -> str | None:
        """Two questions only: is the step done, or are we back where it starts?"""
        if self._artifact is None or self._step is None:
            return None
        steps = self._artifact.steps
        index = next((i for i, s in enumerate(steps) if s.id == self._step.id), 0)
        if self._step.expected_state and evaluate(
            self._surface, self._step.expected_state, self._params, self._step.target
        ):
            return RESUME
        previous = steps[index - 1] if index else None
        if previous and previous.expected_state and evaluate(
            self._surface, previous.expected_state, self._params, previous.target
        ):
            return RETRY
        return None


def _last_good(artifact: CapabilityArtifact, step: Step) -> str:
    index = next((i for i, s in enumerate(artifact.steps) if s.id == step.id), 0)
    for earlier in reversed(artifact.steps[:index]):
        if earlier.expected_state:
            return f"{earlier.id}: {earlier.expected_state.kind} '{earlier.expected_state.matcher}'"
    return "none reached"


def _describe(step: Step) -> str:
    if step.target is None:
        return f"{step.action} {step.value}"
    return f"{step.action} on {step.target.role} '{step.target.accessible_name}'"
