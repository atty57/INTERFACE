"""Capture what the human did while they held the lease.

The brief requires it verbatim: preserve context and evidence across the handoff, and
record what the human did. Listeners are installed in the live page, so this is the real
session's real events — redacted on the way to the evidence bus. A compounding benefit:
repeated human intervention at the same step is the signal to re-record the capability.
"""

from __future__ import annotations

from typing import Any

BINDING = "__cuaHumanEvent"

CAPTURE_JS = """
() => {
  if (window.__cuaCapturing) return;
  window.__cuaCapturing = true;
  const describe = el => {
    if (!el || !el.tagName) return 'unknown';
    const name = el.getAttribute('title') || el.getAttribute('aria-label')
      || ((el.type === 'submit' || el.type === 'button') ? el.value : '')
      || (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 40);
    return el.tagName.toLowerCase() + (name ? ' "' + name + '"' : '');
  };
  document.addEventListener('click', e => {
    window.__cuaHumanEvent({kind: 'click', target: describe(e.target)});
  }, true);
  document.addEventListener('change', e => {
    const secret = e.target && e.target.type === 'password';
    window.__cuaHumanEvent({
      kind: 'change',
      target: describe(e.target),
      value: secret ? '(password)' : String((e.target && e.target.value) || ''),
    });
  }, true);
}
"""


class HumanActivityRecorder:
    """Installs listeners for the duration of a handoff and collects what they see."""

    def __init__(self, session: Any, evidence: Any) -> None:
        self.session = session
        self.evidence = evidence
        self.events: list[dict[str, Any]] = []
        self._installed = False

    def _on_event(self, source: Any, payload: dict[str, Any]) -> None:
        del source
        record = {"holder": "human", **payload}
        self.events.append(record)
        self.session.human_actions.append(record)
        self.evidence.log("human_action", **record)

    def _on_navigation(self, frame: Any) -> None:
        if frame.parent_frame is not None and frame.url == "about:blank":
            return
        self._on_event(None, {"kind": "navigate", "target": frame.url})

    def start(self) -> None:
        page = self.session.page
        if not self._installed:
            try:
                page.expose_binding(BINDING, self._on_event)
            except Exception:  # noqa: BLE001 - already exposed on this page
                pass
            page.add_init_script(f"({CAPTURE_JS})()")
            page.on("framenavigated", self._on_navigation)
            self._installed = True
        for frame in page.frames:
            try:
                frame.evaluate(CAPTURE_JS)
            except Exception:  # noqa: BLE001 - detached or cross-origin frame
                continue

    def stop(self) -> list[dict[str, Any]]:
        return list(self.events)
