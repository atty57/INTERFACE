"""Write-through redaction. Every artifact and evidence write goes through this.

Redaction is applied on the way out rather than at read time, so there is no window in
which a secret exists on disk.
"""

from __future__ import annotations

from typing import Any

MASK = "[redacted]"


class Redactor:
    def __init__(self, values: set[str] | None = None) -> None:
        self.values: set[str] = {v for v in (values or set()) if v}

    def add(self, value: str | None) -> None:
        """Register a value seen at runtime — a pii parameter, say — as sensitive."""
        if value and len(str(value)) >= 3:
            self.values.add(str(value))

    def text(self, value: str) -> str:
        for secret in sorted(self.values, key=len, reverse=True):
            value = value.replace(secret, MASK)
        return value

    def __call__(self, payload: Any) -> Any:
        if isinstance(payload, str):
            return self.text(payload)
        if isinstance(payload, dict):
            return {k: self(v) for k, v in payload.items()}
        if isinstance(payload, (list, tuple)):
            return [self(v) for v in payload]
        return payload
