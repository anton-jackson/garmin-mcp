"""Self-hosted OAuth 2.0 server for MCP clients (claude.ai web/mobile).

A tiny in-process IdP modeled on the strava-mcp pattern: one hardcoded client,
auth-code flow, opaque bearer tokens. Tokens persist to GCS so Cloud Run cold
starts don't invalidate active sessions.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from html import escape
from typing import Optional

from google.cloud import storage

CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "garmin-mcp")
_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET")

ACCESS_TOKEN_TTL_S = 90 * 24 * 60 * 60  # 90 days
AUTH_CODE_TTL_S = 5 * 60

_TOKENS_BLOB = "oauth-tokens.json"


class _TokenStore:
    """JSON token store backed by a GCS blob. One blob per server."""

    def __init__(self, bucket: str | None):
        self._bucket = bucket
        self._client: storage.Client | None = None
        self._lock = threading.Lock()
        self._data: dict = {"access_tokens": {}, "refresh_tokens": {}}
        self._loaded = False

    def _gcs(self) -> storage.Client:
        if self._client is None:
            self._client = storage.Client()
        return self._client

    def _blob(self):
        return self._gcs().bucket(self._bucket).blob(_TOKENS_BLOB)

    def load(self) -> None:
        if not self._bucket:
            self._loaded = True
            return
        try:
            raw = self._blob().download_as_text()
            self._data = json.loads(raw)
            self._data.setdefault("access_tokens", {})
            self._data.setdefault("refresh_tokens", {})
        except Exception:
            # Missing blob or first run — start fresh.
            self._data = {"access_tokens": {}, "refresh_tokens": {}}
        self._loaded = True

    def _save(self) -> None:
        if not self._bucket:
            return
        self._blob().upload_from_string(
            json.dumps(self._data), content_type="application/json"
        )

    def issue(self, client_id: str) -> tuple[str, str]:
        access = secrets.token_hex(32)
        refresh = secrets.token_hex(32)
        with self._lock:
            self._data["access_tokens"][access] = {
                "expires_at": int(time.time()) + ACCESS_TOKEN_TTL_S,
                "client_id": client_id,
            }
            self._data["refresh_tokens"][refresh] = {"client_id": client_id}
            self._save()
        return access, refresh

    def refresh(self, refresh_token: str) -> Optional[str]:
        with self._lock:
            entry = self._data["refresh_tokens"].get(refresh_token)
            if not entry:
                return None
            access = secrets.token_hex(32)
            self._data["access_tokens"][access] = {
                "expires_at": int(time.time()) + ACCESS_TOKEN_TTL_S,
                "client_id": entry["client_id"],
            }
            self._save()
        return access

    def validate(self, access_token: str) -> bool:
        with self._lock:
            entry = self._data["access_tokens"].get(access_token)
            if not entry:
                return False
            if entry["expires_at"] < int(time.time()):
                self._data["access_tokens"].pop(access_token, None)
                self._save()
                return False
        return True


_store = _TokenStore(os.environ.get("GARMIN_SESSION_BUCKET"))


def init() -> None:
    """Load tokens from GCS at startup. Call once from server boot."""
    _store.load()


# ── auth codes (in-memory, 5min TTL) ────────────────────────────────────────

_auth_codes: dict[str, dict] = {}
_codes_lock = threading.Lock()


def generate_auth_code(client_id: str, redirect_uri: str, state: str) -> str:
    code = secrets.token_hex(32)
    with _codes_lock:
        _auth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "expires_at": int(time.time()) + AUTH_CODE_TTL_S,
        }
    return code


def redeem_auth_code(code: str, client_id: str, redirect_uri: str) -> bool:
    with _codes_lock:
        entry = _auth_codes.pop(code, None)
    if not entry:
        return False
    if entry["expires_at"] < int(time.time()):
        return False
    if entry["client_id"] != client_id or entry["redirect_uri"] != redirect_uri:
        return False
    return True


# ── client + token validation ───────────────────────────────────────────────


def validate_client(client_id: str, client_secret: str) -> bool:
    if not _CLIENT_SECRET:
        return False
    return (
        client_id == CLIENT_ID
        and secrets.compare_digest(client_secret, _CLIENT_SECRET)
    )


def issue_tokens(client_id: str) -> dict:
    access, refresh = _store.issue(client_id)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_S,
    }


def refresh_tokens(refresh_token: str) -> Optional[dict]:
    access = _store.refresh(refresh_token)
    if not access:
        return None
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_S,
    }


def validate_access_token(token: str) -> bool:
    return _store.validate(token)


# ── consent page ────────────────────────────────────────────────────────────


def authorize_page(client_id: str, redirect_uri: str, state: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Authorize – Garmin MCP</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f5f5f5; display: flex; align-items: center; justify-content: center;
           min-height: 100vh; padding: 1rem; }}
    .card {{ background: white; border-radius: 12px; padding: 2rem; max-width: 400px;
            width: 100%; box-shadow: 0 2px 16px rgba(0,0,0,0.1); text-align: center; }}
    .icon {{ font-size: 3rem; margin-bottom: 1rem; }}
    h1 {{ font-size: 1.4rem; font-weight: 600; margin-bottom: 0.5rem; color: #111; }}
    p {{ color: #555; font-size: 0.95rem; margin-bottom: 1.5rem; }}
    .client {{ font-family: monospace; background: #f0f0f0; padding: 2px 6px;
              border-radius: 4px; font-size: 0.9rem; }}
    button {{ width: 100%; padding: 0.85rem; border: none; border-radius: 8px;
             font-size: 1rem; font-weight: 600; cursor: pointer; transition: opacity 0.15s; }}
    .authorize {{ background: #007cc3; color: white; }}
    .authorize:hover {{ opacity: 0.9; }}
    .deny {{ background: #eee; color: #333; margin-top: 0.75rem; }}
    .deny:hover {{ background: #ddd; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">⌚</div>
    <h1>Authorize Garmin MCP</h1>
    <p><span class="client">{escape(client_id)}</span> is requesting access to your Garmin data.</p>
    <form method="POST" action="/oauth/authorize">
      <input type="hidden" name="client_id" value="{escape(client_id)}">
      <input type="hidden" name="redirect_uri" value="{escape(redirect_uri)}">
      <input type="hidden" name="state" value="{escape(state)}">
      <button type="submit" name="action" value="authorize" class="authorize">Authorize</button>
      <button type="submit" name="action" value="deny" class="deny">Deny</button>
    </form>
  </div>
</body>
</html>"""
