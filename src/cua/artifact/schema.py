"""JSON Schema export, so a calling agent can tool-call a capability without an adapter.

One Pydantic definition gives validation, serialisation and this export; the catalog
serves it straight through.
"""

from __future__ import annotations

from typing import Any

from .models import CapabilityArtifact

JSON_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "money": "string",
}


def input_schema(artifact: CapabilityArtifact) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for param in artifact.input_params:
        spec: dict[str, Any] = {"type": JSON_TYPES[param.type]}
        if param.pattern:
            spec["pattern"] = param.pattern
        if param.description:
            spec["description"] = param.description
        properties[param.name] = spec
    return {
        "type": "object",
        "properties": properties,
        "required": [p.name for p in artifact.input_params if p.required],
        "additionalProperties": False,
    }


def output_schema(artifact: CapabilityArtifact) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {o.name: {"type": JSON_TYPES[o.type]} for o in artifact.outputs},
    }


def tool_definition(artifact: CapabilityArtifact) -> dict[str, Any]:
    """The shape an agent framework wants: a name, a description, an input schema."""
    return {
        "name": artifact.capability_id.replace(".", "_"),
        "description": artifact.provenance.goal
        or f"{artifact.capability_id} v{artifact.version}",
        "input_schema": input_schema(artifact),
    }
