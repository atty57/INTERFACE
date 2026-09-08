"""Session-scoped fixtures: the mock app and the browser start once for the whole run.

Nothing here mocks the browser or stubs the target app. A test that stubbed Playwright
would prove nothing about locator robustness against a frameset, which is the thesis.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from cua.mock_bank.app import app as mock_app
from cua.mock_bank.data import operator_credentials
from cua.session.broker import BrowserSession, SessionBroker

os.environ.setdefault("CUA_OPERATOR_USERNAME", "opsuser")
os.environ.setdefault("CUA_OPERATOR_PASSWORD", "synthetic-not-a-real-password")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    port = _free_port()
    config = uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock bank app did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def _browser() -> Iterator[BrowserSession]:
    session = SessionBroker.launch()
    yield session
    session.close()


@pytest.fixture
def session(_browser: BrowserSession) -> Iterator[BrowserSession]:
    _browser.reset()
    yield _browser


@pytest.fixture
def credentials() -> tuple[str, str]:
    return operator_credentials()
