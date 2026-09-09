"""Recovering from the annoying rather than the fatal, without ever improvising."""

from __future__ import annotations

import pytest

from cua.artifact.store import ArtifactStore
from cua.evidence.bus import EvidenceBus
from cua.orchestrator import execute
from cua.policy.redact import Redactor

CAPABILITY = "member.read_savings_balance"


@pytest.fixture(scope="session")
def artifact():
    return ArtifactStore().load(CAPABILITY).model_copy(update={"approval_state": "approved"})


@pytest.fixture
def evidence(tmp_path):
    return EvidenceBus("recovery", root=tmp_path, redactor=Redactor())


@pytest.fixture(autouse=True)
def _credentials(credentials, monkeypatch):
    user, password = credentials
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_USERNAME", user)
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_PASSWORD", password)


def run(artifact, session, base_url, evidence, fault=None):
    return execute(
        artifact,
        session,
        {"member_id": "12345"},
        evidence=evidence,
        base_url=base_url,
        fault=fault,
    )


def events(evidence, name):
    return [e for e in evidence.read() if e["event"] == name]


def test_a_known_interstitial_dialog_is_dismissed_and_the_step_retried(
    artifact, session, base_url, evidence
):
    result = run(artifact, session, base_url, evidence, fault="dialog_once")
    assert result.kind == "success"
    assert result.outputs == {"savings": 4182.55}
    attempts = events(evidence, "recovery_attempt")
    assert [a["strategy"] for a in attempts] == ["dismiss_known_dialog"]


def test_an_expired_session_is_re_authenticated_and_the_step_retried(
    artifact, session, base_url, evidence
):
    result = run(artifact, session, base_url, evidence, fault="timeout")
    assert result.kind == "success"
    assert [a["strategy"] for a in events(evidence, "recovery_attempt")] == ["re_login"]


def test_a_slow_load_needs_no_recovery_at_all(artifact, session, base_url, evidence):
    assert run(artifact, session, base_url, evidence, fault="slow").kind == "success"
    assert events(evidence, "recovery_attempt") == []


def test_attempts_are_capped_and_exhaustion_is_a_hard_failure_not_a_loop(
    artifact, session, base_url, evidence
):
    """The sticky dialog comes straight back, so recovery has something to exhaust against."""
    result = run(artifact, session, base_url, evidence, fault="dialog")
    assert result.kind == "failure"
    signature = next(s for s in artifact.recoverable_signatures if s.name == "interstitial_dialog")
    assert len(events(evidence, "recovery_attempt")) == signature.max_attempts
    assert events(evidence, "recovery_exhausted")


def test_every_recovery_attempt_is_logged_with_its_strategy_and_attempt_number(
    artifact, session, base_url, evidence
):
    run(artifact, session, base_url, evidence, fault="dialog")
    attempts = events(evidence, "recovery_attempt")
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert all(a["step"] == "s5" for a in attempts)


def test_a_strategy_the_capability_did_not_declare_is_refused(
    artifact, session, base_url, evidence
):
    """Recovery is declared by the capability, never invented at runtime."""
    stripped = artifact.model_copy(deep=True)
    for step in stripped.steps:
        step.on_error.recover = []
    result = run(stripped, session, base_url, evidence, fault="dialog_once")
    assert result.kind == "failure"
    assert events(evidence, "recovery_refused")
    assert events(evidence, "recovery_attempt") == []
