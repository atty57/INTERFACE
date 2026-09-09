"""The through-line: an agent invoking a recorded capability by name with typed arguments."""

from __future__ import annotations

import pytest

from conftest import CAPABILITY
from cua.artifact.store import ArtifactStore
from cua.catalog.registry import CapabilityCatalog, CapabilityUnavailable
from cua.evidence.bus import EvidenceBus
from cua.orchestrator import execute
from cua.policy.redact import Redactor


@pytest.fixture
def catalog():
    return CapabilityCatalog()


def test_a_capability_resolves_by_name(catalog):
    assert catalog.resolve(CAPABILITY).capability_id == CAPABILITY


def test_a_capability_resolves_by_name_and_version(catalog):
    artifact = catalog.resolve(CAPABILITY)
    assert catalog.resolve(CAPABILITY, artifact.version).version == artifact.version


def test_an_unknown_name_is_refused(catalog):
    with pytest.raises(CapabilityUnavailable):
        catalog.resolve("member.close_account")


def test_a_capability_that_is_not_approved_is_refused(tmp_path):
    store = ArtifactStore(tmp_path)
    store.save(ArtifactStore().load(CAPABILITY).model_copy(update={"approval_state": "draft"}))
    with pytest.raises(CapabilityUnavailable) as caught:
        CapabilityCatalog(store).resolve(CAPABILITY)
    assert "not approved" in str(caught.value)


def test_schemas_are_exported_in_a_shape_an_agent_can_tool_call(catalog):
    described = catalog.describe(CAPABILITY)
    schema = described["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["member_id"]
    assert schema["properties"]["member_id"]["pattern"]
    assert schema["additionalProperties"] is False
    assert described["output_schema"]["properties"]["savings"]["type"] == "string"


def test_the_tool_definition_names_the_capability_and_its_inputs(catalog):
    tool = next(t for t in catalog.tools() if t["name"] == "member_read_savings_balance")
    assert tool["description"]
    assert "member_id" in tool["input_schema"]["properties"]


def test_the_calling_contract_carries_no_credential(catalog):
    """Credentials are declared as handles the gate resolves; they are not parameters."""
    described = catalog.describe(CAPABILITY)
    assert described["requires_secrets"] == ["core_operator"]
    assert not set(described["input_schema"]["properties"]) & {"username", "password"}


def test_the_catalog_holds_no_knowledge_of_browsers_or_surfaces():
    """Asserted on the import graph, not on prose: the catalog resolves and describes."""
    import ast
    from pathlib import Path

    from cua.catalog import registry

    tree = ast.parse(Path(registry.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.iter_child_nodes(tree):  # module level only
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not [m for m in imported if "surface" in m or "session" in m or "playwright" in m]


def test_invoking_by_name_with_typed_arguments_returns_typed_outputs(
    session, base_url, tmp_path
):
    """End to end, the way an agent would call it — through the catalog, by name."""
    evidence = EvidenceBus("catalog", root=tmp_path, redactor=Redactor())

    def runner(name, params, **kwargs):
        artifact = ArtifactStore().load(name).model_copy(update={"approval_state": "approved"})
        return execute(artifact, session, params, evidence=evidence, base_url=base_url)

    store = ArtifactStore(tmp_path / "capabilities")
    store.save(ArtifactStore().load(CAPABILITY).model_copy(update={"approval_state": "approved"}))
    catalog = CapabilityCatalog(store, runner=runner)

    result = catalog.invoke(CAPABILITY, {"member_id": "12345"})
    assert result.kind == "success"
    assert result.outputs == {"savings": 4182.55}
