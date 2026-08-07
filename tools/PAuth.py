"""Local Python 3 compatible PAuth shim.

This module provides the legacy attributes used by older PDT scripts:

    PAuth.user
    PAuth.pw

Set LOCAL_JIRA_USER / LOCAL_JIRA_PASSWORD below if you want this local
Python 3 PAuth.py file to be the direct source of credentials.

If those constants are left blank, it falls back to:

    JIRA_USER / JIRA_PASSWORD
    LDAP_USER / LDAP_PASSWORD

A small getAuthFile()/cleanupAuthFile() compatibility layer is included for
legacy Orbit code paths that expect PAuth to create a temporary auth file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv should exist in app env
    load_dotenv = None  # type: ignore


_BASE_DIR = Path(__file__).resolve().parent
_ENV_PATH = _BASE_DIR / ".env"

# Direct local credentials for Python 3 PAuth usage.
# Fill these only if you do not want to use .env/environment variables.
LOCAL_JIRA_USER = ""
LOCAL_JIRA_PASSWORD = ""

if load_dotenv is not None:
    load_dotenv(str(_ENV_PATH), override=False)


user = (
    LOCAL_JIRA_USER
    or os.getenv("JIRA_USER", "")
    or os.getenv("LDAP_USER", "")
    or ""
).strip()

pw = (
    LOCAL_JIRA_PASSWORD
    or os.getenv("JIRA_PASSWORD", "")
    or os.getenv("LDAP_PASSWORD", "")
    or ""
).strip()

_auth_file_path: Optional[str] = None


def getAuthFile() -> str:
    """Create a temporary auth file for legacy Orbit API callers.

    The old Python 2 PAuth module exposed this function. The Orbit API expects
    a path to a file containing username/password credentials. This helper
    preserves that interface using the local env-backed credentials.
    """
    global _auth_file_path

    if not user or not pw:
        raise RuntimeError(
            "PAuth credentials missing. Set LOCAL_JIRA_USER/LOCAL_JIRA_PASSWORD "
            "in PAuth.py, or set JIRA_USER/JIRA_PASSWORD or "
            "LDAP_USER/LDAP_PASSWORD in .env/environment."
        )

    fd, path = tempfile.mkstemp(prefix="pauth_", suffix=".txt")
    with os.fdopen(fd, "w") as handle:
        handle.write(user + "\n")
        handle.write(pw + "\n")

    _auth_file_path = path
    return path


def cleanupAuthFile() -> None:
    """Delete the temporary auth file created by getAuthFile()."""
    global _auth_file_path

    if _auth_file_path and os.path.exists(_auth_file_path):
        try:
            os.remove(_auth_file_path)
        except OSError:
            pass
    _auth_file_path = None
