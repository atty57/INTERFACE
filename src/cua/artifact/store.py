"""Artifacts are JSON files in the repository, versioned by filename.

Git is the review mechanism. A schema change appears as a pull-request diff, which is a
stronger answer to "reviewable" than a database would be — and committing the artifact is
what lets replay reproduce on a clean clone with no credentials.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..policy.redact import Redactor
from .models import CapabilityArtifact

DEFAULT_ROOT = Path("capabilities")


class ArtifactNotFound(Exception):
    pass


class ArtifactStore:
    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def path_for(self, capability_id: str, version: str) -> Path:
        return self.root / capability_id / f"{version}.json"

    def versions(self, capability_id: str) -> list[str]:
        directory = self.root / capability_id
        if not directory.is_dir():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))

    def capabilities(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def load(self, capability_id: str, version: str | None = None) -> CapabilityArtifact:
        if version is None:
            available = self.versions(capability_id)
            if not available:
                raise ArtifactNotFound(f"no versions of '{capability_id}' in {self.root}")
            version = available[-1]
        path = self.path_for(capability_id, version)
        if not path.exists():
            raise ArtifactNotFound(f"{capability_id} {version} not found at {path}")
        return CapabilityArtifact.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, artifact: CapabilityArtifact, redactor: Redactor | None = None) -> Path:
        """Write-through redaction, so no secret can exist on disk even momentarily."""
        path = self.path_for(artifact.capability_id, artifact.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = artifact.to_json()
        if redactor is not None:
            body = redactor.text(body)
        path.write_text(body + "\n", encoding="utf-8")
        return path
