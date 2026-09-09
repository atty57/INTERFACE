"""Three independent stopping triggers. A confused run terminates instead of burning money."""

from __future__ import annotations

import time

from pydantic import BaseModel


class Budget(BaseModel):
    max_steps: int = 25
    wall_clock_s: float = 300.0
    max_tokens: int = 400_000


class StuckDetector:
    """Explicit, no-progress, and budget — in that order of preference.

    The explicit ``stuck()`` tool is cheapest and carries the best context. The no-progress
    detector catches the common failure: a model looping without admitting it. The budget
    is the backstop for everything else.
    """

    def __init__(self, budget: Budget, no_progress_limit: int = 3) -> None:
        self.budget = budget
        self.no_progress_limit = no_progress_limit
        self.started = time.monotonic()
        self.steps = 0
        self.tokens = 0
        self._last_fingerprint = ""
        self._repeats = 0

    def spend(self, tokens: int) -> None:
        self.tokens += tokens

    def observe(self, fingerprint: str) -> None:
        if fingerprint == self._last_fingerprint:
            self._repeats += 1
        else:
            self._repeats = 0
            self._last_fingerprint = fingerprint

    def trigger(self) -> tuple[str, str] | None:
        """Return ``(trigger, reason)`` if the run should stop, else ``None``."""
        if self._repeats >= self.no_progress_limit:
            return (
                "no_progress",
                f"the screen has not changed across {self._repeats + 1} consecutive steps",
            )
        if self.steps >= self.budget.max_steps:
            return "budget", f"step budget of {self.budget.max_steps} exhausted"
        elapsed = time.monotonic() - self.started
        if elapsed >= self.budget.wall_clock_s:
            return "budget", f"wall clock budget of {self.budget.wall_clock_s:.0f}s exhausted"
        if self.tokens >= self.budget.max_tokens:
            return "budget", f"token budget of {self.budget.max_tokens} exhausted"
        return None
