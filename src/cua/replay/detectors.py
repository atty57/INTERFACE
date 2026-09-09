"""Classification: race all detectors under one timeout, and fail closed.

The naive design checks the success checkpoint, then the business outcomes, in sequence.
That is wrong: on a still-loading page neither matches yet, and you misclassify a slow load
as a failure. The correct primitive is a single timed race in which every detector is
evaluated on every pass.
"""

from __future__ import annotations

import re
import time
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from ..artifact.models import Checkpoint, Step
from ..surface.base import LocatorDescriptor
from ..surface.web import WebPerception

Role = Literal["checkpoint", "business", "recoverable"]

PARAM = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
COLON_PARAM = re.compile(r"(?<=/):([A-Za-z0-9_]+)")

DIALOG_VISIBLE = """
() => {
  const el = document.querySelector('[role="dialog"]');
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return !!(r.width && r.height);
}
"""


class Candidate(BaseModel):
    name: str
    role: Role
    checkpoint: Checkpoint


class RaceOutcome(BaseModel):
    kind: Literal["match", "ambiguous", "timeout"]
    matched: list[str] = []
    role: Role | None = None
    name: str = ""
    observed: str = ""


def bind(text: str, params: dict[str, object]) -> str:
    """``${member_id}`` and ``/member/:member_id`` both resolve from the same values."""
    text = PARAM.sub(lambda m: str(params.get(m.group(1), m.group(0))), text)
    return COLON_PARAM.sub(lambda m: str(params.get(m.group(1), m.group(0))), text)


def evaluate(
    surface: WebPerception,
    checkpoint: Checkpoint,
    params: dict[str, object],
    target: LocatorDescriptor | None = None,
    text: str | None = None,
) -> bool:
    """``text`` lets one race pass read the screen once and judge every detector on it."""
    matcher = bind(checkpoint.matcher, params)
    if not matcher:
        return False
    if checkpoint.kind == "text":
        screen = surface.page_text() if text is None else text
        return matcher.casefold() in screen.casefold()
    if checkpoint.kind == "url":
        wanted = urlparse(matcher)
        current = urlparse(surface.page.url)
        return current.path == (wanted.path or current.path) and (
            wanted.query in current.query if wanted.query else True
        )
    return _element_state(surface, matcher, target)


def _element_state(
    surface: WebPerception, matcher: str, target: LocatorDescriptor | None
) -> bool:
    """Three forms, and no expression language. Anything else would need a parser."""
    if matcher == "role=dialog visible":
        for _, frame in surface.frames():
            try:
                if frame.evaluate(DIALOG_VISIBLE):
                    return True
            except Exception:  # noqa: BLE001 - detached frame
                continue
        return False
    if target is None:
        return False
    try:
        handle = surface.locate(target).handle
        value = handle.evaluate("el => (el.value === undefined ? '' : String(el.value))")
    except Exception:  # noqa: BLE001 - an unresolvable target simply means "not in this state"
        return False
    if matcher == "value.length>0":
        return bool(value)
    if matcher.startswith("value=="):
        return str(value) == matcher[len("value==") :]
    return False


def race(
    surface: WebPerception,
    candidates: list[Candidate],
    timeout_ms: int,
    params: dict[str, object],
    target: LocatorDescriptor | None = None,
    poll_ms: int = 250,
) -> RaceOutcome:
    """Poll until timeout, evaluating ALL detectors each pass. One match classifies."""
    deadline = time.monotonic() + timeout_ms / 1000
    hits: list[Candidate] = []
    while True:
        # Read the screen once, then judge every detector against that same reading —
        # otherwise the detectors race each other's page loads instead of the clock.
        screen = surface.page_text()
        hits = [
            c for c in candidates if evaluate(surface, c.checkpoint, params, target, screen)
        ]
        if len(hits) == 1:
            return RaceOutcome(
                kind="match", matched=[hits[0].name], role=hits[0].role, name=hits[0].name
            )
        if len(hits) > 1:
            # Never guess between two readings of the screen.
            return RaceOutcome(
                kind="ambiguous", matched=[c.name for c in hits], observed=summarize(screen)
            )
        if time.monotonic() >= deadline:
            return RaceOutcome(kind="timeout", observed=summarize(surface.page_text()))
        time.sleep(poll_ms / 1000)


def describe_step(step: Step) -> str:
    """One line naming what a step tries to do — for failures and for the operator bundle."""
    if step.target is None:
        return f"{step.action} {step.value}"
    return f"{step.action} on {step.target.role} '{step.target.accessible_name}'"


def summarize(screen: str) -> str:
    return " / ".join(line.strip() for line in screen.splitlines() if line.strip())[:300]
