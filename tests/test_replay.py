"""Seam 1: the orchestrator, in process, against a real browser and the real mock app.

Every test here drives the system the way a caller does and asserts on the returned result
union or on files written to disk. The artifact under test is the committed one — the same
file a reviewer replays on a clean clone — rebound to the test server's port.
"""

from __future__ import annotations

import json

from conftest import CAPABILITY
from cua.artifact.store import ArtifactStore
from cua.orchestrator import execute, replay, validate_params


def run(artifact, session, base_url, evidence, params, **kwargs):
    return execute(artifact, session, params, evidence=evidence, base_url=base_url, **kwargs)


# --- the happy path --------------------------------------------------------------


def test_replay_returns_success_with_the_declared_typed_output(
    artifact, session, base_url, evidence
):
    result = run(artifact, session, base_url, evidence, {"member_id": "12345"})
    assert result.kind == "success"
    assert result.outputs == {"savings": 4182.55}


def test_replay_reports_which_targeting_signal_resolved_each_step(
    artifact, session, base_url, evidence
):
    result = run(artifact, session, base_url, evidence, {"member_id": "12345"})
    assert result.tiers_used["s1"] == 1  # role + accessible name
    assert set(result.tiers_used) >= {"s1", "s2", "s3", "s4", "s5"}


def test_the_same_capability_works_for_a_different_member(
    artifact, session, base_url, evidence
):
    """The generalization pass is what makes this true; a macro would only replay 12345."""
    result = run(artifact, session, base_url, evidence, {"member_id": "54321"})
    assert result.kind == "success"
    assert result.outputs == {"savings": 12004.90}


def test_replay_runs_with_no_model_api_key_present(
    artifact, session, base_url, evidence, monkeypatch
):
    """Every model key, not just one: .env is loaded for all commands, replay included."""
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert run(artifact, session, base_url, evidence, {"member_id": "12345"}).kind == "success"


def test_a_structured_step_log_is_written(artifact, session, base_url, evidence):
    run(artifact, session, base_url, evidence, {"member_id": "12345"})
    events = {e["event"] for e in evidence.read()}
    assert {"replay_started", "action", "classified", "replay_finished"} <= events


def test_the_pii_parameter_is_masked_in_the_evidence_trail(
    artifact, session, base_url, evidence
):
    run(artifact, session, base_url, evidence, {"member_id": "54321"})
    for path in evidence.dir.rglob("*"):
        if path.is_file() and path.suffix in (".jsonl", ".json"):
            assert "54321" not in path.read_text()


# --- validation and resolution, before a session opens -----------------------------


def test_a_parameter_failing_its_pattern_is_rejected(committed_artifact):
    assert validate_params(committed_artifact, {"member_id": "abc"}) == [
        "'member_id' does not match ^\\d{3,9}$"
    ]


def test_a_missing_required_parameter_is_rejected(committed_artifact):
    assert "missing required parameter 'member_id'" in validate_params(committed_artifact, {})


def test_an_unknown_parameter_is_rejected(committed_artifact):
    assert "unknown parameter 'sort_code'" in validate_params(
        committed_artifact, {"member_id": "12345", "sort_code": "00"}
    )


def test_invalid_input_is_refused_without_opening_a_browser(tmp_path, base_url):
    """If this opened a session it would be slow; that it is instant is the assertion."""
    result = replay(
        CAPABILITY,
        {"member_id": "not-a-number"},
        base_url=base_url,
        evidence_root=tmp_path,
        headless=True,
    )
    assert result.kind == "failure"
    assert result.failure_class == "invalid_input"


def test_a_draft_capability_is_refused_at_replay(tmp_path, base_url, committed_artifact):
    store = ArtifactStore(tmp_path / "capabilities")
    store.save(committed_artifact.model_copy(update={"approval_state": "draft"}))
    result = replay(
        CAPABILITY,
        {"member_id": "12345"},
        store_root=tmp_path / "capabilities",
        base_url=base_url,
        evidence_root=tmp_path,
        headless=True,
    )
    assert result.kind == "failure"
    assert result.failure_class == "capability_unavailable"
    assert "not approved" in result.observed


def test_an_unknown_capability_is_refused(tmp_path, base_url):
    result = replay(
        "member.wire_transfer", {}, store_root=tmp_path, evidence_root=tmp_path, base_url=base_url
    )
    assert result.failure_class == "capability_unavailable"


# --- I1: no model on the production path -------------------------------------------


MODEL_MODULES = ("anthropic", "openai", "cua.discover")


def test_no_model_client_is_reachable_from_the_replay_import_graph():
    """Structural, not procedural. Walk what replay imports and look for a model client.

    ``cua.discover`` is in the list because this repo's OpenRouter client is written on
    ``urllib``: a check that only looked for an SDK package name would not see it.
    """
    import importlib
    import sys

    for name in list(sys.modules):
        if name.startswith(MODEL_MODULES):
            del sys.modules[name]

    for module in ("cua.replay.engine", "cua.replay.detectors", "cua.replay.outcomes"):
        importlib.import_module(module)

    smuggled = [name for name in sys.modules if name.startswith(MODEL_MODULES)]
    assert smuggled == [], f"a model client is reachable from replay: {smuggled}"


def test_the_replay_package_names_no_model_module_in_its_source():
    from pathlib import Path

    import cua.replay as package

    for path in Path(package.__file__).parent.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        for forbidden in ("import anthropic", "from anthropic", "import openai", "discover"):
            assert forbidden not in body, f"{path.name} names {forbidden!r}"


# --- the command line shell ---------------------------------------------------------


def test_the_cli_prints_a_result_a_caller_could_parse(
    capsys, tmp_path, base_url, artifact
):
    from cua.cli import main

    store = ArtifactStore(tmp_path / "capabilities")
    store.save(artifact)
    code = main(
        [
            "replay",
            "--capability",
            CAPABILITY,
            "--params",
            json.dumps({"member_id": "12345"}),
            "--base-url",
            base_url,
            "--evidence-root",
            str(tmp_path / "evidence"),
            "--store-root",
            str(tmp_path / "capabilities"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["kind"] == "success"
    assert payload["outputs"]["savings"] == 4182.55
