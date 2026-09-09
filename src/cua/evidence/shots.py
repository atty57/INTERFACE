"""Masked screenshots.

"Never persist sensitive data" and "capture a screenshot on failure" collide. The
mechanism: overwrite the *rendered* value of anything sensitive with bullets, capture,
then restore. It is a real mechanism rather than a promise, and it is honest about its
limit — it catches flagged values, not arbitrary PII rendered elsewhere on the page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MASK_PAGE = r"""
(values) => {
  const marks = [];
  const bullets = '••••';
  document.querySelectorAll('input, textarea').forEach(el => {
    if (el.type === 'password' || (el.value && values.includes(el.value))) {
      marks.push({el, kind: 'value', was: el.value});
      el.value = bullets;
    }
  });
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const hits = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (values.some(v => v && node.nodeValue.includes(v))) hits.push(node);
  }
  hits.forEach(node => {
    marks.push({el: node, kind: 'text', was: node.nodeValue});
    let masked = node.nodeValue;
    values.forEach(v => { if (v) masked = masked.split(v).join(bullets); });
    node.nodeValue = masked;
  });
  window.__cuaMasked = marks;
  return marks.length;
}
"""

RESTORE_PAGE = r"""
() => {
  (window.__cuaMasked || []).forEach(m => {
    if (m.kind === 'value') m.el.value = m.was; else m.el.nodeValue = m.was;
  });
  window.__cuaMasked = [];
}
"""


def masked_screenshot(page: Any, destination: Path, values: list[str]) -> Path:
    """Mask, capture, restore. Restoration runs even if the capture fails."""
    frames = list(page.frames)
    for frame in frames:
        try:
            frame.evaluate(MASK_PAGE, values)
        except Exception:  # noqa: BLE001 - a detached frame must not block evidence
            continue
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(destination), full_page=False)
    finally:
        for frame in frames:
            try:
                frame.evaluate(RESTORE_PAGE)
            except Exception:  # noqa: BLE001
                continue
    return destination
