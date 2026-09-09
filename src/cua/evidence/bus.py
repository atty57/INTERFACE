"""Append-only, redacted, per-run evidence. Cross-cutting, and never on the critical path.

Every plane writes here and nothing reads back from it at runtime, so a failure to write
evidence degrades the audit trail rather than failing the run. Denied and blocked actions
are written too: a denial is the guardrail working, and it belongs in the trail.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..policy.redact import Redactor


class EvidenceBus:
    def __init__(
        self, run_id: str, root: Path | str = "evidence", redactor: Redactor | None = None
    ) -> None:
        self.run_id = run_id
        self.dir = Path(root) / run_id
        self.log_path = self.dir / "steps.jsonl"
        self.redactor = redactor or Redactor()

    def log(self, event: str, **fields: Any) -> None:
        record = self.redactor({"ts": time.time(), "event": event, **fields})
        try:
            # Created on first write, so a run that writes nothing leaves no directory.
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError:  # pragma: no cover - evidence degrades, never blocks
            pass

    def read(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def screenshot(self, page: Any, name: str) -> Path | None:
        from .shots import masked_screenshot

        try:
            return masked_screenshot(
                page, self.dir / "shots" / f"{name}.png", sorted(self.redactor.values)
            )
        except Exception:  # noqa: BLE001 - pragma: no cover
            return None

    def write_text(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.redactor.text(text), encoding="utf-8")
        return path

    def write_json(self, name: str, payload: Any) -> Path:
        return self.write_text(name, json.dumps(self.redactor(payload), indent=2, default=str))

    @property
    def ref(self) -> str:
        return str(self.dir).replace("\\", "/")
