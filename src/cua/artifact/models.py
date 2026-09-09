"""The capability artifact: what we know, as opposed to how we found out.

The transcript of a discovery run holds dead ends, retries, and untrusted page text, none
of which should be executable, reviewable, or diffable. This is the one-way door's output:
a typed, versioned description of a flow that a human can review in a pull request and an
agent can call by name.

Nothing here mentions Playwright, CSS, or a browser. Every targeting signal is stated in
accessibility-tree vocabulary, which exists on Windows UIA, macOS AX and AT-SPI too.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..policy.policy import Reversibility
from ..surface.base import ActionKind, LocatorDescriptor

SCHEMA_VERSION = "1.0.0"

DEFAULT_ALLOWED_ACTIONS: list[ActionKind] = [
    "navigate",
    "click",
    "type",
    "extract",
    "assert",
    "wait",
]

Sensitivity = Literal["public", "pii"]
Severity = Literal["info", "warn", "error"]
ApprovalState = Literal["draft", "approved"]
RecoveryStrategy = Literal["dismiss_known_dialog", "retry_backoff", "re_login"]


class Checkpoint(BaseModel):
    """Three kinds only.

    ``aria_snapshot`` was considered and dropped: it asserts a whole subtree when you mean
    one heading, so cosmetic change breaks it. Over-precision reads as fragility.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "element_state", "url"]
    matcher: str
    timeout_ms: int = 8000


class InputParam(BaseModel):
    """A business input the calling agent supplies. Credentials are never parameters."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["string", "integer", "number", "boolean", "money"] = "string"
    pattern: str | None = None
    required: bool = True
    sensitivity: Sensitivity = "public"
    description: str = ""


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["string", "integer", "number", "boolean", "money"] = "string"
    locator: LocatorDescriptor
    transform: Literal["none", "parse_currency", "strip"] = "none"


class OnErrorPolicy(BaseModel):
    """What this step does when the detector race does not land on its checkpoint."""

    model_config = ConfigDict(extra="forbid")

    business_outcomes: list[str] = Field(default_factory=list)
    recover: list[RecoveryStrategy] = Field(default_factory=list)
    otherwise: Literal["hard_fail", "escalate"] = "hard_fail"


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    action: ActionKind
    target: LocatorDescriptor | None = None
    value: str = ""
    expected_state: Checkpoint | None = None
    reversibility: Reversibility = "safe"
    recorded_tier: int | None = None  # the tier this descriptor resolved at when recorded
    on_error: OnErrorPolicy = Field(default_factory=OnErrorPolicy)


class KnownBusinessOutcome(BaseModel):
    """An answer the caller must act on, not a fault.

    The rule the classifier encodes: if a competent human operator would report it to the
    customer, it is a business outcome; if they would file a ticket, it is a failure.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    detector: Checkpoint
    severity: Severity = "info"
    message: str = ""
    outputs: dict[str, str] = Field(default_factory=dict)


class RecoverableSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    detector: Checkpoint
    strategy: RecoveryStrategy
    max_attempts: int = 1


class Safety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowlisted_domains: list[str] = Field(default_factory=list)
    allowlisted_routes: list[str] = Field(default_factory=lambda: ["/**"])
    allowed_actions: list[ActionKind] = Field(
        default_factory=lambda: DEFAULT_ALLOWED_ACTIONS.copy()
    )
    risky_actions: list[str] = Field(default_factory=list)
    confirm_mode: Literal["block_irreversible", "allow_all"] = "block_irreversible"


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recorded_by: str = ""
    run_id: str = ""
    trace_ref: str = ""
    recorded_at: str = ""
    goal: str = ""


class CapabilityArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    capability_id: str
    version: str
    base_url: str
    vendor_product: str = ""
    vendor_version_range: str = ""
    approval_state: ApprovalState = "draft"

    requires_secrets: list[str] = Field(default_factory=list)
    input_params: list[InputParam] = Field(default_factory=list)
    outputs: list[Output] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    known_business_outcomes: list[KnownBusinessOutcome] = Field(default_factory=list)
    recoverable_signatures: list[RecoverableSignature] = Field(default_factory=list)
    success_checkpoint: Checkpoint
    safety: Safety = Field(default_factory=Safety)
    provenance: Provenance = Field(default_factory=Provenance)

    @field_validator("steps")
    @classmethod
    def _steps_have_unique_ids(cls, steps: list[Step]) -> list[Step]:
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        return steps

    def param(self, name: str) -> InputParam | None:
        return next((p for p in self.input_params if p.name == name), None)

    def outcome(self, name: str) -> KnownBusinessOutcome | None:
        return next((o for o in self.known_business_outcomes if o.name == name), None)

    def pii_params(self) -> list[str]:
        return [p.name for p in self.input_params if p.sensitivity == "pii"]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2, by_alias=True, exclude_none=True)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CapabilityArtifact:
        return cls.model_validate(payload)
