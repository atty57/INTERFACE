"""Lease exclusivity, the handoff, and the re-anchor on the way back.

The operator here is a function rather than a person at a browser, which is exactly what
the console does when a person clicks Claim. The mechanism under it — lease transitions,
event capture, re-anchoring — is the real one either way.
"""

from __future__ import annotations

import pytest

from conftest import CAPABILITY
from cua.escalate.broker import EscalationBroker
from cua.escalate.console import router
from cua.orchestrator import execute
from cua.session.lease import Holder, LeaseViolation
from cua.surface.base import Action


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


def _console(broker):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router(broker))
    return TestClient(app)


def test_the_console_shows_the_flow_that_led_to_the_stop(evidence):
    """An operator needs the sequence, not just the step it died on."""
    evidence.log("action", action={"kind": "click"}, label="Sign On", decision={"verdict": "allow"}, tier=1)
    evidence.log("classified", step="s3", kind="match", matched=["s3"])
    evidence.log("recovery_attempt", step="s5", strategy="dismiss_known_dialog", attempt=1)
    evidence.log("lease_claimed", request="esc-1")

    page = _console(EscalationBroker(evidence)).get("/operator").text
    assert "Sign On" in page
    assert "dismiss_known_dialog (attempt 1)" in page
    assert "a human took control" in page


def test_the_console_renders_the_screenshot_rather_than_its_path(
    artifact, session, base_url, evidence
):
    """Asserted while the operator is actually looking at it, mid-handoff."""
    seen: dict = {}

    def look(request, session_, surface_):
        client = _console(broker)
        seen["page"] = client.get("/operator").text
        seen["shot"] = client.get(f"/operator/{request.id}/screenshot")
        seen["id"] = request.id
        return "abort"

    broker = EscalationBroker(evidence, operator=look)
    run(artifact, session, base_url, evidence, broker)

    assert f'src="/operator/{seen["id"]}/screenshot"' in seen["page"]
    assert seen["shot"].status_code == 200
    assert seen["shot"].headers["content-type"] == "image/png"
    assert seen["shot"].content[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_operator_sees_the_stopping_step_and_the_flow_that_led_there(
    artifact, session, base_url, evidence
):
    seen: dict = {}

    def look(request, session_, surface_):
        seen["page"] = _console(broker).get("/operator").text
        return "abort"

    broker = EscalationBroker(evidence, operator=look)
    run(artifact, session, base_url, evidence, broker)

    page = seen["page"]
    assert "s5" in page and "Member Detail" in page
    for verb in ("Claim", "Done", "Abort"):
        assert verb in page
    assert "recovery_attempt" in page  # the sequence, not just the endpoint


def test_the_screenshot_route_serves_nothing_outside_the_run_evidence(evidence):
    from cua.escalate.broker import InterventionRequest

    broker = EscalationBroker(evidence)
    broker.queue.append(
        InterventionRequest(
            capability_id=CAPABILITY,
            goal="g",
            step_id="s1",
            why_stopped="w",
            expected="e",
            observed="o",
            screenshot_ref="C:/Windows/win.ini" if __import__("os").name == "nt" else "/etc/passwd",
        )
    )
    got = _console(broker).get(f"/operator/{broker.queue[0].id}/screenshot")
    assert got.status_code == 404


def test_screen_text_from_the_page_cannot_inject_markup_into_the_console(evidence):
    """Observed text is page content, so it is escaped, never rendered as markup."""
    from cua.escalate.broker import InterventionRequest

    broker = EscalationBroker(evidence)
    broker.queue.append(
        InterventionRequest(
            capability_id=CAPABILITY,
            goal="g",
            step_id="s1",
            why_stopped="w",
            expected="e",
            observed="<script>alert(1)</script>",
        )
    )
    page = _console(broker).get("/operator").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
