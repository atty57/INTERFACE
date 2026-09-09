"""Lease exclusivity, the handoff, and the re-anchor on the way back.

The operator here is a function rather than a person at a browser, which is exactly what
the console does when a person clicks Claim. The mechanism under it — lease transitions,
event capture, re-anchoring — is the real one either way.
"""

from __future__ import annotations

import pytest

from cua.artifact.store import ArtifactStore
from cua.escalate.broker import EscalationBroker
from cua.escalate.console import router
from cua.evidence.bus import EvidenceBus
from cua.orchestrator import execute
from cua.policy.redact import Redactor
from cua.session.lease import Holder, LeaseViolation
from cua.surface.base import Action

CAPABILITY = "member.read_savings_balance"


@pytest.fixture(scope="session")
def artifact():
    return ArtifactStore().load(CAPABILITY).model_copy(update={"approval_state": "approved"})


@pytest.fixture
def evidence(tmp_path):
    return EvidenceBus("escalation", root=tmp_path, redactor=Redactor())


@pytest.fixture(autouse=True)
def _credentials(credentials, monkeypatch):
    user, password = credentials
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_USERNAME", user)
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_PASSWORD", password)


def run(artifact, session, base_url, evidence, escalation, fault="dialog"):
    return execute(
        artifact,
        session,
        {"member_id": "12345"},
        evidence=evidence,
        base_url=base_url,
        escalation=escalation,
        fault=fault,
    )


def finish_by_hand(base_url):
    """What a person does at the console: drive the same live window, then signal Done.

    Here they clear the stuck notice the automation could not get past and land the content
    frame on the member detail screen — by hand, in the session automation was using.
    """

    def operator(request, session, surface):
        frame = session.page.frame(name="content") or session.page.main_frame
        frame.goto(f"{base_url}/detail?member_id=12345&fault=none")
        session.page.wait_for_timeout(400)
        return "done"

    return operator


# --- I3: exactly one holder, always known ------------------------------------------


def test_automation_cannot_act_while_a_human_holds_the_lease(session, base_url):
    from cua.policy.gate import PolicyGate
    from cua.policy.policy import Policy
    from cua.surface.web import WebSurface

    surface = WebSurface(
        session.page, PolicyGate(Policy(allowlisted_domains=["127.0.0.1"])), session.lease
    )
    session.lease.transfer(Holder.HUMAN)
    with pytest.raises(LeaseViolation) as caught:
        surface.act(Action(kind="navigate", url=base_url))
    assert "human" in str(caught.value)


def test_nobody_can_act_while_a_request_is_open(session, base_url):
    from cua.policy.gate import PolicyGate
    from cua.policy.policy import Policy
    from cua.surface.web import WebSurface

    surface = WebSurface(
        session.page, PolicyGate(Policy(allowlisted_domains=["127.0.0.1"])), session.lease
    )
    session.lease.transfer(Holder.NONE)
    with pytest.raises(LeaseViolation):
        surface.act(Action(kind="navigate", url=base_url))


# --- the full cycle ----------------------------------------------------------------


def test_a_hard_failure_raises_an_intervention_carrying_the_whole_context(
    artifact, session, base_url, evidence
):
    seen = {}

    def inspect(request, session_, surface_):
        seen.update(request.model_dump())
        return "abort"

    broker = EscalationBroker(evidence, operator=inspect)
    run(artifact, session, base_url, evidence, broker)

    assert seen["capability_id"] == CAPABILITY
    assert seen["goal"]
    assert seen["step_id"] == "s5"
    assert seen["why_stopped"]
    assert "Member Detail" in seen["expected"]
    assert seen["observed"]
    assert seen["last_good_checkpoint"].startswith("s4")
    assert seen["screenshot_ref"].endswith(".png")
    assert seen["proposed_action"]


def test_claiming_transfers_the_lease_and_automation_becomes_unable_to_act(
    artifact, session, base_url, evidence
):
    holders = []

    def observe(request, session_, surface_):
        holders.append(session_.lease.holder)
        with pytest.raises(LeaseViolation):
            surface_.act(Action(kind="navigate", url=base_url))
        return "abort"

    run(artifact, session, base_url, evidence, EscalationBroker(evidence, operator=observe))
    assert holders == [Holder.HUMAN]


