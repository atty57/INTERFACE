"""A smoke test for the studio's HTTP shell.

Like the CLI, this layer is argument parsing and serialization over calls already covered
at seam 1. What is worth asserting here is that it starts the right work and hands the page
the artifact's own contract — not that replay works, which is tested properly elsewhere.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import CAPABILITY
from cua.studio.app import build
from cua.studio.runner import RunManager


@pytest.fixture
def client(tmp_path):
    # No worker: these tests assert what gets queued, not what a browser then does.
    manager = RunManager(evidence_root=tmp_path, start_worker=False)
    return TestClient(build(manager)), manager


def test_the_page_is_served(client):
    page = client[0].get("/").text
    assert "Computer-use studio" in page
    assert "Run a goal" in page and "Invoke a capability" in page


def test_the_form_is_generated_from_the_capabilitys_own_schema(client):
    """The types the page renders are the artifact's, not the page's."""
    described = client[0].get("/api/capabilities").json()
    capability = next(c for c in described if c["name"] == CAPABILITY)
    schema = capability["input_schema"]
    assert schema["properties"]["member_id"]["pattern"] == r"^\d{3,9}$"
    assert schema["required"] == ["member_id"]


def test_a_goal_and_a_url_queue_a_discovery_run(client):
    api, manager = client
    run_id = api.post(
        "/api/record",
        json={"goal": "read the savings balance", "target": "http://127.0.0.1:8000",
              "planner": "scripted"},
    ).json()["run_id"]
    assert manager.runs[run_id].kind == "record"
    assert manager.runs[run_id].status == "queued"
    assert "savings" in manager.runs[run_id].label


def test_typed_arguments_queue_a_replay(client):
    api, manager = client
    run_id = api.post(
        "/api/replay",
        json={"capability": CAPABILITY, "params": {"member_id": "12345"}},
    ).json()["run_id"]
    assert manager.runs[run_id].kind == "replay"
    assert api.get("/api/runs").json()[0]["id"] == run_id


def test_the_injectable_conditions_are_offered(client):
    faults = client[0].get("/api/faults").json()
    assert {"not_found", "permission_denied", "dialog", "timeout"} <= set(faults)


def test_an_unknown_run_is_a_404(client):
    assert client[0].get("/api/runs/nope").status_code == 404
    assert client[0].get("/api/runs/nope/screenshot").status_code == 404


def test_intervening_on_a_run_with_no_broker_is_refused(client):
    api, _ = client
    run_id = api.post(
        "/api/replay", json={"capability": CAPABILITY, "params": {"member_id": "12345"}}
    ).json()["run_id"]
    assert api.post(f"/api/runs/{run_id}/intervention/esc-1/claim").status_code == 404


def test_a_run_reports_its_own_evidence_and_events(client):
    api, _ = client
    run_id = api.post(
        "/api/replay", json={"capability": CAPABILITY, "params": {"member_id": "12345"}}
    ).json()["run_id"]
    body = api.get(f"/api/runs/{run_id}").json()
    assert body["run"]["id"] == run_id
    assert body["events"] == []  # nothing has run yet
    assert body["interventions"] == []
