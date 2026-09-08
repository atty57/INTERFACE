"""Smoke test for the test fixture itself: the mock app's screens and every fault.

The mock is the fixture the rest of the suite drives a real browser against, so this
checks the fixture behaves, not that the system works.
"""

import pytest
from fastapi.testclient import TestClient

from cua.mock_bank import app as mock
from cua.mock_bank.data import operator_credentials


@pytest.fixture
def client() -> TestClient:
    c = TestClient(mock.app)
    user, pwd = operator_credentials()
    c.post("/signon", data={"userid": user, "password": pwd})
    return c


def test_frameset_nests_content_inside_main():
    c = TestClient(mock.app)
    assert 'name="main"' in c.get("/").text
    user, pwd = operator_credentials()
    c.post("/signon", data={"userid": user, "password": pwd})
    assert 'name="content"' in c.get("/main").text


def test_signon_rejects_a_wrong_credential():
    c = TestClient(mock.app)
    assert "Sign on failed" in c.post("/signon", data={"userid": "x", "password": "y"}).text


def test_unauthenticated_search_is_refused():
    assert "Operator Sign On" in TestClient(mock.app).get("/search").text


def test_known_member_reaches_detail_with_a_savings_balance(client):
    body = client.get("/detail", params={"member_id": "12345"}).text
    assert "Member Detail" in body
    assert "$4,182.55" in body
    assert "Close Account" in body


def test_unknown_member_is_not_found(client):
    assert "No member found" in client.get("/detail", params={"member_id": "99999"}).text


def test_markup_carries_no_automation_affordances(client):
    body = client.get("/detail", params={"member_id": "12345"}).text
    for affordance in (" id=", " class=", "data-test", "data-", "aria-label"):
        assert affordance not in body


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("validation", "Enter a valid Member ID"),
        ("not_found", "No member found"),
        ("permission_denied", "not authorized"),
        ("dialog", 'role="dialog"'),
        ("dialog_once", 'role="dialog"'),
        ("timeout", "Session has expired"),
        ("ambiguous", "Member Detail"),
    ],
)
def test_each_fault_forces_its_condition(client, fault, expected):
    assert expected in client.get("/detail", params={"member_id": "12345", "fault": fault}).text


def test_ambiguous_fault_matches_two_detectors_at_once(client):
    body = client.get("/detail", params={"member_id": "12345", "fault": "ambiguous"}).text
    assert "Member Detail" in body and "No member found" in body


def test_no_names_fault_strips_accessible_names(client):
    body = client.get("/detail", params={"member_id": "12345", "fault": "no_names"}).text
    assert "title=" not in body
    assert "Savings" in body  # anchor text survives, so weaker signals still work


def test_slow_fault_is_slow_but_arrives(client):
    import time

    start = time.monotonic()
    body = client.get("/detail", params={"member_id": "12345", "fault": "slow"}).text
    assert time.monotonic() - start >= 2.5
    assert "Member Detail" in body


def test_timeout_fault_really_invalidates_the_session(client):
    client.get("/detail", params={"member_id": "12345", "fault": "timeout"})
    assert "Session has expired" in client.get("/search").text


def test_timeout_is_one_shot_so_re_login_can_recover(client):
    client.get("/detail", params={"member_id": "12345", "fault": "timeout"})
    user, pwd = operator_credentials()
    client.post("/signon", data={"userid": user, "password": pwd})
    assert "Member Detail" in client.get("/detail", params={"member_id": "12345"}).text


def test_dismissing_a_one_shot_dialog_clears_it(client):
    client.get("/detail", params={"member_id": "12345", "fault": "dialog_once"})
    assert "Member Detail" in client.get("/dismiss", params={"member_id": "12345"}).text


def test_a_sticky_dialog_survives_dismissal(client):
    client.get("/detail", params={"member_id": "12345", "fault": "dialog"})
    assert 'role="dialog"' in client.get("/dismiss", params={"member_id": "12345"}).text
