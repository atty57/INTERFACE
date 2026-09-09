"""Wiring for a recording run: session, gate, evidence, discovery loop, recorder, store.

Kept out of ``cli.py`` so the composition is readable and so a test or a script can drive a
recording without going through argument parsing.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from .artifact.models import CapabilityArtifact, Provenance
from .artifact.store import ArtifactStore
from .discover.loop import MODEL, ClaudePlanner, DiscoveryEngine, DiscoveryOutcome, Planner
from .discover.prompts import SYSTEM
from .discover.recorder import Recorder
from .discover.scripted import ScriptedPlanner
from .discover.stuck import Budget
from .evidence.bus import EvidenceBus
from .policy.gate import PolicyGate
from .policy.policy import Policy
from .policy.redact import Redactor
from .policy.secrets import SecretResolver
from .session.broker import SessionBroker
from .surface.web import WebSurface

DEFAULT_SECRET_HANDLES = ["core_operator"]


class RecordingResult(BaseModel):
    outcome: DiscoveryOutcome
    artifact_path: str | None = None
    evidence_ref: str
    capability_id: str | None = None


def record(
    *,
    goal: str,
    target: str,
    capability_id: str,
    version: str = "1.0.0",
    planner_kind: str = "claude",
    headless: bool | None = None,
    budget: Budget | None = None,
    evidence_root: Path | str = "evidence",
    store_root: Path | str = "capabilities",
    secret_handles: list[str] | None = None,
) -> RecordingResult:
    run_id = f"discovery-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    secrets = SecretResolver(secret_handles or DEFAULT_SECRET_HANDLES)
    secrets.check_available()
    evidence = EvidenceBus(run_id, root=evidence_root, redactor=Redactor(secrets.values()))

    host = urlparse(target).hostname or "localhost"
    policy = Policy(allowlisted_domains=[host])
    session = SessionBroker.launch(headless=headless)
    try:
        surface = WebSurface(
            session.page, PolicyGate(policy, secrets), session.lease, evidence=evidence
        )
        planner: Planner = (
            ScriptedPlanner(goal) if planner_kind == "scripted" else ClaudePlanner(SYSTEM)
        )
        recorder = Recorder()
        engine = DiscoveryEngine(surface, planner, recorder, evidence, budget)
        evidence.log("run_started", goal=goal, target=target, planner=planner_kind)
        outcome = engine.run(goal, target)
        evidence.log("run_finished", **outcome.model_dump())

        if outcome.status != "done":
            return RecordingResult(outcome=outcome, evidence_ref=evidence.ref)

        artifact = recorder.finalize(
            goal=goal,
            capability_id=capability_id,
            version=version,
            base_url=target.rstrip("/"),
            final_values=outcome.final_values,
            policy=policy,
            provenance=Provenance(
                recorded_by=MODEL if planner_kind == "claude" else "scripted-planner",
                run_id=run_id,
                trace_ref=evidence.ref,
            ),
        )
        path = ArtifactStore(store_root).save(artifact, redactor=evidence.redactor)
        evidence.write_json("artifact.json", artifact.model_dump())
        return RecordingResult(
            outcome=outcome,
            artifact_path=str(path).replace("\\", "/"),
            evidence_ref=evidence.ref,
            capability_id=artifact.capability_id,
        )
    finally:
        session.close()


def approve(store: ArtifactStore, capability_id: str, version: str) -> CapabilityArtifact:
    """Move a reviewed artifact from draft to approved. There is no workflow, by design."""
    artifact = store.load(capability_id, version)
    approved = artifact.model_copy(update={"approval_state": "approved"})
    store.save(approved)
    return approved
