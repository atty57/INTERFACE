"""What the automation is permitted to do. Configuration, not prompt.

The allowlist bounds *where*; the reversibility patterns bound *what*. Both deny by
default: an action type absent from ``allowed_actions`` is refused, and so is a domain
absent from ``allowlisted_domains``.
"""

from __future__ import annotations

import fnmatch
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

Reversibility = Literal["safe", "risky", "irreversible"]

ORDER: dict[str, int] = {"safe": 0, "risky": 1, "irreversible": 2}

# Named here rather than in the artifact on purpose: the artifact is a file in version
# control that a person can edit, and a guardrail that one word can disable is cosmetic.
DEFAULT_IRREVERSIBLE = [
    "close account",
    "post transaction",
    "wire transfer",
    "delete",
    "purge",
]
DEFAULT_RISKY = ["update", "post", "submit payment", "reverse"]


class Policy(BaseModel):
    allowlisted_domains: list[str] = Field(default_factory=list)
    allowlisted_routes: list[str] = Field(default_factory=lambda: ["/**"])
    allowed_actions: list[str] = Field(
        default_factory=lambda: ["navigate", "click", "type", "select", "extract", "assert", "wait"]
    )
    risky_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_RISKY))
    irreversible_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_IRREVERSIBLE))
    irreversible_routes: list[str] = Field(default_factory=lambda: ["/close*"])
    confirm_mode: Literal["block_irreversible", "allow_all"] = "block_irreversible"

    def domain_allowed(self, url: str) -> bool:
        host = urlparse(url).hostname or ""
        return any(host == d or host.endswith("." + d) for d in self.allowlisted_domains)

    def route_allowed(self, url: str) -> bool:
        path = urlparse(url).path or "/"
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.allowlisted_routes)

    def classify(self, label: str = "", url: str = "") -> Reversibility:
        """Reversibility as *policy* sees it, computed from the control and the route.

        Replay takes the stricter of this and the artifact's own claim, so hand-editing
        an artifact can tighten a step but never loosen one.
        """
        text = (label or "").casefold()
        path = urlparse(url).path or ""
        if any(p in text for p in self.irreversible_patterns) or any(
            fnmatch.fnmatch(path, r) for r in self.irreversible_routes
        ):
            return "irreversible"
        if any(p in text for p in self.risky_patterns):
            return "risky"
        return "safe"


def stricter(a: Reversibility, b: Reversibility) -> Reversibility:
    return a if ORDER[a] >= ORDER[b] else b
