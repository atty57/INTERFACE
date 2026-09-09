"""The web surface: Playwright, an accessibility-derived digest, frame walking, anchors.

There is no separate "legacy web" driver. Frame walking and anchor-relative targeting
*are* the legacy handling, and they live here.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Page

from ..policy.gate import ConfirmationRequired, PolicyDenied, PolicyGate
from ..session.lease import ControlLease, Holder

from .base import (
    Action,
    DigestEntry,
    Effect,
    ElementDigest,
    Located,
    LocatorDescriptor,
    LocatorUnresolved,
    SurfaceError,
)

INTERACTIVE = (
    'a[href], button, input:not([type="hidden"]), select, textarea, '
    '[role="button"], [role="link"]'
)

# Runs in the page. Computes what a screen reader would announce, plus the nearby stable
# text a table layout carries its labels in, plus a positional path as a last resort.
DESCRIBE = r"""
(els) => els.map(el => {
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  let role = el.getAttribute('role');
  if (!role) {
    if (tag === 'a') role = 'link';
    else if (tag === 'button') role = 'button';
    else if (tag === 'select') role = 'combobox';
    else if (tag === 'textarea') role = 'textbox';
    else if (tag === 'input') role = ({submit: 'button', button: 'button', reset: 'button',
        checkbox: 'checkbox', radio: 'radio'})[type] || 'textbox';
    else role = 'generic';
  }

  const forLabel = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
  const wrapping = el.closest('label');
  const buttonish = (type === 'submit' || type === 'button' || type === 'reset');
  const name = clean(el.getAttribute('aria-label')
    || (forLabel && forLabel.textContent)
    || (wrapping && wrapping.textContent)
    || el.getAttribute('title')
    || (buttonish ? el.value : '')
    || ((tag === 'a' || tag === 'button') ? el.textContent : '')
    || el.getAttribute('placeholder'));

  // Nearest stable text: in a table layout the label lives in the preceding cell.
  let near = '';
  const cell = el.closest('td, th');
  if (cell) {
    let prev = cell.previousElementSibling;
    while (prev && !clean(prev.textContent)) prev = prev.previousElementSibling;
    if (prev) near = clean(prev.textContent);
  }
  if (!near) {
    let n = el.previousSibling;
    while (n && !clean(n.textContent)) n = n.previousSibling;
    near = clean(n && n.textContent).slice(0, 60);
  }

  const r = el.getBoundingClientRect();
  return {
    role, accessible_name: name, near,
    placeholder: clean(el.getAttribute('placeholder')),
    visible_text: clean((tag === 'a' || tag === 'button') ? el.textContent
                        : (buttonish ? el.value : '')),
    structural: window.__cuaPath(el),
    value: (el.value === undefined ? '' : String(el.value)),
    visible: !!(r.width && r.height)
  };
})
"""

# A positional path, scoped to the frame. Tier 5 and nothing more: it is the signal that
# breaks first when the vendor reflows a screen, which is exactly why it ranks last.
PATH_HELPER = r"""
window.__cuaPath = el => {
  let e = el, parts = [];
  while (e && e.nodeType === 1 && e.tagName.toLowerCase() !== 'body') {
    const p = e.parentElement;
    if (!p) break;
    const kin = Array.prototype.filter.call(p.children, c => c.tagName === e.tagName);
    parts.unshift(e.tagName.toLowerCase() +
      (kin.length > 1 ? ':nth-of-type(' + (kin.indexOf(e) + 1) + ')' : ''));
    e = p;
  }
  return parts.join(' > ');
};
"""

# Anchor extraction: find the stable text, then read the value it labels.
EXTRACT_BY_ANCHOR = r"""
([text, relation]) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const wanted = clean(text).replace(/:$/, '').toLowerCase();
  const cells = Array.prototype.slice.call(
    document.querySelectorAll('td, th, li, dt, span'));
  const hit = cells.find(c => clean(c.textContent).replace(/:$/, '').toLowerCase() === wanted);
  if (!hit) return null;
  if (relation === 'same_cell') return clean(hit.textContent);
  let next = hit.nextElementSibling;
  while (next && !clean(next.textContent)) next = next.nextElementSibling;
  return next ? clean(next.textContent) : null;
}
"""


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().rstrip(":").casefold()


class WebPerception:
    """Perceive and locate on one browser page. Knows nothing about artifacts or models.

    ``WebSurface`` in ``policy``-aware form adds ``act()`` on top of this; the split keeps
    the gate impossible to skip, since the only class that can act is the gated one.
    """

    def __init__(self, page: Page) -> None:
        self.page = page

    # --- perceive -----------------------------------------------------------------

    def frames(self) -> list[tuple[list[str], Frame]]:
        """Every frame with the path of frame names that reaches it. Main frame is []."""
        out: list[tuple[list[str], Frame]] = []
        for frame in self.page.frames:
            path: list[str] = []
            node: Frame | None = frame
            while node is not None and node.parent_frame is not None:
                path.insert(0, node.name or "?")
                node = node.parent_frame
            out.append((path, frame))
        return out

    def snapshot(self) -> ElementDigest:
        digest = ElementDigest(url=self.page.url)
        for path, frame in self.frames():
            try:
                frame.evaluate(PATH_HELPER)
                described = frame.eval_on_selector_all(INTERACTIVE, DESCRIBE)
            except Exception:  # noqa: BLE001 - a frame can detach mid-walk; skip it
                continue
            for ordinal, item in enumerate(described):
                if not item.pop("visible", False):
                    continue
                digest.entries.append(
                    DigestEntry(
                        index=len(digest.entries),
                        ordinal=ordinal,
                        frame_path=path,
                        **item,
                    )
                )
        return digest

    def page_text(self) -> str:
        """Visible text across every frame, for text checkpoints and detectors."""
        chunks = []
        for _, frame in self.frames():
            try:
                chunks.append(frame.locator("body").inner_text(timeout=1000))
            except Exception:  # noqa: BLE001 - detached or still-loading frame
                continue
        return "\n".join(chunks)

    # --- locate -------------------------------------------------------------------

    def locate(self, desc: LocatorDescriptor) -> Located:
        """The ladder: the first tier resolving to exactly one control wins.

        More than one match is a failure, never a first match — silently acting on the
        wrong control is the failure mode this whole design exists to avoid.
        """
        entries = [
            e
            for e in self.snapshot().entries
            if not desc.frame_path or e.frame_path == desc.frame_path
        ]
        attempts: list[str] = []
        for tier, matches in enumerate(_tiers(desc, entries), start=1):
            if matches is None:
                attempts.append(f"tier {tier}: no signal recorded")
                continue
            if len(matches) == 1:
                return Located(tier=tier, handle=self.handle_for(matches[0]))
            attempts.append(f"tier {tier}: {len(matches)} matches")
        if desc.structural:
            handles = self.frame_at(desc.frame_path).query_selector_all(desc.structural)
            if len(handles) == 1:
                return Located(tier=5, handle=handles[0])
            attempts.append(f"tier 5: {len(handles)} matches")
        else:
            attempts.append("tier 5: no signal recorded")
        raise LocatorUnresolved(desc, attempts)

    def frame_at(self, path: list[str]) -> Frame:
        """Walk the frame path first — framesets are the number one legacy failure mode."""
        for candidate, frame in self.frames():
            if candidate == path:
                return frame
        raise SurfaceError(f"no frame at path {path or ['(main)']}")

    def handle_for(self, entry: DigestEntry) -> Any:
        handles = self.frame_at(entry.frame_path).query_selector_all(INTERACTIVE)
        if entry.ordinal < 0 or entry.ordinal >= len(handles):
            raise SurfaceError(f"lost the handle for [{entry.index}] {entry.accessible_name}")
        return handles[entry.ordinal]

    def extract(self, desc: LocatorDescriptor) -> str | None:
        """Read a value the way a person does: find the label, read what it labels."""
        if not desc.anchor:
            return None
        for _, frame in self.frames():
            try:
                found = frame.evaluate(
                    EXTRACT_BY_ANCHOR, [desc.anchor.stable_text, desc.anchor.relation]
                )
            except Exception:  # noqa: BLE001 - detached frame
                continue
            if found:
                return str(found)
        return None


def _tiers(
    desc: LocatorDescriptor, entries: list[DigestEntry]
) -> list[list[DigestEntry] | None]:
    """Tiers 1 to 4, in priority order. ``None`` means the artifact recorded no signal."""

    def role_ok(e: DigestEntry) -> bool:
        return not desc.role or e.role == desc.role

    def match(field: str, wanted: str | None) -> list[DigestEntry] | None:
        if not wanted:
            return None
        return [e for e in entries if role_ok(e) and norm(getattr(e, field)) == norm(wanted)]

    return [
        match("accessible_name", desc.accessible_name),
        match("placeholder", desc.label or desc.placeholder),
        match("visible_text", desc.visible_text),
        match("near", desc.anchor.stable_text if desc.anchor else None),
    ]


LABEL_JS = r"""
el => (el.getAttribute('aria-label') || el.getAttribute('title')
       || ((el.type === 'submit' || el.type === 'button' || el.type === 'reset') ? el.value : '')
       || el.textContent || '').replace(/\s+/g, ' ').trim()
