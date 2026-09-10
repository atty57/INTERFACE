"""The studio: one page to state a goal, watch the agent work, and take over when it stops.

A thin layer. Every route below either starts a run through the same functions the CLI
calls, or reads the run's own evidence back. No orchestration decision is made here.

Bound to 127.0.0.1 with no authentication: this is a local operator surface, not a
deployed product. A real one would sit behind the bank's SSO.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from ..artifact.store import ArtifactStore
from ..catalog.registry import CapabilityCatalog, CapabilityUnavailable
from ..mock_bank.app import FAULTS
from .page import PAGE
from .runner import RunManager


class RecordRequest(BaseModel):
    goal: str
    target: str
    planner: str = "claude"
    model: str = ""


class ReplayRequest(BaseModel):
    capability: str
    params: dict[str, Any] = {}
    fault: str = ""


def build(manager: RunManager, store_root: Path | str = "capabilities") -> FastAPI:
    app = FastAPI(title="Computer-use studio")
    catalog = CapabilityCatalog(ArtifactStore(store_root))
    api = APIRouter()

    @api.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @api.get("/api/capabilities")
    def capabilities() -> list[dict[str, Any]]:
        """Each capability's own typed contract — the form is generated from this."""
        described = []
        for name in catalog.names():
            try:
                described.append(catalog.describe(name))
            except CapabilityUnavailable as refused:
                described.append({"name": name, "unavailable": str(refused)})
        return described

    @api.get("/api/faults")
    def faults() -> list[str]:
        return list(FAULTS)

    @api.post("/api/record")
    def start_record(request: RecordRequest) -> dict[str, str]:
        return {"run_id": manager.submit_record(
            request.goal, request.target, request.planner, request.model
        )}

    @api.post("/api/replay")
    def start_replay(request: ReplayRequest) -> dict[str, str]:
        return {"run_id": manager.submit_replay(
            request.capability, request.params, request.fault
        )}

    @api.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return [manager.runs[i].model_dump() for i in manager.order if i in manager.runs]

    @api.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> JSONResponse:
        run = manager.runs.get(run_id)
        if run is None:
            return JSONResponse({"error": "no such run"}, status_code=404)
        broker = manager.broker(run_id)
        open_requests = [r.model_dump() for r in broker.open_requests()] if broker else []
        return JSONResponse(
            {
                "run": run.model_dump(),
                "events": manager.events(run_id),
                "interventions": open_requests,
                "has_screenshot": manager.latest_screenshot(run_id) is not None,
            }
        )

    @api.get("/api/runs/{run_id}/screenshot")
    def screenshot(run_id: str) -> Response:
        """The most recent masked capture from this run's own evidence directory."""
        shot = manager.latest_screenshot(run_id)
        if shot is None or not shot.is_file():
            return Response(status_code=404)
        return Response(shot.read_bytes(), media_type="image/png")

    @api.post("/api/runs/{run_id}/intervention/{request_id}/{verb}")
    def intervene(run_id: str, request_id: str, verb: str) -> JSONResponse:
        broker = manager.broker(run_id)
        if broker is None or verb not in ("claim", "done", "abort"):
            return JSONResponse({"error": "not available"}, status_code=404)
        outcome = {"claim": broker.claim, "done": broker.done, "abort": broker.abort}[verb](
            request_id
        )
        if verb == "done" and outcome is None:
            # Re-anchoring failed: the screen could not be placed, so control goes back.
            return JSONResponse(
                {"ok": False, "reason": "the current screen could not be recognised"}
            )
        return JSONResponse({"ok": True, "outcome": str(outcome)})

    app.include_router(api)
    return app
