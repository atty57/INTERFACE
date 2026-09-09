"""Credentials the system can use but never store.

A capability declares the secrets it needs by *name*. Values are read from the
environment at the moment of use, so no credential can reach the artifact, the logs, or a
screenshot — the artifact only ever contains the handle's name. The calling agent never
touches one, because credentials are not parameters.
"""

from __future__ import annotations

import os
import re

REFERENCE = re.compile(r"\$\{secret:([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\}")


class SecretUnavailable(Exception):
    """A declared handle has no value in the environment. Fail before acting, not after."""


class SecretResolver:
    """Resolves ``${secret:handle.field}`` from ``CUA_SECRET_<HANDLE>_<FIELD>``."""

    def __init__(self, handles: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self.handles = list(handles or [])
        self._env = env if env is not None else os.environ

    def _lookup(self, handle: str, field: str) -> str:
        key = f"CUA_SECRET_{handle}_{field}".upper()
        value = self._env.get(key)
        if not value:
            raise SecretUnavailable(f"{key} is not set (handle '{handle}', field '{field}')")
        return value

    def resolve(self, text: str) -> str:
        """Substitute every reference. Called at act-time, never at load time."""
        return REFERENCE.sub(lambda m: self._lookup(m.group(1), m.group(2)), text)

    def references(self, text: str) -> list[tuple[str, str]]:
        return [(m.group(1), m.group(2)) for m in REFERENCE.finditer(text)]

    def values(self) -> set[str]:
        """Every value a declared handle can produce, so the redactor can mask them."""
        found: set[str] = set()
        for handle in self.handles:
            prefix = f"CUA_SECRET_{handle}_".upper()
            found.update(v for k, v in self._env.items() if k.upper().startswith(prefix) and v)
        return found

    def check_available(self) -> None:
        for handle in self.handles:
            prefix = f"CUA_SECRET_{handle}_".upper()
            if not any(k.upper().startswith(prefix) for k in self._env):
                raise SecretUnavailable(f"no environment values for secret handle '{handle}'")
