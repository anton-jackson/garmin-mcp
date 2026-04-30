"""Authenticated garminconnect client, lazily initialized and shared per process."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

from .session_store import default_store

_lock = threading.Lock()
_client: Garmin | None = None
_pending_mfa: dict | None = None  # {"client": Garmin, "result": <mfa state>} when MFA required


class NeedsMFA(Exception):
    """Raised when the Garmin login flow needs an MFA code from the user."""


def _build_client() -> Garmin:
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]
    token_dir = Path(os.environ.get("GARMIN_SESSION_DIR", "/tmp/garminconnect"))
    token_dir.mkdir(parents=True, exist_ok=True)

    store = default_store()
    if store is not None:
        store.pull()

    client = Garmin(email=email, password=password, return_on_mfa=True)

    # Try cached session first.
    try:
        client.login(str(token_dir))
        return client
    except (FileNotFoundError, GarminConnectAuthenticationError):
        pass

    result = client.login()
    if isinstance(result, tuple) and result and result[0] == "needs_mfa":
        global _pending_mfa
        _pending_mfa = {"client": client, "state": result[1]}
        raise NeedsMFA()

    client.garth.dump(str(token_dir))
    if store is not None:
        store.push()
    return client


def get_client() -> Garmin:
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            _client = _build_client()
        return _client


def submit_mfa(code: str) -> bool:
    """Resume a login that returned needs_mfa. Returns True on success."""
    global _client, _pending_mfa
    if _pending_mfa is None:
        return False
    client = _pending_mfa["client"]
    state = _pending_mfa["state"]
    client.resume_login(state, code)
    token_dir = Path(os.environ.get("GARMIN_SESSION_DIR", "/tmp/garminconnect"))
    client.garth.dump(str(token_dir))
    store = default_store()
    if store is not None:
        store.push()
    _client = client
    _pending_mfa = None
    return True
