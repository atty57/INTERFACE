"""Bounded recovery. Named strategies, declared by the capability, capped, and logged.

Each strategy is one of three the engine knows how to perform. The capability chooses
which of them a step may use; nothing is invented at runtime, and nothing is open-ended.
"""

from __future__ import annotations

from pydantic import BaseModel

# After these, the step's action is performed again. Dismissing a dialog reveals the page
# that was already behind it, so re-acting there would click a control that has gone.
REPEAT_ACTION_AFTER = {"retry_backoff", "re_login"}

BACKOFF_SECONDS = (0.8, 1.6, 3.2)

DISMISS_CONTROL = """
() => {
  const dialog = document.querySelector('[role="dialog"]');
  if (!dialog) return null;
  const control = dialog.querySelector('a, button, input[type="submit"]');
  if (!control) return null;
  const text = (control.value || control.textContent || '').replace(/\\s+/g, ' ').trim();
  return text || null;
}
"""


class RecoveryLedger(BaseModel):
    """Attempts per step per strategy. Exhaustion is a hard failure, never a loop."""

    attempts: dict[str, int] = {}

    def key(self, step_id: str, name: str) -> str:
        return f"{step_id}:{name}"

    def take(self, step_id: str, name: str, cap: int) -> bool:
        slot = self.key(step_id, name)
        if self.attempts.get(slot, 0) >= cap:
            return False
        self.attempts[slot] = self.attempts.get(slot, 0) + 1
        return True

    def used(self, step_id: str, name: str) -> int:
        return self.attempts.get(self.key(step_id, name), 0)
