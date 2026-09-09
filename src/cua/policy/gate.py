"""The policy gate. It sits inside ``Surface.act()``, below the model, and denies by default.

Putting it here rather than in the orchestrator or a prompt is the whole point: a
jailbroken model, or a caller that skipped every layer above, still cannot act
off-allowlist. ``test_calling_act_off_allowlist_on_a_bare_surface_still_raises`` is the
only way to prove that from outside, so it exists.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .policy import Policy, Reversibility, stricter
from .secrets import SecretResolver

Verdict = Literal["allow", "deny", "confirm_required"]


class Decision(BaseModel):
    verdict: Verdict
    reason: str
    reversibility: Reversibility = "safe"


class PolicyDenied(Exception):
    """The action is outside the allowlist. Nothing was done."""


class ConfirmationRequired(Exception):
    """The action is irreversible. It escalates to a human — a different outcome to denial."""

    def __init__(self, reason: str, decision: Decision | None = None) -> None:
        self.decision = decision
        super().__init__(reason)


class PolicyGate:
    def __init__(self, policy: Policy, secrets: SecretResolver | None = None) -> None:
        self.policy = policy
        self.secrets = secrets or SecretResolver()

    def authorize(
        self,
        kind: str,
        url: str = "",
        label: str = "",
        href: str = "",
        declared: Reversibility = "safe",
    ) -> Decision:
        if kind not in self.policy.allowed_actions:
            return Decision(verdict="deny", reason=f"action type '{kind}' is not allowlisted")

        for candidate in [u for u in (url, href) if u and u.startswith("http")]:
            if not self.policy.domain_allowed(candidate):
                return Decision(verdict="deny", reason=f"domain not allowlisted: {candidate}")
            if not self.policy.route_allowed(candidate):
                return Decision(verdict="deny", reason=f"route not allowlisted: {candidate}")

        # The stricter of what policy computes and what the artifact claims. An artifact
        # may tighten a step; it can never downgrade one.
        effective = stricter(self.policy.classify(label, href or url), declared)
        if effective == "irreversible" and self.policy.confirm_mode == "block_irreversible":
            return Decision(
                verdict="confirm_required",
                reason=f"irreversible action requires human confirmation: {label or url}",
                reversibility=effective,
            )
        return Decision(
            verdict="allow",
            reason="risky action permitted and flagged" if effective == "risky" else "allowed",
            reversibility=effective,
        )

    def resolve(self, value: str) -> str:
        """Secrets are resolved here, at act-time, and nowhere earlier."""
        return self.secrets.resolve(value)
