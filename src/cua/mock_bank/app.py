"""Mock core-banking app: frameset, table layout, session cookie, fault injection.

Faults are requested with ``?fault=<name>`` on any route. The name is stored in a
cookie so it survives the frameset's own navigations, which makes every fault
reproducible by hand in a browser: visit ``/?fault=dialog`` and sign on normally.
See ``docs/faults.md``.
"""

import secrets
import time
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import views
from .data import MEMBERS, RESTRICTED, operator_credentials

SESSION_COOKIE = "coresess"
FAULT_COOKIE = "corefault"

FAULTS = (
    "validation",
    "not_found",
    "permission_denied",
    "dialog",
    "dialog_once",
    "timeout",
    "slow",
    "ambiguous",
    "no_names",
)
# Faults that clear themselves once they have fired, so a bounded recovery can succeed.
ONE_SHOT = {"timeout", "dialog_once"}

_live_sessions: set[str] = set()

app = FastAPI(title="ACME Core Banking (mock)")


def _fault(request: Request) -> str:
    """A ?fault= on this request wins; otherwise the cookie set by an earlier one."""
    requested = request.query_params.get("fault")
    if requested is not None:
        return requested if requested in FAULTS else ""
    return request.cookies.get(FAULT_COOKIE, "")


def _authed(request: Request) -> bool:
    return request.cookies.get(SESSION_COOKIE, "") in _live_sessions


def _respond(request: Request, html: str, clear_fault: bool = False) -> Response:
    """Every response applies a pending ?fault= and honours one-shot clearing."""
    resp = HTMLResponse(html)
    requested = request.query_params.get("fault")
    if clear_fault or requested == "none":
        resp.delete_cookie(FAULT_COOKIE, path="/")
    elif requested in FAULTS:
        resp.set_cookie(FAULT_COOKIE, requested, path="/")
    return resp


@app.get("/")
def root(request: Request) -> Response:
    return _respond(request, views.frameset_root())


@app.get("/banner")
def banner(request: Request) -> Response:
    return _respond(request, views.banner())


@app.get("/nav")
def nav(request: Request) -> Response:
    return _respond(request, views.nav())


@app.get("/main")
def main(request: Request) -> Response:
    if not _authed(request):
        return _respond(request, views.signon())
    return _respond(request, views.frameset_main())


@app.post("/signon")
def signon(
    request: Request,
    userid: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> Response:
    user, pwd = operator_credentials()
    if userid != user or password != pwd:
        return _respond(request, views.signon("Sign on failed - check User ID and Password"))
    token = secrets.token_hex(16)
    _live_sessions.add(token)
    resp = RedirectResponse("/main", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, path="/")
    return resp


@app.get("/signoff")
def signoff(request: Request) -> Response:
    _live_sessions.discard(request.cookies.get(SESSION_COOKIE, ""))
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/search")
def search(request: Request) -> Response:
    if not _authed(request):
        return _respond(request, _expired())
    return _respond(request, views.search(strip_names=_fault(request) == "no_names"))


@app.get("/detail")
def detail(request: Request, member_id: str = "") -> Response:
    fault = _fault(request)

    if fault == "slow":
        time.sleep(3.0)

    if fault == "timeout":
        _live_sessions.discard(request.cookies.get(SESSION_COOKIE, ""))
        return _respond(request, _expired(), clear_fault=True)

    if not _authed(request):
        return _respond(request, _expired())

    if fault == "validation" or not member_id.isdigit():
        return _respond(
            request,
            views.search(
                "Enter a valid Member ID - numeric digits only",
                member_id,
                strip_names=fault == "no_names",
            ),
        )

    if fault in ("dialog", "dialog_once"):
        return _respond(request, views.dialog(member_id))

    if fault == "ambiguous":
        # Matches the success checkpoint and a business outcome at the same time.
        return _respond(
            request,
            views.message_page(
                "Member Detail", "No member found for that Member ID", "Index rebuild in progress."
            ),
        )

    if fault == "permission_denied" or member_id in RESTRICTED:
        return _respond(
            request,
            views.message_page(
                "Member Search",
                "You are not authorized to view this record",
                "Contact your branch administrator to request access.",
            ),
        )

    rec = MEMBERS.get(member_id)
    if fault == "not_found" or rec is None:
        return _respond(
            request,
            views.message_page("Member Search", "No member found for that Member ID"),
        )

    return _respond(request, views.detail(member_id, rec, strip_names=fault == "no_names"))


@app.get("/dismiss")
def dismiss(request: Request, member_id: str = "") -> Response:
    """The interstitial's own Continue control. One-shot faults clear here."""
    resp = RedirectResponse(f"/detail?member_id={member_id}", status_code=303)
    if _fault(request) in ONE_SHOT:
        resp.delete_cookie(FAULT_COOKIE, path="/")
    return resp


@app.get("/close")
def close_account(request: Request, member_id: str = "") -> Response:
    """Irreversible. Reachable by a human; the policy gate denies it to automation."""
    if not _authed(request):
        return _respond(request, _expired())
    return _respond(
        request,
        views.message_page(
            "Close Account",
            f"Account closure for member {member_id} has been queued",
            "This action cannot be undone.",
        ),
    )


def _expired() -> str:
    return views.page(
        "Sign On",
        '<table cellpadding="6" border="0"><tr><td><font color="#a00000"><b>'
        "Session has expired - please sign on again</b></font></td></tr></table>"
        + views.signon(),
    )
