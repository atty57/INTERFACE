"""Telling three different things apart: an answer, something worth retrying, a fault.

Conflating the first with the third is the most expensive mistake available here.
"""

from __future__ import annotations

import pytest

from cua.artifact.models import Checkpoint
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
    return EvidenceBus("classify", root=tmp_path, redactor=Redactor())


@pytest.fixture(autouse=True)
def _credentials(credentials, monkeypatch):
    user, password = credentials
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_USERNAME", user)
    monkeypatch.setenv("CUA_SECRET_CORE_OPERATOR_PASSWORD", password)


def run(artifact, session, base_url, evidence, params=None, **kwargs):
    return execute(
        artifact,
        session,
        params or {"member_id": "12345"},
        evidence=evidence,
        base_url=base_url,
        **kwargs,
    )


# --- arm two: answers, not errors ------------------------------------------------


def test_an_unknown_member_is_a_business_outcome_not_a_failure(
    artifact, session, base_url, evidence
):
    result = run(artifact, session, base_url, evidence, {"member_id": "99999"})
    assert result.kind == "business_outcome"
    assert result.name == "record_not_found"
    assert result.severity == "info"
    assert result.message


def test_a_permission_denial_is_a_business_outcome(artifact, session, base_url, evidence):
    result = run(artifact, session, base_url, evidence, {"member_id": "77777"})
    assert result.kind == "business_outcome"
    assert result.name == "permission_denied"
    assert result.severity == "warn"


def test_the_applications_own_validation_error_is_a_business_outcome(
    artifact, session, base_url, evidence
):
    result = run(artifact, session, base_url, evidence, fault="validation")
    assert result.kind == "business_outcome"
    assert result.name == "invalid_member_id"


def test_a_business_outcome_names_the_step_it_happened_on(
    artifact, session, base_url, evidence
):
    assert run(artifact, session, base_url, evidence, {"member_id": "99999"}).step_id == "s5"


# --- arm three: faults ------------------------------------------------------------


def test_two_detectors_matching_at_once_is_a_hard_failure(
    artifact, session, base_url, evidence
):
    """A sequential checker would call this a success: 'Member Detail' is on the page."""
    result = run(artifact, session, base_url, evidence, fault="ambiguous")
    assert result.kind == "failure"
    assert result.failure_class == "ambiguous_state"
    assert "record_not_found" in result.observed


def test_nothing_matching_before_the_timeout_is_a_hard_failure(
    artifact, session, base_url, evidence
):
    never = artifact.model_copy(
        update={
            "success_checkpoint": Checkpoint(
                kind="text", matcher="Wire Transfer Complete", timeout_ms=3000
            )
        }
    )
    result = run(never, session, base_url, evidence)
    assert result.kind == "failure"
    assert result.failure_class == "checkpoint_missed"


def test_a_failure_names_the_step_the_expectation_the_observation_and_the_evidence(
    artifact, session, base_url, evidence
):
    result = run(artifact, session, base_url, evidence, fault="ambiguous")
    assert result.step_id == "s5"
    assert "Member Detail" in result.expected
    assert result.observed
    assert result.evidence_ref.endswith("classify")


def test_failure_classes_are_distinguishable_by_the_caller(
    artifact, session, base_url, evidence, tmp_path
):
    ambiguous = run(artifact, session, base_url, evidence, fault="ambiguous")
    never = run(
        artifact.model_copy(
            update={
                "success_checkpoint": Checkpoint(
                    kind="text", matcher="Nothing Here", timeout_ms=2000
                )
            }
        ),
        session,
        base_url,
        EvidenceBus("classify-2", root=tmp_path, redactor=Redactor()),
    )
    assert {ambiguous.failure_class, never.failure_class} == {
        "ambiguous_state",
        "checkpoint_missed",
    }


# --- a slow page is not a failure --------------------------------------------------


def test_a_slow_load_is_waited_out_rather_than_misread_as_a_failure(
    artifact, session, base_url, evidence
):
    """Sequential evaluation would misclassify this; the race waits on expected state."""
    result = run(artifact, session, base_url, evidence, fault="slow")
    assert result.kind == "success"
    assert result.outputs == {"savings": 4182.55}
