"""Owns the browser process and the control lease. Deliberately below both engines.

Chrome runs headful on a debugging port and automation attaches over CDP. Because the
browser is local and visible, "the human takes control of the live session" is physically
true with no streaming infrastructure: the window is on screen, and nothing is torn down
on escalation, so cookies, navigation state and half-filled forms all survive.
"""

from __future__ import annotations

import atexit
import os
import socket
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from .lease import ControlLease, Holder


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


_driver: Playwright | None = None


def driver() -> Playwright:
    """One Playwright driver per process.

    The sync API binds its event loop to the calling thread, so a second ``start()`` in the
    same thread fails. A replay opened while a session is already alive is a legitimate
    thing to do, so the driver is shared and only the browsers are per-session.
    """
    global _driver
    if _driver is None:
        _driver = sync_playwright().start()
        atexit.register(shutdown)
    return _driver


def shutdown() -> None:
    global _driver
    if _driver is not None:
        try:
            _driver.stop()
        except Exception:  # noqa: BLE001 - interpreter teardown
            pass
        _driver = None


def _headless_default() -> bool:
    return os.environ.get("CUA_HEADLESS", "1") not in ("0", "false", "no")


@dataclass
class BrowserSession:
    playwright: Playwright
    launched: Browser
    browser: Browser
    page: Page
    cdp_url: str
    lease: ControlLease = field(default_factory=lambda: ControlLease(Holder.AUTOMATION))
    human_actions: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        self.page.context.clear_cookies()
        self.page.goto("about:blank")
        self.human_actions.clear()
        self.lease.transfer(Holder.AUTOMATION)

    def close(self) -> None:
        # The driver stays up for the process; only this session's browsers close.
        for shut in (self.browser.close, self.launched.close):
            try:
                shut()
            except Exception:  # noqa: BLE001 - teardown must not mask a test failure
                pass


class SessionBroker:
    @staticmethod
    def launch(headless: bool | None = None) -> BrowserSession:
        headless = _headless_default() if headless is None else headless
        port = _free_port()
        pw = driver()
        launched = pw.chromium.launch(
            headless=headless, args=[f"--remote-debugging-port={port}"]
        )
        # Attach the way an external tool would, so a human at the same window is not a
        # special case. This is what makes the handoff in Flow C real.
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        return BrowserSession(pw, launched, browser, page, f"http://127.0.0.1:{port}")
