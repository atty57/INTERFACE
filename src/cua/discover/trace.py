"""One verified action, as the recorder saw it. The unit the generalization pass works on."""

from __future__ import annotations

from pydantic import BaseModel

from ..artifact.models import Checkpoint
from ..surface.base import Action


class RecordedAction(BaseModel):
    """An action that reached a verified checkpoint. Nothing else is ever recorded."""

    action: Action
    label: str = ""
    checkpoint: Checkpoint | None = None
    url_before: str = ""
    url_after: str = ""
    tier: int | None = None
