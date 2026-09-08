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
