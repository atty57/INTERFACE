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
from pathlib import Path

import pytest
import uvicorn

from cua.artifact.models import CapabilityArtifact
from cua.artifact.store import ArtifactStore
from cua.evidence.bus import EvidenceBus
from cua.mock_bank.app import app as mock_app
from cua.mock_bank.data import operator_credentials
from cua.policy.redact import Redactor
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


CAPABILITY = "member.read_savings_balance"


@pytest.fixture(autouse=True)
def operator_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handles the committed artifact declares, resolved from the environment."""
    user, password = operator_credentials()
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_USERNAME", user)
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_PASSWORD", password)


@pytest.fixture(scope="session")
def committed_artifact() -> CapabilityArtifact:
    """The artifact in ``capabilities/`` — recorder output, committed, reviewed as a diff."""
    return ArtifactStore().load(CAPABILITY)


@pytest.fixture
def artifact(committed_artifact: CapabilityArtifact) -> CapabilityArtifact:
    """The committed artifact, approved — which is what a caller actually invokes."""
    return committed_artifact.model_copy(update={"approval_state": "approved"})


@pytest.fixture
def evidence(tmp_path: Path, request: pytest.FixtureRequest) -> EvidenceBus:
    return EvidenceBus(request.node.name[:60], root=tmp_path, redactor=Redactor())
