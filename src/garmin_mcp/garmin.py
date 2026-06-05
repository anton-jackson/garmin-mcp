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


# Garmin profile path that returns the account's displayName (and userName).
_SOCIAL_PROFILE = "/userprofile-service/socialProfile"


def _ensure_display_name(client: Garmin) -> None:
    """Guarantee ``client.display_name`` is populated before the client is used.

    Some garminconnect versions don't set ``display_name`` on the token-based
    login path (the "tokenstore path may not populate it" fix is only in newer
    releases). When it's unset, get_rhr_day/get_hrv_data raise "Display name is
    not set" while every other endpoint keeps working, because those two are the
    only ones that build their URL via the display name. Fetch it explicitly so
    the result is the same regardless of garminconnect version or login path.
    """
    if getattr(client, "display_name", None):
        return
    try:
        prof = client.connectapi(_SOCIAL_PROFILE)
    except Exception:
        return
    if isinstance(prof, dict):
        client.display_name = prof.get("displayName") or prof.get("userName")


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
        _ensure_display_name(client)
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
    # A fresh login with return_on_mfa=True returns before garminconnect loads
    # the social profile, so display_name is unset here too.
    _ensure_display_name(client)
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
    _ensure_display_name(client)
    _client = client
    _pending_mfa = None
    return True
