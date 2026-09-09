"""The commands a reviewer types. Argument parsing and printing over the calls below it."""

from __future__ import annotations

import argparse
import json
import sys


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .mock_bank.app import app

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


def _digest(args: argparse.Namespace) -> int:
    """Print the digest for a URL, for eyeballing what the model would see."""
    from .session.broker import SessionBroker
    from .surface.web import WebPerception

    session = SessionBroker.launch(headless=not args.headful)
    try:
        session.page.goto(args.url)
        session.page.wait_for_timeout(args.settle_ms)
        print(WebPerception(session.page).snapshot().render())
    finally:
        session.close()
    return 0


def _record(args: argparse.Namespace) -> int:
    from .record import record

    result = record(
        goal=args.goal,
        target=args.target.rstrip("/"),
        capability_id=args.capability,
        version=args.artifact_version,
        planner_kind=args.planner,
        headless=not args.headful,
    )
    emit(result.model_dump())
    return 0 if result.outcome.status == "done" else 1


def _approve(args: argparse.Namespace) -> int:
    from .artifact.store import ArtifactStore
    from .record import approve

    artifact = approve(ArtifactStore(), args.capability, args.artifact_version)
    emit({"capability_id": artifact.capability_id, "approval_state": artifact.approval_state})
    return 0


def _replay(args: argparse.Namespace) -> int:
    from .escalate.broker import EscalationBroker
    from .escalate.console import serve_in_thread
    from .evidence.bus import EvidenceBus
    from .orchestrator import replay

    params = json.loads(args.params)
    escalation = None
    if args.console_port:
        escalation = EscalationBroker(
            EvidenceBus("console", root=args.evidence_root),
            wait_timeout_s=args.escalation_timeout,
            keep_open=True,
        )
        serve_in_thread(escalation, args.console_port)
        print(f"operator console: http://127.0.0.1:{args.console_port}/operator", file=sys.stderr)

    result = replay(
        args.capability,
        params,
        version=args.artifact_version,
        store_root=args.store_root,
        base_url=args.base_url,
        fault=args.fault,
        headless=not args.headful,
        evidence_root=args.evidence_root,
        escalation=escalation,
    )
    emit(result.model_dump())
    return 0 if result.kind != "failure" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cua", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the mock core-banking app")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_serve)

    digest = sub.add_parser("digest", help="print the element digest for a URL")
    digest.add_argument("url")
    digest.add_argument("--settle-ms", type=int, default=500)
    digest.add_argument("--headful", action="store_true")
    digest.set_defaults(func=_digest)

    rec = sub.add_parser("record", help="record a capability from a goal and a target")
    rec.add_argument("--goal", required=True)
    rec.add_argument("--target", required=True)
    rec.add_argument("--capability", default="member.read_savings_balance")
    rec.add_argument("--artifact-version", default="1.0.0")
    rec.add_argument(
        "--planner",
        choices=["claude", "scripted"],
        default="claude",
        help="'claude' needs ANTHROPIC_API_KEY; 'scripted' reproduces a recording without one",
    )
    rec.add_argument("--headful", action="store_true", default=True)
    rec.add_argument("--headless", dest="headful", action="store_false")
    rec.set_defaults(func=_record)

    rep = sub.add_parser("replay", help="replay a capability with typed parameters")
    rep.add_argument("--capability", required=True)
    rep.add_argument("--params", default="{}", help="JSON object of input parameters")
    rep.add_argument("--artifact-version", default=None)
    rep.add_argument("--base-url", default=None, help="override the artifact's recorded host")
    rep.add_argument("--fault", default=None, help="arm a mock-app fault; see docs/faults.md")
    rep.add_argument("--evidence-root", default="evidence")
    rep.add_argument("--store-root", default="capabilities")
    rep.add_argument("--console-port", type=int, default=0, help="serve the operator console")
    rep.add_argument("--escalation-timeout", type=float, default=120.0)
    rep.add_argument("--headful", action="store_true")
    rep.set_defaults(func=_replay)

    app = sub.add_parser("approve", help="move a reviewed artifact from draft to approved")
    app.add_argument("--capability", required=True)
    app.add_argument("--artifact-version", required=True)
    app.set_defaults(func=_approve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


def emit(payload: dict[str, object]) -> None:
    """Structured output a caller could parse, which is the point of a result union."""
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
