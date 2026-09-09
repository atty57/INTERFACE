"""The one-way door: verified actions in, a reviewable capability out.

The Recorder emits only from actions that reached a verified checkpoint, and it never
persists the model transcript as the artifact. The transcript is how we found out; the
artifact is what we know.
"""

from __future__ import annotations

import datetime as dt
import re

from ..artifact.models import (
    CapabilityArtifact,
    Checkpoint,
    KnownBusinessOutcome,
    OnErrorPolicy,
    Provenance,
    RecoverableSignature,
    Safety,
    Step,
)
from ..policy.policy import Policy
from ..surface.base import Action
from .generalize import generalize
from .trace import RecordedAction

# A happy-path run cannot discover the screens it never saw, so the recorder seeds the
# vendor's known conditions and the pull-request diff is where a human confirms them.
# Inventing them at replay time would be improvisation; leaving them out would make every
# "no such member" a false alarm.
SEEDED_OUTCOMES = [
    KnownBusinessOutcome(
        name="record_not_found",
        detector=Checkpoint(kind="text", matcher="No member found"),
        severity="info",
        message="No member exists with that Member ID.",
    ),
    KnownBusinessOutcome(
        name="permission_denied",
        detector=Checkpoint(kind="text", matcher="not authorized"),
        severity="warn",
        message="The operator is not authorized to view this record.",
    ),
    KnownBusinessOutcome(
        name="invalid_member_id",
        detector=Checkpoint(kind="text", matcher="Enter a valid Member ID"),
        severity="warn",
        message="The Member ID was rejected by the application's own validation.",
    ),
    KnownBusinessOutcome(
        name="invalid_credentials",
        detector=Checkpoint(kind="text", matcher="Sign on failed"),
        severity="error",
        message="The operator credential was rejected.",
    ),
]

SEEDED_RECOVERIES = [
    RecoverableSignature(
        name="session_expired",
        detector=Checkpoint(kind="text", matcher="Session has expired"),
        strategy="re_login",
        max_attempts=1,
    ),
    RecoverableSignature(
        name="interstitial_dialog",
        detector=Checkpoint(kind="element_state", matcher="role=dialog visible"),
        strategy="dismiss_known_dialog",
        max_attempts=2,
    ),
]

HEADING = re.compile(r"^[A-Z][A-Za-z0-9 /'&-]{3,39}$")


def probe_checkpoint(
    action: Action,
    after_text: str,
    before_text: str,
    url_before: str,
    url_after: str,
) -> Checkpoint | None:
    """The state we expect after this action, derived from what the screen actually did.

    ponytail: a heuristic — first new heading-shaped line, else the URL. Its ceiling is a
    screen whose only change is a value rather than a heading; the upgrade path is asking
    the model to name the checkpoint alongside its action.
    """
    if action.kind == "type":
        if action.value.startswith("${secret:"):
            return Checkpoint(kind="element_state", matcher="value.length>0")
        return Checkpoint(kind="element_state", matcher=f"value=={action.value}")

    before = {line.strip() for line in before_text.splitlines()}
    for line in (line.strip() for line in after_text.splitlines()):
        if line and line not in before and HEADING.match(line):
            return Checkpoint(kind="text", matcher=line)
    if url_after and url_after != url_before:
        return Checkpoint(kind="url", matcher=url_after)
    return None


class Recorder:
    """Verified action -> multi-signal descriptor + checkpoint, then the generalization pass."""

    def __init__(self) -> None:
        self.trace: list[RecordedAction] = []

    def record(
        self,
        action: Action,
        label: str,
        checkpoint: Checkpoint | None,
        url_before: str = "",
        url_after: str = "",
        tier: int | None = None,
    ) -> bool:
        """Only a verified action is recorded. An unverified one is a dead end, by design."""
        if checkpoint is None:
            return False
        self.trace.append(
            RecordedAction(
                action=action,
                label=label,
                checkpoint=checkpoint,
                url_before=url_before,
                url_after=url_after,
                tier=tier,
            )
        )
        return True

    def finalize(
        self,
        *,
        goal: str,
        capability_id: str,
        version: str,
        base_url: str,
        final_values: dict[str, str],
        policy: Policy,
        provenance: Provenance,
        vendor_product: str = "acme-core",
        vendor_version_range: str = ">=9.0 <10",
    ) -> CapabilityArtifact:
        result = generalize(self.trace, goal, final_values, base_url)
        steps = [
            _to_step(f"s{index}", recorded)
            for index, recorded in enumerate(result.trace)
        ]
        _attach_error_policies(steps)
        success = next(
            (s.expected_state for s in reversed(steps) if s.expected_state), None
        ) or Checkpoint(kind="text", matcher="")
        provenance = provenance.model_copy(
            update={"goal": goal, "recorded_at": dt.datetime.now(dt.UTC).isoformat()}
        )
        return CapabilityArtifact(
            capability_id=capability_id,
            version=version,
            base_url=base_url,
            vendor_product=vendor_product,
            vendor_version_range=vendor_version_range,
            approval_state="draft",
            requires_secrets=result.requires_secrets,
            input_params=result.input_params,
            outputs=result.outputs,
            steps=steps,
            known_business_outcomes=SEEDED_OUTCOMES,
            recoverable_signatures=SEEDED_RECOVERIES,
            success_checkpoint=success.model_copy(update={"timeout_ms": 10000}),
            safety=Safety(
                allowlisted_domains=list(policy.allowlisted_domains),
                allowlisted_routes=list(policy.allowlisted_routes),
                allowed_actions=["navigate", "click", "type", "extract", "assert", "wait"],
                risky_actions=list(policy.irreversible_patterns),
                confirm_mode="block_irreversible",
            ),
            provenance=provenance,
        )


def _to_step(step_id: str, recorded: RecordedAction) -> Step:
    return Step(
        id=step_id,
        action=recorded.action.kind,
        target=recorded.action.target,
        value=recorded.action.value or recorded.action.url,
        expected_state=recorded.checkpoint,
        reversibility=recorded.action.reversibility,
    )


def _attach_error_policies(steps: list[Step]) -> None:
    """Every state-changing step races the recoverable signatures; the ones that submit a
    form also race the business outcomes the vendor is known to produce."""
    for step in steps:
        if step.action not in ("click", "navigate"):
            continue
        step.on_error = OnErrorPolicy(
            business_outcomes=[o.name for o in SEEDED_OUTCOMES],
            recover=["dismiss_known_dialog", "retry_backoff", "re_login"],
            otherwise="escalate",
        )
