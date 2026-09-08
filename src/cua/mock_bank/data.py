"""Synthetic member records. No real people, no real money, no real credentials."""

import os

MEMBERS: dict[str, dict[str, str]] = {
    "12345": {
        "name": "Ada Byron",
        "status": "Active",
        "branch": "0042 - Riverside",
        "checking": "$1,204.18",
        "savings": "$4,182.55",
        "opened": "1994-03-11",
    },
    "54321": {
        "name": "Grace Hopper",
        "status": "Active",
        "branch": "0017 - Harbourside",
        "checking": "$88.00",
        "savings": "$12,004.90",
        "opened": "2001-09-04",
    },
    "77777": {
        "name": "Restricted Record",
        "status": "Sealed",
        "branch": "0001 - Head Office",
        "checking": "$0.00",
        "savings": "$0.00",
        "opened": "1981-01-01",
    },
}

RESTRICTED = {"77777"}


def operator_credentials() -> tuple[str, str]:
    """Synthetic sign-on credential, supplied by environment configuration."""
    return (
        os.environ.get("CUA_OPERATOR_USERNAME", "opsuser"),
        os.environ.get("CUA_OPERATOR_PASSWORD", "synthetic-not-a-real-password"),
    )
