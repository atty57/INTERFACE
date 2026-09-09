"""The generalization pass — what turns a run into a capability.

A recording repeats what happened; a capability accepts parameters and returns typed data.
Skip this pass and you have a macro, and macros do not compose into an agent's tool
catalog.

Pure functions, no I/O, no model. Tested directly against a fixed action trace, because a
subtle bug here produces an artifact that still replays correctly against the data it was
recorded on and fails on every other input — a failure invisible from the outside.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel

from ..artifact.models import Checkpoint, InputParam, Output, Sensitivity
from ..policy.secrets import REFERENCE
from ..surface.base import Anchor, LocatorDescriptor
from .trace import RecordedAction

CURRENCY = re.compile(r"^[-+]?[$£€]\s?[\d,]+(\.\d{2})?$")
PII_HINTS = ("id", "ssn", "account", "member", "customer", "tax", "dob", "birth")


class Generalized(BaseModel):
    trace: list[RecordedAction]
    input_params: list[InputParam]
    outputs: list[Output]
    requires_secrets: list[str]


def snake(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    return cleaned or "value"


def infer_pattern(value: str) -> str | None:
    if value.isdigit():
        return rf"^\d{{{max(1, len(value) - 2)},{len(value) + 4}}}$"
    return None


def sensitivity_for(label: str) -> Sensitivity:
    return "pii" if any(hint in label.casefold() for hint in PII_HINTS) else "public"


def generalize(
    trace: list[RecordedAction],
    goal: str,
    final_values: dict[str, str],
    base_url: str,
) -> Generalized:
    """Literals the operator supplied become parameters; read values become outputs."""
    params: dict[str, InputParam] = {}
    literal_to_param: dict[str, str] = {}
    secrets: list[str] = []

    for recorded in trace:
        value = recorded.action.value
        if not value:
            continue
        references = REFERENCE.findall(value)
        if references:
            # Credentials become named handles, never values, and never parameters.
            for handle, _field in references:
                if handle not in secrets:
                    secrets.append(handle)
            continue
        if recorded.action.kind != "type":
            continue
        name = snake(recorded.label or "value")
        params.setdefault(
            name,
            InputParam(
                name=name,
                type="string",
                pattern=infer_pattern(value),
                required=True,
                sensitivity=sensitivity_for(recorded.label),
                description=f"Value supplied for '{recorded.label}' during recording.",
            ),
        )
        literal_to_param[value] = name

    rewritten = [_rewrite(r, literal_to_param, base_url) for r in trace]
    return Generalized(
        trace=rewritten,
        input_params=list(params.values()),
        outputs=_outputs(goal, final_values),
        requires_secrets=secrets,
    )


def _rewrite(
    recorded: RecordedAction, literal_to_param: dict[str, str], base_url: str
) -> RecordedAction:
    copy = recorded.model_copy(deep=True)
    if copy.action.value in literal_to_param:
        copy.action.value = "${" + literal_to_param[copy.action.value] + "}"
    if copy.action.url:
        copy.action.url = generalize_route(copy.action.url, literal_to_param, base_url)
    if copy.checkpoint is not None:
        copy.checkpoint = _rewrite_checkpoint(copy.checkpoint, literal_to_param)
    return copy


def generalize_route(url: str, literal_to_param: dict[str, str], base_url: str = "") -> str:
    """``/member/12345`` becomes ``/member/:member_id`` — the artifact is not bound to the
    record-time record."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    path = "/".join(
        ":" + literal_to_param[s] if s in literal_to_param else s for s in path.split("/")
    )
    query = parsed.query
    for literal, name in literal_to_param.items():
        query = query.replace(f"={literal}", "=${" + name + "}")
    # Always relative: the host lives in ``base_url``, which is the one thing a tenant
    # overlay is allowed to rebind.
    return path + (f"?{query}" if query else "")


def _rewrite_checkpoint(checkpoint: Checkpoint, literal_to_param: dict[str, str]) -> Checkpoint:
    matcher = checkpoint.matcher
    for literal, name in literal_to_param.items():
        matcher = matcher.replace(literal, "${" + name + "}")
    return checkpoint.model_copy(update={"matcher": matcher})


def _outputs(goal: str, final_values: dict[str, str]) -> list[Output]:
    """Values read from the final screen become typed outputs — the ones the goal asked for."""
    words = set(re.findall(r"[a-z]+", goal.casefold()))
    outputs = []
    for label, value in final_values.items():
        if not words & set(re.findall(r"[a-z]+", label.casefold())):
            continue
        money = bool(CURRENCY.match(value.strip()))
        outputs.append(
            Output(
                name=snake(label),
                type="money" if money else "string",
                locator=LocatorDescriptor(
                    role="cell", anchor=Anchor(stable_text=label, relation="same_row")
                ),
                transform="parse_currency" if money else "strip",
            )
        )
    return outputs
