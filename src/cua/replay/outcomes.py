"""The result contract: one union, three arms.

Collapsing arm two into arm three is the most expensive mistake available in this design.
"No such member" is an *answer* the calling agent must act on — it comes back as data with
a severity, not as an exception. The rule the classifier encodes: if a competent human
operator would report it to the customer, it is a BusinessOutcome; if they would file a
ticket, it is a Failure.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FailureClass = Literal[
    "capability_unavailable",
    "invalid_input",
    "policy_denied",
    "locator_unresolved",
    "checkpoint_missed",
    "ambiguous_state",
    "session_lost",
    "surface_error",
    "operator_aborted",
    "escalation_timeout",
]


class Success(BaseModel):
    kind: Literal["success"] = "success"
    outputs: dict[str, object] = Field(default_factory=dict)
    run_id: str = ""
    evidence_ref: str = ""
    duration_ms: int = 0
    tiers_used: dict[str, int] = Field(default_factory=dict)


class BusinessOutcome(BaseModel):
    kind: Literal["business_outcome"] = "business_outcome"
    name: str
    severity: Literal["info", "warn", "error"] = "info"
    message: str = ""
    partial_outputs: dict[str, object] = Field(default_factory=dict)
    run_id: str = ""
    evidence_ref: str = ""
    step_id: str = ""


class Failure(BaseModel):
    kind: Literal["failure"] = "failure"
    failure_class: FailureClass
    step_id: str = ""
    expected: str = ""
    observed: str = ""
    run_id: str = ""
    evidence_ref: str = ""
    escalation_id: str | None = None
    tiers_used: dict[str, int] = Field(default_factory=dict)


RunResult = Success | BusinessOutcome | Failure
