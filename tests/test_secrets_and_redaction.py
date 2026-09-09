"""Credentials the system can use but never store."""

from __future__ import annotations

import pytest

from cua.evidence.bus import EvidenceBus
from cua.evidence.shots import MASK_PAGE, RESTORE_PAGE
from cua.policy.gate import PolicyGate
from cua.policy.policy import Policy
from cua.policy.redact import Redactor
from cua.policy.secrets import SecretResolver, SecretUnavailable
from cua.surface.base import Action, LocatorDescriptor
from cua.surface.web import WebSurface


@pytest.fixture
def secrets(credentials, monkeypatch):
    user, password = credentials
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_USERNAME", user)
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_PASSWORD", password)
    return SecretResolver(["core_operator"])


@pytest.fixture
def evidence(tmp_path, secrets):
    return EvidenceBus("secrets-run", root=tmp_path, redactor=Redactor(secrets.values()))


@pytest.fixture
def surface(session, secrets, evidence):
    return WebSurface(
        session.page,
        PolicyGate(Policy(allowlisted_domains=["127.0.0.1"]), secrets),
        session.lease,
        evidence=evidence,
    )


def _sign_on(surface, base_url):
    surface.act(Action(kind="navigate", url=base_url))
    for name, ref in (
        ("User ID", "${secret:core_operator.username}"),
        ("Password", "${secret:core_operator.password}"),
    ):
        surface.act(
            Action(
                kind="type",
                target=LocatorDescriptor(role="textbox", accessible_name=name),
                value=ref,
            )
        )
    surface.act(
        Action(kind="click", target=LocatorDescriptor(role="button", accessible_name="Sign On"))
    )
    surface.page.wait_for_timeout(400)


def test_a_missing_handle_fails_loudly_rather_than_typing_nothing():
    resolver = SecretResolver(["core_operator"], env={})
    with pytest.raises(SecretUnavailable):
        resolver.resolve("${secret:core_operator.password}")


def test_a_scripted_sign_on_completes_using_a_handle(surface, base_url):
    _sign_on(surface, base_url)
    assert "Member Search" in surface.page_text()


def test_no_secret_value_appears_anywhere_beneath_the_evidence_tree(
    surface, base_url, evidence, credentials
):
    _sign_on(surface, base_url)
    evidence.screenshot(surface.page, "after-signon")
    _, password = credentials
    for path in evidence.dir.rglob("*"):
        if path.is_file():
            assert password.encode() not in path.read_bytes(), path


def test_the_log_records_the_handle_name_rather_than_the_value(surface, base_url, evidence):
    _sign_on(surface, base_url)
    body = evidence.log_path.read_text()
    assert "${secret:core_operator.password}" in body


def test_a_pii_value_registered_at_runtime_is_masked_in_the_log(evidence):
    evidence.redactor.add("12345678")
    evidence.log("action", value="member 12345678 looked up")
    assert "12345678" not in evidence.log_path.read_text()
    assert "[redacted]" in evidence.log_path.read_text()


def test_flagged_values_are_masked_in_the_page_before_capture_and_restored_after(
    surface, base_url, credentials
):
    """The masking mechanism itself, asserted on the DOM — a PNG cannot be asserted on."""
    _, password = credentials
    surface.act(Action(kind="navigate", url=base_url))
    surface.act(
        Action(
            kind="type",
            target=LocatorDescriptor(role="textbox", accessible_name="Password"),
            value="${secret:core_operator.password}",
        )
    )
    frame = surface.page.frame(name="main")
    assert frame.eval_on_selector('input[type="password"]', "el => el.value") == password

    frame.evaluate(MASK_PAGE, [password])
    assert frame.eval_on_selector('input[type="password"]', "el => el.value") == "••••"

    frame.evaluate(RESTORE_PAGE)
    assert frame.eval_on_selector('input[type="password"]', "el => el.value") == password


def test_a_masked_screenshot_is_written_and_the_page_still_works_afterwards(
    surface, base_url, evidence
):
    _sign_on(surface, base_url)
    shot = evidence.screenshot(surface.page, "member-search")
    assert shot is not None and shot.exists() and shot.stat().st_size > 0
    assert "Member Search" in surface.page_text()
