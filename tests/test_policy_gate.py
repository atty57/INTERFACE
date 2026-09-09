"""The gate lives inside the driver, below the model. These tests try to get around it."""

from __future__ import annotations

import json

import pytest

from cua.evidence.bus import EvidenceBus
from cua.policy.gate import ConfirmationRequired, PolicyDenied, PolicyGate
from cua.policy.policy import Policy
from cua.policy.redact import Redactor
from cua.policy.secrets import SecretResolver
from cua.surface.base import Action, LocatorDescriptor
from cua.surface.web import WebSurface

HANDLE = "core_operator"


@pytest.fixture
def secrets(credentials, monkeypatch):
    user, password = credentials
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_USERNAME", user)
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_PASSWORD", password)
    return SecretResolver([HANDLE])


@pytest.fixture
def evidence(tmp_path, secrets):
    return EvidenceBus("test-run", root=tmp_path, redactor=Redactor(secrets.values()))


@pytest.fixture
def surface(session, base_url, secrets, evidence):
    policy = Policy(allowlisted_domains=["127.0.0.1", "localhost"])
    return WebSurface(session.page, PolicyGate(policy, secrets), session.lease, evidence=evidence)


def _sign_on(surface, base_url) -> None:
    surface.act(Action(kind="navigate", url=base_url))
    surface.act(
        Action(
            kind="type",
            target=LocatorDescriptor(role="textbox", accessible_name="User ID"),
            value="${secret:core_operator.username}",
        )
    )
    surface.act(
        Action(
            kind="type",
            target=LocatorDescriptor(role="textbox", accessible_name="Password"),
            value="${secret:core_operator.password}",
        )
    )
    surface.act(
        Action(kind="click", target=LocatorDescriptor(role="button", accessible_name="Sign On"))
    )
    surface.page.wait_for_timeout(400)


def _reach_detail(surface, base_url, member_id: str = "12345") -> None:
    _sign_on(surface, base_url)
    surface.act(
        Action(
            kind="type",
            target=LocatorDescriptor(role="textbox", accessible_name="Member ID"),
            value=member_id,
        )
    )
    surface.act(
        Action(kind="click", target=LocatorDescriptor(role="button", accessible_name="Search"))
    )
    surface.page.wait_for_timeout(400)


# --- acting ---------------------------------------------------------------------


def test_navigate_type_and_click_drive_the_live_application(surface, base_url):
    _sign_on(surface, base_url)
    assert "Member Search" in surface.page_text()


def test_typing_by_digest_index_works_the_way_discovery_uses_it(surface, base_url):
    surface.act(Action(kind="navigate", url=base_url))
    index = next(
        e.index for e in surface.snapshot().entries if e.accessible_name == "User ID"
    )
    surface.act(Action(kind="type", index=index, value="typed-by-index"))
    assert any(e.value == "typed-by-index" for e in surface.snapshot().entries)


# --- the allowlist --------------------------------------------------------------


def test_navigation_to_an_off_allowlist_domain_is_refused(surface):
    with pytest.raises(PolicyDenied) as caught:
        surface.act(Action(kind="navigate", url="https://example.com/"))
    assert "example.com" in str(caught.value)


def test_navigation_to_an_off_allowlist_route_is_refused(session, base_url, secrets, evidence):
    policy = Policy(allowlisted_domains=["127.0.0.1"], allowlisted_routes=["/", "/main"])
    narrow = WebSurface(
        session.page, PolicyGate(policy, secrets), session.lease, evidence=evidence
    )
    with pytest.raises(PolicyDenied):
        narrow.act(Action(kind="navigate", url=f"{base_url}/detail?member_id=12345"))


def test_an_off_allowlist_action_type_is_refused(session, base_url, secrets, evidence):
    policy = Policy(allowlisted_domains=["127.0.0.1"], allowed_actions=["navigate", "extract"])
    narrow = WebSurface(
        session.page, PolicyGate(policy, secrets), session.lease, evidence=evidence
    )
    narrow.act(Action(kind="navigate", url=base_url))
    with pytest.raises(PolicyDenied) as caught:
        narrow.act(Action(kind="click", target=LocatorDescriptor(accessible_name="Sign On")))
    assert "click" in str(caught.value)


def test_the_irreversible_control_escalates_and_is_never_clicked(surface, base_url):
    """Close Account is visible to the model and denied by the gate. Nothing is clicked."""
    _reach_detail(surface, base_url)
    with pytest.raises(ConfirmationRequired):
        surface.act(
            Action(kind="click", target=LocatorDescriptor(role="link", visible_text="Close Account"))
        )
    assert "queued" not in surface.page_text()  # the closure screen was never reached


# --- seam 3: the gate is in the driver, not in any layer above it -----------------


def test_calling_act_off_allowlist_on_a_bare_surface_still_raises(session, secrets, evidence):
    """Bypass the orchestrator entirely. A gate in a higher layer would not fire here."""
    bare = WebSurface(
        session.page,
        PolicyGate(Policy(allowlisted_domains=["127.0.0.1"]), secrets),
        session.lease,
        evidence=evidence,
    )
    with pytest.raises(PolicyDenied):
        bare.act(Action(kind="navigate", url="https://evil.example/"))


# --- evidence --------------------------------------------------------------------


def _events(evidence) -> list[dict]:
    return [json.loads(line) for line in evidence.log_path.read_text().splitlines()]


def test_every_action_is_written_to_a_structured_per_run_log(surface, base_url, evidence):
    _sign_on(surface, base_url)
    kinds = [e["action"]["kind"] for e in _events(evidence) if e["event"] == "action"]
    assert kinds == ["navigate", "type", "type", "click"]


def test_a_denial_is_written_to_the_evidence_trail(surface, evidence):
    with pytest.raises(PolicyDenied):
        surface.act(Action(kind="navigate", url="https://example.com/"))
    denials = [e for e in _events(evidence) if e["decision"]["verdict"] == "deny"]
    assert denials and "example.com" in denials[0]["decision"]["reason"]


def test_an_escalation_is_recorded_as_distinct_from_a_denial(surface, base_url, evidence):
    """Escalating and denying are different outcomes, so they are different log records."""
    _reach_detail(surface, base_url)
    with pytest.raises(ConfirmationRequired):
        surface.act(
            Action(kind="click", target=LocatorDescriptor(role="link", visible_text="Close Account"))
        )
    verdicts = [e["decision"]["verdict"] for e in _events(evidence)]
    assert "confirm_required" in verdicts
    assert "deny" not in verdicts
