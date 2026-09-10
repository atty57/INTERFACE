"""Runs started from the browser, executed one at a time on a single worker thread.

Serial execution is not a shortcut. Playwright's sync API binds its event loop to the
thread that created it, and — more to the point — there is one visible browser window and
one control lease. Two concurrent runs would have to fight over both.

Nothing here decides anything about a run: it calls the same ``record()`` and ``replay()``
the CLI calls, and reads the same ``steps.jsonl`` the audit trail is built on.
"""

from __future__ import annotations

import datetime as dt
import queue
import threading
import traceback
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..escalate.broker import EscalationBroker
from ..evidence.bus import EvidenceBus

Status = Literal["queued", "running", "awaiting_operator", "finished", "failed"]


class Run(BaseModel):
    """What the page needs to render one run. The detail lives in the evidence bus."""

    id: str
    kind: Literal["record", "replay"]
    label: str
    status: Status = "queued"
    result: dict[str, Any] | None = None
    error: str = ""
    started_at: str = Field(default_factory=lambda: dt.datetime.now().astimezone().strftime("%H:%M:%S"))
    evidence_ref: str = ""


class RunManager:
    def __init__(
        self,
        evidence_root: Path | str = "evidence",
        headless: bool = False,
        start_worker: bool = True,
    ) -> None:
        self.evidence_root = Path(evidence_root)
        self.headless = headless
        self.runs: dict[str, Run] = {}
        self.order: list[str] = []
        self._jobs: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._brokers: dict[str, EscalationBroker] = {}
        self._lock = threading.Lock()
        # A test of the HTTP shell wants the queueing without a browser behind it.
        self._worker = threading.Thread(target=self._drain, daemon=True)
        if start_worker:
            self._worker.start()

    # --- starting work ---------------------------------------------------------------

    def submit_record(self, goal: str, target: str, planner: str, model: str = "") -> str:
        run_id = self._new_id("record")
        self.runs[run_id] = Run(id=run_id, kind="record", label=goal)
        self.order.insert(0, run_id)
        self._jobs.put((run_id, lambda: self._do_record(run_id, goal, target, planner, model)))
        return run_id

    def submit_replay(
        self, capability: str, params: dict[str, Any], fault: str = ""
    ) -> str:
        run_id = self._new_id("replay")
        label = f"{capability} {params}" + (f" --fault {fault}" if fault else "")
        self.runs[run_id] = Run(id=run_id, kind="replay", label=label)
        self.order.insert(0, run_id)
        self._jobs.put((run_id, lambda: self._do_replay(run_id, capability, params, fault)))
        return run_id

    # --- the worker ------------------------------------------------------------------

    def _drain(self) -> None:
        while True:
            run_id, job = self._jobs.get()
            run = self.runs[run_id]
            run.status = "running"
            try:
                job()
            except Exception as failure:  # noqa: BLE001 - a failed run must not kill the worker
                run.status = "failed"
                run.error = f"{type(failure).__name__}: {failure}"
                traceback.print_exc()
            finally:
                if run.status not in ("failed",):
                    run.status = "finished"
                self._jobs.task_done()

    def _do_record(self, run_id: str, goal: str, target: str, planner: str, model: str) -> None:
        from ..record import record

        run = self.runs[run_id]
        evidence = self._bus(run_id)
        run.evidence_ref = evidence.ref
        result = record(
            evidence=evidence,
            goal=goal,
            target=target.rstrip("/"),
            capability_id=_capability_id_for(goal),
            planner_kind=planner,
            model=model,
            headless=self.headless,
            evidence_root=self.evidence_root,
        )
        run.result = result.model_dump()

    def _do_replay(
        self, run_id: str, capability: str, params: dict[str, Any], fault: str
    ) -> None:
        from ..orchestrator import replay

        run = self.runs[run_id]
        evidence = self._bus(run_id)
        run.evidence_ref = evidence.ref
        broker = EscalationBroker(evidence, wait_timeout_s=900.0, keep_open=True)
        with self._lock:
            self._brokers[run_id] = broker

        # The page needs to know the moment a request opens, so it can offer the operator
        # the three verbs while the run is genuinely paused.
        original = broker.raise_intervention

        def watched(*args: Any, **kwargs: Any) -> Any:
            run.status = "awaiting_operator"
            try:
                return original(*args, **kwargs)
            finally:
                run.status = "running"

        broker.raise_intervention = watched  # type: ignore[method-assign]

        result = replay(
            capability,
            params,
            headless=self.headless,
            evidence_root=self.evidence_root,
            escalation=broker,
            fault=fault or None,
            evidence=evidence,
        )
        run.result = result.model_dump()

    # --- reading back ----------------------------------------------------------------

    def _bus(self, run_id: str) -> EvidenceBus:
        from ..policy.redact import Redactor

        return EvidenceBus(run_id, root=self.evidence_root, redactor=Redactor())

    def broker(self, run_id: str) -> EscalationBroker | None:
        return self._brokers.get(run_id)

    def events(self, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        run = self.runs.get(run_id)
        if run is None:
            return []
        return self._bus(run_id).read()[-limit:]

    def latest_screenshot(self, run_id: str) -> Path | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        directory = self.evidence_root / run_id
        shots = sorted((directory / "shots").glob("*.png"), key=lambda p: p.stat().st_mtime)
        return shots[-1] if shots else None

    def _new_id(self, kind: str) -> str:
        stamp = dt.datetime.now().astimezone().strftime("%H%M%S")
        return f"{kind}-{stamp}-{len(self.order)}"


def _capability_id_for(goal: str) -> str:
    """A recording needs a name before anyone has reviewed it; the goal is the best guess."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in goal).split() if len(w) > 2]
    return "adhoc." + ("_".join(words[:4]).lower() or "capability")
