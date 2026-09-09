"""The production execution path. No model is reachable from this module — that is I1.

``test_no_model_client_is_reachable_from_the_replay_import_graph`` in
``tests/test_replay.py`` walks this module's import graph and fails if a model
client appears anywhere beneath it, which turns "no LLM on the production path" from a
claim into something continuous integration enforces.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from ..artifact.models import CapabilityArtifact, Checkpoint, Step
from ..evidence.bus import EvidenceBus
from ..policy.gate import ConfirmationRequired, PolicyDenied
from ..surface.base import Action, LocatorAmbiguous, LocatorUnresolved, SurfaceError
from ..surface.web import WebSurface
from .detectors import Candidate, RaceOutcome, bind, describe_step, race, summarize
from .outcomes import BusinessOutcome, Failure, RunResult, Success
from .recovery import BACKOFF_SECONDS, DISMISS_CONTROL, REPEAT_ACTION_AFTER, RecoveryLedger

# What an escalation hands back: the step is done, repeat it, or the run is over.
RESUME = "resume"
RETRY = "retry"
ABORTED = "aborted"
ABANDONED = "abandoned"

MAX_ROUNDS_PER_STEP = 8

# An escalation hook returns "resume" to try the step again, or None to give up. The
# escalation broker supplies one; without it a hard failure is simply reported.
Escalator = Callable[[CapabilityArtifact, Step, str, str], str | None]


class ReplayEngine:
    def __init__(
        self,
        artifact: CapabilityArtifact,
        surface: WebSurface,
        evidence: EvidenceBus,
        params: dict[str, Any],
        run_id: str = "",
        base_url: str | None = None,
        escalator: Escalator | None = None,
    ) -> None:
        self.artifact = artifact
        self.surface = surface
        self.evidence = evidence
        self.params = params
        self.run_id = run_id or evidence.run_id
        self.base_url = (base_url or artifact.base_url).rstrip("/")
        self.escalator = escalator
        self.ledger = RecoveryLedger()
        self.tiers: dict[str, int] = {}

    # --- the run ------------------------------------------------------------------

    def run(self) -> RunResult:
        started = time.monotonic()
        for name in self.artifact.pii_params():
            self.evidence.redactor.add(str(self.params.get(name, "")))
        self.evidence.log("replay_started", capability=self.artifact.capability_id)

        for index, step in enumerate(self.artifact.steps):
            result = self._execute_step(index)
            if result is not None:
                self.evidence.screenshot(self.surface.page, f"{step.id}-stopped")
                self.evidence.log("replay_finished", result=result.model_dump())
                return result

        outputs = self._extract_outputs()
        duration = int((time.monotonic() - started) * 1000)
        success = Success(
            outputs=outputs,
            run_id=self.run_id,
            evidence_ref=self.evidence.ref,
            duration_ms=duration,
            tiers_used=self.tiers,
        )
        self.evidence.screenshot(self.surface.page, "success")
        self.evidence.log("replay_finished", result=success.model_dump())
        return success

    # --- one step -----------------------------------------------------------------

    def _execute_step(self, index: int) -> RunResult | None:
        """``None`` means proceed. Anything else ends the run."""
        step = self.artifact.steps[index]
        perform = True
        for _ in range(MAX_ROUNDS_PER_STEP):
            if perform:
                failure = self._act(step)
                if failure is not None:
                    return failure
            outcome = self._classify(step, index)

            if outcome.kind in ("ambiguous", "timeout"):
                failure_class = (
                    "ambiguous_state" if outcome.kind == "ambiguous" else "checkpoint_missed"
                )
                ending = self._escalate(step, failure_class, index, outcome)
                if isinstance(ending, str):  # an operator resolved it and re-anchoring passed
                    perform = ending == RETRY
                    continue
                return ending
            if outcome.role == "checkpoint":
                return None
            if outcome.role == "business":
                return self._business(step, outcome.name)

            recovered = self._recover(step, index, outcome.name)
            if recovered is None:
                ending = self._escalate(step, "checkpoint_missed", index, outcome)
                if isinstance(ending, str):
                    perform = ending == RETRY
                    continue
                return ending
            perform = recovered
        return self._fail(
            "checkpoint_missed", step, self._expected(step, index), "recovery rounds exhausted"
        )

    def _act(self, step: Step) -> RunResult | None:
        action = self._action_for(step)
        if action is None:
            return None
        try:
            effect = self.surface.act(action)
        except PolicyDenied as denied:
            self.evidence.log("policy_denied", step=step.id, reason=str(denied))
            return self._fail("policy_denied", step, "an allowlisted action", str(denied))
        except ConfirmationRequired as confirm:
            return self._escalate_irreversible(step, str(confirm))
        except LocatorAmbiguous as ambiguous:
            return self._fail("ambiguous_state", step, describe_step(step), str(ambiguous))
        except LocatorUnresolved as unresolved:
            # One re-snapshot and backoff, then the ladder once more — no third chance.
            self.evidence.log("locator_retry", step=step.id, attempts=unresolved.attempts)
            time.sleep(BACKOFF_SECONDS[0])
            try:
                effect = self.surface.act(action)
            except LocatorAmbiguous as ambiguous:
                return self._fail("ambiguous_state", step, describe_step(step), str(ambiguous))
            except (LocatorUnresolved, SurfaceError) as final:
                return self._fail("locator_unresolved", step, describe_step(step), str(final))
        except SurfaceError as broken:
            return self._fail("surface_error", step, describe_step(step), str(broken))
        if effect.tier is not None:
            self.tiers[step.id] = effect.tier
            self._warn_on_tier_drift(step, effect.tier)
        return None

    def _fail(self, failure_class: Any, step: Step, expected: str, observed: str) -> Failure:
        return Failure(
            failure_class=failure_class,
            step_id=step.id,
            expected=expected,
            observed=observed,
            run_id=self.run_id,
            evidence_ref=self.evidence.ref,
            tiers_used=self.tiers,
        )

    def _action_for(self, step: Step) -> Action | None:
        if step.action in ("assert", "wait"):
            return None
        value = bind(step.value, self.params)
        if step.action == "navigate":
            return Action(kind="navigate", url=urljoin(self.base_url + "/", value.lstrip("/")))
        return Action(
            kind=step.action,
            target=step.target,
            value=value,
            reversibility=step.reversibility,
        )

    def _warn_on_tier_drift(self, step: Step, tier: int) -> None:
        """Resolving below the recorded tier warns and logs. It never fails a run."""
        if step.recorded_tier is not None and tier > step.recorded_tier:
            self.evidence.log(
                "tier_drift", step=step.id, recorded=step.recorded_tier, resolved=tier
            )

    # --- classification -------------------------------------------------------------

    def _classify(self, step: Step, index: int) -> RaceOutcome:
        checkpoint = self._expected_checkpoint(step, index)
        candidates = [Candidate(name=step.id, role="checkpoint", checkpoint=checkpoint)]
        for name in step.on_error.business_outcomes:
            outcome = self.artifact.outcome(name)
            if outcome is not None:
                candidates.append(
                    Candidate(name=name, role="business", checkpoint=outcome.detector)
                )
        for signature in self.artifact.recoverable_signatures:
            candidates.append(
                Candidate(name=signature.name, role="recoverable", checkpoint=signature.detector)
            )
        result = race(
            self.surface, candidates, checkpoint.timeout_ms, self.params, step.target
        )
        self.evidence.log(
            "classified", step=step.id, kind=result.kind, matched=result.matched
        )
        return result

    def _expected_checkpoint(self, step: Step, index: int) -> Checkpoint:
        if index == len(self.artifact.steps) - 1:
            return self.artifact.success_checkpoint
        return step.expected_state or Checkpoint(kind="url", matcher=self.surface.page.url)

    def _expected(self, step: Step, index: int) -> str:
        checkpoint = self._expected_checkpoint(step, index)
        return f"{checkpoint.kind} '{bind(checkpoint.matcher, self.params)}'"

    # --- recovery -------------------------------------------------------------------

    def _recover(self, step: Step, index: int, signature_name: str) -> bool | None:
        """Returns whether to repeat the action, or ``None`` when recovery is exhausted."""
        signature = next(
            (s for s in self.artifact.recoverable_signatures if s.name == signature_name), None
        )
        if signature is None or signature.strategy not in step.on_error.recover:
            self.evidence.log(
                "recovery_refused", step=step.id, signature=signature_name,
                reason="strategy is not declared by this capability",
            )
            return None
        if not self.ledger.take(step.id, signature.name, signature.max_attempts):
            self.evidence.log(
                "recovery_exhausted",
                step=step.id,
                signature=signature.name,
                cap=signature.max_attempts,
            )
            return None
        attempt = self.ledger.used(step.id, signature.name)
        self.evidence.log(
            "recovery_attempt", step=step.id, strategy=signature.strategy, attempt=attempt
        )
        if signature.strategy == "dismiss_known_dialog":
            self._dismiss_dialog()
        elif signature.strategy == "retry_backoff":
            time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
        elif signature.strategy == "re_login":
            self._re_login(index)
        return signature.strategy in REPEAT_ACTION_AFTER

    def _dismiss_dialog(self) -> None:
        for _, frame in self.surface.frames():
            try:
                name = frame.evaluate(DISMISS_CONTROL)
            except Exception:  # noqa: BLE001 - detached frame
                continue
            if not name:
                continue
            from ..surface.base import LocatorDescriptor

            self.surface.act(
                Action(kind="click", target=LocatorDescriptor(visible_text=str(name)))
            )
            self.surface.page.wait_for_timeout(300)
            return

    def _re_login(self, index: int) -> None:
        """Re-run the flow up to this step. Re-authenticating alone would lose the context
        the failing step depends on, so the prefix is replayed — from the artifact, in order."""
        for earlier in self.artifact.steps[:index]:
            self._act(earlier)
            self.surface.page.wait_for_timeout(200)

    # --- endings ---------------------------------------------------------------------

    def _business(self, step: Step, name: str) -> BusinessOutcome:
        declared = self.artifact.outcome(name)
        return BusinessOutcome(
            name=name,
            severity=declared.severity if declared else "info",
            message=declared.message if declared else "",
            partial_outputs=self._extract_outputs(),
            run_id=self.run_id,
            evidence_ref=self.evidence.ref,
            step_id=step.id,
        )

    def _escalate(
        self, step: Step, failure_class: Any, index: int, outcome: RaceOutcome | None
    ) -> RunResult | str:
        """A string means an operator took the wheel, resolved it, and handed control back."""
        expected = self._expected(step, index)
        observed = outcome.observed if outcome else ""
        if outcome and outcome.kind == "ambiguous":
            observed = f"matched {outcome.matched} at once: {outcome.observed}"
        elif outcome and outcome.role == "recoverable":
            observed = f"'{outcome.name}' kept recurring; recovery exhausted"
        if not observed:
            observed = summarize(self.surface.page_text())
        self.evidence.screenshot(self.surface.page, f"{step.id}-{failure_class}")
        escalation_id = None
        if self.escalator is not None and step.on_error.otherwise == "escalate":
            resolution = self.escalator(self.artifact, step, expected, observed)
            if resolution in (RESUME, RETRY):
                self.evidence.log("resumed_after_handoff", step=step.id, resolution=resolution)
                return resolution
            if resolution and ":" in resolution:
                verdict, escalation_id = resolution.split(":", 1)
                failure_class = {
                    ABORTED: "operator_aborted",
                    ABANDONED: "escalation_timeout",
                }.get(verdict, failure_class)
            else:
                escalation_id = resolution
        failure = self._fail(failure_class, step, expected, observed)
        return failure.model_copy(update={"escalation_id": escalation_id})

    def _escalate_irreversible(self, step: Step, reason: str) -> RunResult:
        """Escalating and denying are different code paths, because they mean different things."""
        self.evidence.log("escalation_required", step=step.id, reason=reason)
        escalation_id = None
        if self.escalator is not None:
            escalation_id = self.escalator(self.artifact, step, "human confirmation", reason)
        failure = self._fail(
            "escalation_required", step, "human confirmation of an irreversible action", reason
        )
        return failure.model_copy(update={"escalation_id": escalation_id})

    # --- outputs ---------------------------------------------------------------------

    def _extract_outputs(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for output in self.artifact.outputs:
            raw = self.surface.extract(output.locator)
            if raw is None:
                continue
            values[output.name] = _transform(raw, output.transform)
        return values


def _transform(raw: str, transform: str) -> object:
    if transform == "parse_currency":
        cleaned = raw.strip().lstrip("$£€").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return raw.strip()
    if transform == "strip":
        return raw.strip()
    return raw
