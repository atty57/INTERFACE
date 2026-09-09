"""Read a local ``.env`` into the environment. Git-ignored, so keys stay out of the repo."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path | str = ".env") -> list[str]:
    """Set any KEY=value the environment does not already define. Returns the names set."""
    source = Path(path)
    if not source.exists():
        return []
    loaded = []
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
