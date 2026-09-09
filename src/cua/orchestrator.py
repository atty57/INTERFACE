"""The run orchestrator: resolve, validate, open a session, pick an engine, assemble a result.

It owns the run lifecycle and nothing else — no UI logic, no model calls. Validation happens
before a session is opened, so a malformed call never touches the target application.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .artifact.models import CapabilityArtifact
from .artifact.store import ArtifactNotFound, ArtifactStore
from .escalate.broker import EscalationBroker
from .evidence.bus import EvidenceBus
from .policy.gate import PolicyGate
from .policy.policy import DEFAULT_IRREVERSIBLE, Policy
from .policy.redact import Redactor
from .policy.secrets import SecretResolver, SecretUnavailable
from .replay.engine import ReplayEngine
from .replay.outcomes import Failure, RunResult
from .session.broker import BrowserSession, SessionBroker
from .surface.web import WebSurface


def validate_params(artifact: CapabilityArtifact, params: dict[str, Any]) -> list[str]:
    """Arguments are validated before anything runs, so a malformed call fails immediately."""
    problems: list[str] = []
    declared = {p.name for p in artifact.input_params}
    for unknown in sorted(set(params) - declared):
        problems.append(f"unknown parameter '{unknown}'")
    for param in artifact.input_params:
        if param.name not in params:
            if param.required:
                problems.append(f"missing required parameter '{param.name}'")
            continue
        value = params[param.name]
        if param.type in ("integer", "number") and not isinstance(value, (int, float)):
            problems.append(f"'{param.name}' must be a {param.type}")
            continue
        if param.type == "boolean" and not isinstance(value, bool):
            problems.append(f"'{param.name}' must be a boolean")
            continue
        if param.pattern and not re.match(param.pattern, str(value)):
            problems.append(f"'{param.name}' does not match {param.pattern}")
    return problems


def effective_policy(artifact: CapabilityArtifact, base_url: str) -> Policy:
    """The artifact may tighten the deployment's policy. It can never widen it.

    The reversibility patterns and the block-irreversible mode come from policy, not from
    the file, so hand-editing an artifact cannot disable the guardrail. The artifact's
    allowlist is intersected with the deployment's, never unioned.
    """
    deployment_domains = {urlparse(base_url).hostname or "localhost"}
    declared = set(artifact.safety.allowlisted_domains) or deployment_domains
    return Policy(
        allowlisted_domains=sorted(declared & deployment_domains) or sorted(deployment_domains),
        allowlisted_routes=list(artifact.safety.allowlisted_routes),
        allowed_actions=list(artifact.safety.allowed_actions),
        irreversible_patterns=DEFAULT_IRREVERSIBLE
        + [p for p in artifact.safety.risky_actions if p not in DEFAULT_IRREVERSIBLE],
        confirm_mode="block_irreversible",
    )


def execute(
    artifact: CapabilityArtifact,
    session: BrowserSession,
    params: dict[str, Any],
    *,
    evidence: EvidenceBus,
    base_url: str | None = None,
    escalation: EscalationBroker | None = None,
    fault: str | None = None,
) -> RunResult:
    """Run a resolved, validated capability against an already-open session."""
    target = (base_url or artifact.base_url).rstrip("/")
    policy = effective_policy(artifact, target)
    secrets = SecretResolver(artifact.requires_secrets)
    evidence.redactor.values.update(secrets.values())
    surface = WebSurface(session.page, PolicyGate(policy, secrets), session.lease, evidence=evidence)

    if escalation is not None:
        escalation.bind_evidence(evidence)

    if fault:
        # Arms the mock application's fault injection. Gated and logged like any navigation.
        from .surface.base import Action

        surface.act(Action(kind="navigate", url=f"{target}/?fault={fault}"))

    engine = ReplayEngine(
        artifact,
        surface,
        evidence,
        params,
        base_url=target,
        escalator=escalation.escalator(session, surface, params) if escalation else None,
    )
    return engine.run()


def replay(
    capability_id: str,
    params: dict[str, Any],
    *,
    version: str | None = None,
    store_root: Path | str = "capabilities",
    base_url: str | None = None,
    fault: str | None = None,
    headless: bool | None = None,
    evidence_root: Path | str = "evidence",
    escalation: EscalationBroker | None = None,
    label: str = "",
) -> RunResult:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    run_id = f"replay-{label}-{stamp}" if label else f"replay-{stamp}"
    evidence = EvidenceBus(run_id, root=evidence_root, redactor=Redactor())

    try:
        artifact = ArtifactStore(store_root).load(capability_id, version)
    except ArtifactNotFound as missing:
        return _refuse("capability_unavailable", str(missing), run_id, evidence)

    if artifact.approval_state != "approved":
        return _refuse(
            "capability_unavailable",
            f"'{capability_id}' is in state '{artifact.approval_state}', not approved",
            run_id,
            evidence,
        )

    problems = validate_params(artifact, params)
    if problems:
        # No session is opened, so a malformed call never reaches the target application.
        return _refuse("invalid_input", "; ".join(problems), run_id, evidence)

    try:
        SecretResolver(artifact.requires_secrets).check_available()
    except SecretUnavailable as missing:
        return _refuse("invalid_input", str(missing), run_id, evidence)

    session = SessionBroker.launch(headless=headless)
    try:
        return execute(
            artifact,
            session,
            params,
            evidence=evidence,
            base_url=base_url,
            escalation=escalation,
            fault=fault,
        )
    finally:
        if escalation is None or not escalation.keep_open:
            session.close()


def _refuse(failure_class: Any, observed: str, run_id: str, evidence: EvidenceBus) -> Failure:
    evidence.log("refused", failure_class=failure_class, observed=observed)
    return Failure(
        failure_class=failure_class,
        expected="a resolvable, approved capability with valid arguments",
        observed=observed,
        run_id=run_id,
        evidence_ref=evidence.ref,
    )