"""

HREF_JS = "el => (el.tagName === 'A' ? el.href : '')"


class WebSurface(WebPerception):
    """Perception plus action. The only class that can act, so the gate cannot be skipped.

    ``act()`` asserts the lease, authorizes through the policy gate, writes the decision to
    evidence — allowed, denied, or escalated alike — and only then touches the page.
    """

    def __init__(
        self,
        page: Page,
        gate: PolicyGate,
        lease: ControlLease,
        holder: Holder = Holder.AUTOMATION,
        evidence: Any = None,
    ) -> None:
        super().__init__(page)
        self.gate = gate
        self.lease = lease
        self.holder = holder
        self.evidence = evidence

    def act(self, action: Action) -> Effect:
        self.lease.require(self.holder)
        located, label, href = self._context(action)
        decision = self.gate.authorize(
            action.kind,
            url=action.url,
            label=label,
            href=href,
            declared=action.reversibility,
        )
        if self.evidence is not None:
            self.evidence.log(
                "action",
                action=action.model_dump(exclude_none=True),
                decision=decision.model_dump(),
                label=label,
                tier=located.tier if located else None,
            )
        if decision.verdict == "deny":
            raise PolicyDenied(decision.reason)
        if decision.verdict == "confirm_required":
            raise ConfirmationRequired(decision.reason, decision)
        return self._perform(action, located)

    # --- internals ----------------------------------------------------------------

    def _context(self, action: Action) -> tuple[Located | None, str, str]:
        """Resolve the target before authorizing, so the gate judges the real control."""
        if action.kind in ("navigate", "wait", "assert"):
            return None, action.url, ""
        if action.kind not in self.gate.policy.allowed_actions:
            return None, "", ""  # deny without doing any work
        if action.kind == "extract":
            return None, "", ""
        located = self._resolve(action)
        return located, located.handle.evaluate(LABEL_JS), located.handle.evaluate(HREF_JS)

    def _resolve(self, action: Action) -> Located:
        if action.index is not None:
            entry = next(
                (e for e in self.snapshot().entries if e.index == action.index), None
            )
            if entry is None:
                raise SurfaceError(f"no control at digest index {action.index}")
            return Located(tier=0, handle=self.handle_for(entry))
        if action.target is None:
            raise SurfaceError(f"{action.kind} needs a target")
        return self.locate(action.target)

    def _perform(self, action: Action, located: Located | None) -> Effect:
        tier = located.tier if located else None
        try:
            if action.kind == "navigate":
                self.page.goto(action.url, wait_until="load")
            elif action.kind == "click":
                assert located is not None
                located.handle.click()
            elif action.kind == "type":
                assert located is not None
                located.handle.fill(self.gate.resolve(action.value))
            elif action.kind == "select":
                assert located is not None
                located.handle.select_option(self.gate.resolve(action.value))
            elif action.kind == "extract":
                assert action.target is not None
                return Effect(
                    action=action, tier=4, extracted=self.extract(action.target), url=self.page.url
                )
        except PlaywrightError as failure:
            raise SurfaceError(f"{action.kind} failed: {failure}") from failure
        return Effect(action=action, tier=tier, url=self.page.url)
