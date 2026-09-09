"""The agent-facing surface: capabilities discoverable and callable by name.

This is the through-line the whole system exists to serve — an AI agent invoking a recorded
capability by name with typed arguments and getting a typed result back. The catalog knows
about artifacts and schemas; it knows nothing about browsers, surfaces, or Playwright.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..artifact.models import CapabilityArtifact
from ..artifact.schema import input_schema, output_schema, tool_definition
from ..artifact.store import ArtifactNotFound, ArtifactStore


class CapabilityUnavailable(Exception):
    """Not resolvable, or not approved. Draft capabilities are refused, never run."""


class Runner(Protocol):
    def __call__(self, capability_id: str, params: dict[str, Any], **kwargs: Any) -> Any: ...


class CapabilityCatalog:
    def __init__(self, store: ArtifactStore | None = None, runner: Runner | None = None) -> None:
        self.store = store or ArtifactStore()
        self._runner = runner

    def names(self) -> list[str]:
        return self.store.capabilities()

    def resolve(self, name: str, version: str | None = None) -> CapabilityArtifact:
        try:
            artifact = self.store.load(name, version)
        except ArtifactNotFound as missing:
            raise CapabilityUnavailable(str(missing)) from missing
        if artifact.approval_state != "approved":
            raise CapabilityUnavailable(
                f"'{name}' is in state '{artifact.approval_state}', not approved"
            )
        return artifact

    def describe(self, name: str, version: str | None = None) -> dict[str, Any]:
        artifact = self.resolve(name, version)
        return {
            "name": artifact.capability_id,
            "version": artifact.version,
            "description": artifact.provenance.goal,
            "input_schema": input_schema(artifact),
            "output_schema": output_schema(artifact),
            "requires_secrets": artifact.requires_secrets,
        }

    def tools(self) -> list[dict[str, Any]]:
        """Every approved capability, in the shape an agent framework tool-calls."""
        definitions = []
        for name in self.names():
            try:
                definitions.append(tool_definition(self.resolve(name)))
            except CapabilityUnavailable:
                continue
        return definitions

    def invoke(self, name: str, params: dict[str, Any], **kwargs: Any) -> Any:
        """Call by name with typed arguments. The runner is injected, so this module has no
        idea a browser is involved."""
        self.resolve(name, kwargs.get("version"))
        if self._runner is not None:
            return self._runner(name, params, **kwargs)
        from ..orchestrator import replay

        return replay(name, params, **kwargs)
