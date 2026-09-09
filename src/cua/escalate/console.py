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
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from .broker import EscalationBroker

PAGE = """<html><head><title>Operator console</title></head>
<body style="font-family:system-ui;margin:2rem;max-width:60rem">
<h2>Operator console</h2>
{body}
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
<tr><td>Screenshot</td><td>{screenshot_ref}</td></tr>
</table>
<form method="post" action="/operator/{id}/claim" style="display:inline"><button>Claim</button></form>
<form method="post" action="/operator/{id}/done" style="display:inline"><button>Done</button></form>
<form method="post" action="/operator/{id}/abort" style="display:inline"><button>Abort</button></form>
</div>"""

EMPTY = """<p>No open interventions.</p>
<p style="color:#666">Automation raises one here when it stops. The browser window it was
driving stays open; claiming takes the lease so you can drive that same session by hand.</p>"""


def router(broker: EscalationBroker) -> APIRouter:
    api = APIRouter()

    @api.get("/operator", response_class=HTMLResponse)
    def index() -> str:
        requests = broker.open_requests()
        body = "".join(CARD.format(**r.model_dump()) for r in requests) or EMPTY
        return PAGE.format(body=body)

    @api.get("/operator/api")
    def as_json() -> list[dict[str, Any]]:
        return [r.model_dump() for r in broker.open_requests()]

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