def test_the_human_drives_the_same_session_and_the_run_resumes_to_success(
    artifact, session, base_url, evidence
):
    """Nothing is serialized across the handoff: the sign-on cookie is still there."""
    broker = EscalationBroker(evidence, operator=finish_by_hand(base_url))
    result = run(artifact, session, base_url, evidence, broker)
    assert result.kind == "success"
    assert result.outputs == {"savings": 4182.55}
    assert broker.queue[0].state == "resolved"
    assert broker.queue[0].resolution == "resume"


def test_what_the_human_did_is_captured_to_the_evidence_trail(
    artifact, session, base_url, evidence
):
    broker = EscalationBroker(evidence, operator=finish_by_hand(base_url))
    run(artifact, session, base_url, evidence, broker)
    captured = broker.queue[0].human_actions
    assert captured, "no human activity was recorded"
    assert any(e["kind"] in ("click", "navigate") for e in captured)
    assert [e for e in evidence.read() if e["event"] == "human_action"]


def test_the_lease_returns_to_automation_after_handback(
    artifact, session, base_url, evidence
):
    broker = EscalationBroker(evidence, operator=finish_by_hand(base_url))
    run(artifact, session, base_url, evidence, broker)
    assert session.lease.holder is Holder.AUTOMATION
    assert Holder.NONE in session.lease.history and Holder.HUMAN in session.lease.history


def test_an_unrecognised_state_on_handback_goes_back_to_the_operator(
    artifact, session, base_url, evidence
):
    """Re-anchor fails, so the engine does not act blindly on a screen it cannot place."""

    def wander_off(request, session_, surface_):
        session_.page.goto("about:blank")
        return "done"

    broker = EscalationBroker(evidence, operator=wander_off)
    result = run(artifact, session, base_url, evidence, broker)
    assert result.kind == "failure"
    assert [e for e in evidence.read() if e["event"] == "re_anchor_failed"]
    assert broker.queue[0].state == "open"


def test_abort_ends_the_run_with_a_clear_failure(artifact, session, base_url, evidence):
    broker = EscalationBroker(evidence, operator=lambda r, s, w: "abort")
    result = run(artifact, session, base_url, evidence, broker)
    assert result.kind == "failure"
    assert result.failure_class == "operator_aborted"
    assert result.escalation_id == broker.queue[0].id


def test_a_queue_that_nobody_claims_times_out_rather_than_hanging(
    artifact, session, base_url, evidence
):
    broker = EscalationBroker(evidence, wait_timeout_s=1.0)
    result = run(artifact, session, base_url, evidence, broker)
    assert result.failure_class == "escalation_timeout"


# --- the console shell --------------------------------------------------------------


def test_the_console_lists_open_interventions_and_offers_the_three_verbs(evidence):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cua.escalate.broker import InterventionRequest

    broker = EscalationBroker(evidence)
    broker.queue.append(
        InterventionRequest(
            capability_id=CAPABILITY,
            goal="look up a member",
            step_id="s5",
            why_stopped="checkpoint missed",
            expected="text 'Member Detail'",
            observed="a system notice",
        )
    )
    app = FastAPI()
    app.include_router(router(broker))
    client = TestClient(app)

    page = client.get("/operator").text
    assert "s5" in page
    for verb in ("Claim", "Done", "Abort"):
        assert verb in page
    assert client.get("/operator/api").json()[0]["capability_id"] == CAPABILITY


def test_the_console_shows_an_explanation_when_the_queue_is_empty(evidence):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router(EscalationBroker(evidence)))
    assert "No open interventions" in TestClient(app).get("/operator").text


def test_the_intervention_summarizes_parameters_without_their_values(
    artifact, session, base_url, evidence
):
    broker = EscalationBroker(evidence, operator=lambda r, s, w: "abort")
    run(artifact, session, base_url, evidence, broker)
    assert broker.queue[0].params_summary == {"member_id": "(redacted)"}
