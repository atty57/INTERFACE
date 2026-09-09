# STUB — design seam only. Not implemented: authentication, multi-operator queues,
# co-browsing. See REPORT §7. The mechanism beneath it — lease transitions, event capture,
# re-anchor — is real, and a production console plugs into it unchanged.
"""A minimal operator console: open interventions, and claim / done / abort.

It runs in the replaying process, because the lease it moves belongs to that process's
live browser session. A real console would talk to a broker over the network; the three
verbs and the context bundle would be identical.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .broker import EscalationBroker

PAGE = """<html><head><title>Operator console</title>
<meta http-equiv="refresh" content="3"></head>
<body style="font-family:system-ui;margin:2rem;max-width:64rem;color:#111">
<h2>Operator console</h2>
{body}
<h3>What the agent has been doing</h3>
<p style="color:#666;margin-top:-.5rem">The live screen is the Chrome window this run is
driving. Claiming takes the lease so you can drive that same window by hand.</p>
{feed}
</body></html>"""

CARD = """<div style="border:1px solid #ccc;padding:1rem;margin-bottom:1rem">
<b>{capability_id}</b> &mdash; step <code>{step_id}</code> &mdash; state <b>{state}</b>
<p><i>{goal}</i></p>
<table cellpadding="4">
<tr><td>Why stopped</td><td>{why_stopped}</td></tr>
<tr><td>Expected</td><td><code>{expected}</code></td></tr>
<tr><td>Observed</td><td><code>{observed}</code></td></tr>
<tr><td>Last good checkpoint</td><td>{last_good_checkpoint}</td></tr>
<tr><td>Proposed action</td><td>{proposed_action}</td></tr>
<tr><td>Parameters</td><td>{params_summary}</td></tr>
</table>
<p style="margin:.5rem 0 0"><b>Where it stopped</b></p>
<img src="/operator/{id}/screenshot" alt="the screen where automation stopped"
     style="max-width:100%;border:1px solid #ddd">
<form method="post" action="/operator/{id}/claim" style="display:inline"><button>Claim</button></form>
<form method="post" action="/operator/{id}/done" style="display:inline"><button>Done</button></form>
<form method="post" action="/operator/{id}/abort" style="display:inline"><button>Abort</button></form>
</div>"""

EMPTY = """<p>No open interventions. Automation raises one here when it stops.</p>"""

FEED = """<table cellpadding="5" cellspacing="0" border="1"
style="border-collapse:collapse;font-size:.85rem;width:100%">
<tr style="background:#eee"><th align="left">Step</th><th align="left">What happened</th>
<th align="left">Detail</th></tr>
{rows}
</table>"""

FEED_EMPTY = """<p style="color:#666">Nothing recorded yet.</p>"""

# The events an operator actually needs to follow the flow. The rest of the log is for
# whoever debugs the run afterwards.
INTERESTING = {
    "action": lambda e: (
        f"{e.get('action', {}).get('kind', '')} {e.get('label') or ''}".strip()
        + f" [{e.get('decision', {}).get('verdict', '')}]"
        + (f" tier {e['tier']}" if e.get("tier") else "")
    ),
    "classified": lambda e: f"{e.get('kind')}: {', '.join(e.get('matched') or []) or 'nothing'}",
    "recovery_attempt": lambda e: f"{e.get('strategy')} (attempt {e.get('attempt')})",
    "recovery_exhausted": lambda e: f"{e.get('signature')} gave up after {e.get('cap')}",
    "policy_denied": lambda e: str(e.get("reason", "")),
    "escalation_required": lambda e: str(e.get("reason", "")),
    "intervention_raised": lambda e: str(e.get("why_stopped", "")),
    "lease_claimed": lambda e: "a human took control",
    "human_action": lambda e: f"human {e.get('kind')}: {e.get('target', '')}",
    "re_anchor_failed": lambda e: "handback refused: the screen could not be placed",
    "resumed_after_handoff": lambda e: f"resumed ({e.get('resolution')})",
    "replay_finished": lambda e: str((e.get("result") or {}).get("kind", "")),
}


def _escape(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _feed(broker: EscalationBroker, limit: int = 40) -> str:
    """The agent's recent flow, read from the same evidence the audit trail is built on."""
    rows = []
    for event in broker.evidence.read()[-limit:]:
        describe = INTERESTING.get(str(event.get("event")))
        if describe is None:
            continue
        try:
            detail = describe(event)
        except Exception:  # noqa: BLE001 - a malformed record must not break the console
            detail = ""
        rows.append(
            "<tr><td><code>{step}</code></td><td>{event}</td><td>{detail}</td></tr>".format(
                step=_escape(event.get("step") or event.get("step_id") or ""),
                event=_escape(event.get("event")),
                detail=_escape(detail),
            )
        )
    return FEED.format(rows="".join(rows)) if rows else FEED_EMPTY


def router(broker: EscalationBroker) -> APIRouter:
    api = APIRouter()

    @api.get("/operator", response_class=HTMLResponse)
    def index() -> str:
        requests = broker.open_requests()
        cards = [CARD.format(**{k: _escape(v) for k, v in r.model_dump().items()})
                 for r in requests]
        return PAGE.format(body="".join(cards) or EMPTY, feed=_feed(broker))

    @api.get("/operator/api")
    def as_json() -> list[dict[str, Any]]:
        return [r.model_dump() for r in broker.open_requests()]

    @api.get("/operator/{request_id}/screenshot")
    def screenshot(request_id: str) -> Response:
        """Serve the masked capture taken where automation stopped."""
        found = broker.find(request_id)
        if found is None or not found.screenshot_ref:
            return Response(status_code=404)
        path = Path(found.screenshot_ref)
        # Only ever serve the masked shot this request already points at, and only from
        # inside the run's own evidence directory.
        try:
            path.resolve().relative_to(broker.evidence.dir.resolve())
        except ValueError:
            return Response(status_code=404)
        if path.suffix != ".png" or not path.is_file():
            return Response(status_code=404)
        return Response(path.read_bytes(), media_type="image/png")

    @api.post("/operator/{request_id}/claim")
    def claim(request_id: str) -> RedirectResponse:
        broker.claim(request_id)
        return RedirectResponse("/operator", status_code=303)

    @api.post("/operator/{request_id}/done")
    def done(request_id: str) -> RedirectResponse:
        broker.done(request_id)
        return RedirectResponse("/operator", status_code=303)

    @api.post("/operator/{request_id}/abort")
    def abort(request_id: str) -> RedirectResponse:
        broker.abort(request_id)
        return RedirectResponse("/operator", status_code=303)

    return api


def serve_in_thread(broker: EscalationBroker, port: int) -> threading.Thread:
    """Run the console alongside the replay it belongs to."""
    import uvicorn

    app = FastAPI(title="Operator console")
    app.include_router(router(broker))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return thread
