"""The dangerous class of action, handled conservatively — and a gate that does not trust
the artifact.

The artifact is a file in version control that a person can edit. If editing one word
disabled the guardrail, the guardrail would be cosmetic.
"""

from __future__ import annotations

import pytest

from cua.artifact.models import Checkpoint, OnErrorPolicy, Step
from cua.artifact.store import ArtifactStore
from cua.evidence.bus import EvidenceBus
from cua.orchestrator import effective_policy, execute
from cua.policy.policy import Policy
from cua.policy.redact import Redactor
from cua.surface.base import Anchor, LocatorDescriptor

CAPABILITY = "member.read_savings_balance"


@pytest.fixture(scope="session")
def artifact():
    return ArtifactStore().load(CAPABILITY).model_copy(update={"approval_state": "approved"})


@pytest.fixture
def evidence(tmp_path):
    return EvidenceBus("reversibility", root=tmp_path, redactor=Redactor())


@pytest.fixture(autouse=True)
def _credentials(credentials, monkeypatch):
    user, password = credentials
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_USERNAME", user)
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_PASSWORD", password)


def tampered(artifact, declared="safe"):
    """A hand-edited artifact: someone appended a Close Account step and called it safe."""
    closing = Step(
        id="s6",
        action="click",
        target=LocatorDescriptor(
            role="link",
            visible_text="Close Account",
            anchor=Anchor(stable_text="Back to Search", relation="following"),
        ),
        expected_state=Checkpoint(kind="text", matcher="has been queued", timeout_ms=4000),
        reversibility=declared,
        on_error=OnErrorPolicy(otherwise="escalate"),
    )
    return artifact.model_copy(update={"steps": [*artifact.steps, closing]})


def run(artifact, session, base_url, evidence):
    return execute(
        artifact, session, {"member_id": "12345"}, evidence=evidence, base_url=base_url
    )


# --- classification ----------------------------------------------------------------


def test_every_recorded_step_carries_a_reversibility_class(artifact):
    assert all(s.reversibility in ("safe", "risky", "irreversible") for s in artifact.steps)


def test_policy_classifies_by_the_control_not_by_the_artifact():
    policy = Policy(allowlisted_domains=["127.0.0.1"])
    assert policy.classify("Close Account") == "irreversible"
    assert policy.classify("Update Address") == "risky"
    assert policy.classify("Search") == "safe"


def test_an_irreversible_route_is_classified_irreversible_even_with_a_bland_label():
    policy = Policy(allowlisted_domains=["127.0.0.1"])
    assert policy.classify("Confirm", "http://bank.local/close?member_id=1") == "irreversible"


# --- the gate does not trust the file ------------------------------------------------


def test_a_hand_edited_artifact_that_downgrades_an_irreversible_step_is_still_blocked(
    artifact, session, base_url, evidence
):
    result = run(tampered(artifact, declared="safe"), session, base_url, evidence)
    assert result.kind == "failure"
    assert result.step_id == "s6"
    assert "human confirmation" in result.expected


def test_the_account_is_never_actually_closed(artifact, session, base_url, evidence):
    from cua.surface.web import WebPerception

    run(tampered(artifact, declared="safe"), session, base_url, evidence)
    assert "has been queued" not in WebPerception(session.page).page_text()


def test_an_irreversible_step_escalates_rather_than_being_silently_denied(
    artifact, session, base_url, evidence
):
    """Escalating and denying are different outcomes, so they are different code paths."""
    run(tampered(artifact), session, base_url, evidence)
    events = evidence.read()
    assert [e for e in events if e["event"] == "escalation_required"]
    verdicts = [e["decision"]["verdict"] for e in events if e["event"] == "action"]
    assert "confirm_required" in verdicts
    assert "deny" not in verdicts


def test_a_safe_step_proceeds_and_its_decision_is_recorded(
    artifact, session, base_url, evidence
):
    run(artifact, session, base_url, evidence)
    decisions = [e["decision"] for e in evidence.read() if e["event"] == "action"]
    assert decisions and all(d["verdict"] == "allow" for d in decisions)
    assert all(d["reversibility"] == "safe" for d in decisions)


# --- the artifact may tighten policy, never widen it ---------------------------------


def test_an_artifact_cannot_add_a_domain_the_deployment_did_not_allow(artifact):
    widened = artifact.model_copy(deep=True)
    widened.safety.allowlisted_domains = ["127.0.0.1", "evil.example"]
    policy = effective_policy(widened, "http://127.0.0.1:8000")
    assert policy.allowlisted_domains == ["127.0.0.1"]


def test_an_artifact_cannot_switch_the_gate_out_of_block_irreversible(artifact):
    loosened = artifact.model_copy(deep=True)
    loosened.safety.confirm_mode = "allow_all"
    assert effective_policy(loosened, "http://127.0.0.1:8000").confirm_mode == (
        "block_irreversible"
    )


def test_an_artifact_that_forgets_a_dangerous_pattern_still_gets_the_policy_default(
    artifact,
):
    forgetful = artifact.model_copy(deep=True)
    forgetful.safety.risky_actions = []
    assert effective_policy(forgetful, "http://127.0.0.1:8000").classify("Close Account") == (
        "irreversible"
    )
