"""The surface seam: perceive and act on one kind of surface, and nothing else.

Everything here is stated in accessibility-tree vocabulary — role, accessible name,
anchor text, frame path — because that vocabulary exists on Windows UIA, macOS AX and
AT-SPI as well as in a browser. The artifact is written in these terms, which is what
makes it surface-agnostic. Nothing in this module knows about artifacts, runs, or models.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ..policy.policy import Reversibility

ActionKind = Literal["navigate", "click", "type", "select", "extract", "assert", "wait"]
AnchorRelation = Literal["label_for", "same_row", "same_cell", "following"]


class Anchor(BaseModel):
    """A control identified by its relationship to nearby text that does not move."""

    stable_text: str
    relation: AnchorRelation = "label_for"


class LocatorDescriptor(BaseModel):
    """Several independent signals for one control, strongest first.

    Replay tries them in order and requires exactly one match; see the ladder in
    ``surface/web.py``. Recording several is the point — replay survives a control being
    reached a different way, and the tier that resolved is a drift signal.
    """

    role: str | None = None
    accessible_name: str | None = None
    placeholder: str | None = None
    visible_text: str | None = None
    anchor: Anchor | None = None
    frame_path: list[str] = Field(default_factory=list)
    structural: str | None = None


class DigestEntry(BaseModel):
    """One interactive control, as the model and the recorder both see it."""

    index: int
    ordinal: int = -1  # position within its frame's control list; how we re-find a handle
    role: str
    accessible_name: str = ""
    near: str = ""
    frame_path: list[str] = Field(default_factory=list)
    placeholder: str = ""
    visible_text: str = ""
    structural: str = ""
    value: str = ""

    def render(self) -> str:
        bits = [f"[{self.index}]", f"{self.role:<8}", f'"{self.accessible_name}"']
        if self.frame_path:
            bits.append(f"frame={'>'.join(self.frame_path)}")
        if self.near:
            bits.append(f'near="{self.near}"')
        return "  ".join(bits)

    def to_descriptor(self) -> LocatorDescriptor:
        return LocatorDescriptor(
            role=self.role,
            accessible_name=self.accessible_name or None,
            placeholder=self.placeholder or None,
            visible_text=self.visible_text or None,
            anchor=Anchor(stable_text=self.near, relation="label_for") if self.near else None,
            frame_path=list(self.frame_path),
            structural=self.structural or None,
        )


class ElementDigest(BaseModel):
    """A numbered digest of the controls a person could interact with."""

    url: str
    entries: list[DigestEntry] = Field(default_factory=list)

    def render(self) -> str:
        return "\n".join(e.render() for e in self.entries) or "(no interactive controls)"

    def fingerprint(self) -> str:
        """Stable hash of the digest, for no-progress detection during discovery."""
        payload = json.dumps(
            [[e.role, e.accessible_name, e.near, e.frame_path] for e in self.entries],
            sort_keys=True,
        )
        return hashlib.sha256(f"{self.url}|{payload}".encode()).hexdigest()[:16]


class Action(BaseModel):
    """One thing to do to the surface.

    Discovery targets by digest index; replay targets by descriptor. Both go through the
    same ``act()``, so both go through the same policy gate.
    """

    kind: ActionKind
    index: int | None = None
    target: LocatorDescriptor | None = None
    value: str = ""
    url: str = ""
    reversibility: Reversibility = "safe"  # the artifact's claim; policy may overrule it


class Effect(BaseModel):
    """What happened. Deliberately thin: the checkpoint race is what decides outcomes."""

    action: Action
    tier: int | None = None
    extracted: str | None = None
    url: str = ""


class Located(BaseModel):
    """A resolved control plus the tier that resolved it. ``handle`` is surface-native."""

    model_config = {"arbitrary_types_allowed": True}

    tier: int
    handle: Any


@runtime_checkable
class Surface(Protocol):
    """Three operations. Adding a surface means implementing these and nothing else."""

    def snapshot(self) -> ElementDigest: ...

    def locate(self, desc: LocatorDescriptor) -> Located: ...

    def act(self, action: Action) -> Effect: ...


class LocatorUnresolved(Exception):
    """No tier resolved to exactly one control. Carries what every tier saw."""

    def __init__(self, desc: LocatorDescriptor, attempts: list[str]) -> None:
        self.desc = desc
        self.attempts = attempts
        super().__init__("; ".join(attempts) or "no signals to try")


class LocatorAmbiguous(LocatorUnresolved):
    """A tier matched more than one control. Never resolved by trying a weaker signal."""


class SurfaceError(Exception):
    """The surface itself failed — navigation error, frame detached, browser gone."""
