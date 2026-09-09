"""A deterministic stand-in for the model, for reproducing a recording without an API key.

It is NOT a test double and no test asserts through it. It exists for one reason: the
committed artifact is what makes key-free replay reproducible on a clean clone, so the
record path has to be runnable by a reviewer who has no credential. It drives the *same*
loop, the same policy gate, the same Recorder, the same generalization pass and the same
evidence bus as ``ClaudePlanner`` — only the decision comes from a rule instead of a model,
and ``provenance.recorded_by`` says so.

``cua record`` uses Claude Opus 5 by default; this is behind ``--planner scripted``.
"""

from __future__ import annotations

import re

from ..surface.base import ElementDigest
from .loop import ToolCall

USERNAME_REF = "${secret:core_operator.username}"
PASSWORD_REF = "${secret:core_operator.password}"


class ScriptedPlanner:
    """Sign on, search the identifier named in the goal, stop on the detail screen."""

    def __init__(self, goal: str) -> None:
        self.goal = goal
        found = re.search(r"\b(\d{4,12})\b", goal)
        self.identifier = found.group(1) if found else "12345"

    @property
    def tokens(self) -> int:
        return 0

    def decide(
        self, observation: str, digest: ElementDigest, screenshot: bytes | None = None
    ) -> ToolCall:
        def empty(name: str) -> int | None:
            return next(
                (e.index for e in digest.entries if e.accessible_name == name and not e.value),
                None,
            )

        def control(name: str) -> int | None:
            return next((e.index for e in digest.entries if e.accessible_name == name), None)

        index = empty("User ID")
        if index is not None:
            return ToolCall(name="type", args={"index": index, "value": USERNAME_REF})
        index = empty("Password")
        if index is not None:
            return ToolCall(name="type", args={"index": index, "value": PASSWORD_REF})
        index = control("Sign On")
        if index is not None:
            return ToolCall(name="click", args={"index": index})
        if control("Close Account") is not None:
            return ToolCall(name="done", args={"summary": "the member detail screen is showing"})
        index = empty("Member ID")
        if index is not None:
            return ToolCall(name="type", args={"index": index, "value": self.identifier})
        index = control("Search")
        if index is not None:
            return ToolCall(name="click", args={"index": index})
        return ToolCall(name="stuck", args={"reason": "no sign-on, search, or detail control here"})
