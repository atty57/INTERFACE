"""Exactly one holder controls a session at a time, and it is always known (I3).

The lease is enforced, not advisory: ``Surface.act()`` asserts the caller holds it and
raises otherwise. There is no state in which both automation and a human can act.
"""

from __future__ import annotations

from enum import Enum


class Holder(str, Enum):
    NONE = "none"
    AUTOMATION = "automation"
    HUMAN = "human"


class LeaseViolation(Exception):
    """Something tried to act while another holder — or nobody — held the lease."""


class ControlLease:
    def __init__(self, holder: Holder = Holder.NONE) -> None:
        self.holder = holder
        self.history: list[Holder] = [holder]

    def transfer(self, to: Holder) -> None:
        self.holder = to
        self.history.append(to)

    def require(self, caller: Holder) -> None:
        if self.holder is not caller:
            raise LeaseViolation(
                f"{caller.value} tried to act while the lease is held by {self.holder.value}"
            )
