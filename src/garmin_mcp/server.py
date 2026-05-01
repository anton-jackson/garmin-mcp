"""MCP server entrypoint. Streamable HTTP + dual-auth (static bearer or OAuth)."""
from __future__ import annotations

import os
from urllib.parse import urlencode

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import oauth
from .tools import activities, hrv, rhr, training_status

# Permissive transport security: we're behind Cloud Run's HTTPS frontend and gate
# access via our own bearer token middleware, so DNS-rebinding protection is moot.
try:
    from mcp.server.transport_security import TransportSecuritySettings
    _security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )
    mcp = FastMCP("garmin-mcp", transport_security=_security)
except ImportError:
    mcp = FastMCP("garmin-mcp")

activities.register(mcp)
hrv.register(mcp)
rhr.register(mcp)
training_status.register(mcp)


def _server_url() -> str:
    return os.environ.get("SERVER_URL", "https://garmin.antonjackson.com").rstrip("/")


# ── auth middleware ─────────────────────────────────────────────────────────


_PUBLIC_PREFIXES = ("/.well-known/", "/oauth/")
_PUBLIC_PATHS = {"/healthz"}


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Accepts either the static MCP_AUTH_TOKEN or a valid OAuth access token."""

    def __init__(self, app, static_token: str):
        super().__init__(app)
        self._static = static_token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.startswith("Bearer "):
            token = header[7:]
            if token == self._static or oauth.validate_access_token(token):
                return await call_next(request)

        url = _server_url()
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Unauthorized"}, "id": None},
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    f'Bearer realm="{url}", '
                    f'resource_metadata="{url}/.well-known/oauth-protected-resource"'
                )
            },
        )


# ── route handlers ──────────────────────────────────────────────────────────


async def healthz(_request: Request) -> Response:
    return JSONResponse({"ok": True})


async def oauth_authorization_server(_request: Request) -> Response:
    url = _server_url()
    return JSONResponse({
        "issuer": url,
        "authorization_endpoint": f"{url}/oauth/authorize",
        "token_endpoint": f"{url}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": [],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
    })


async def oauth_protected_resource(_request: Request) -> Response:
    url = _server_url()
    return JSONResponse({
        "resource": url,
        "authorization_servers": [url],
    })


async def oauth_authorize(request: Request) -> Response:
    if request.method == "GET":
        params = request.query_params
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        state = params.get("state", "")
        response_type = params.get("response_type")
        if response_type != "code" or not client_id or not redirect_uri:
            return Response("Bad Request: missing required parameters", status_code=400)
        return HTMLResponse(oauth.authorize_page(client_id, redirect_uri, state))

    # POST — consent submission
    form = await request.form()
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    state = form.get("state", "")
    action = form.get("action", "")

    sep = "&" if "?" in redirect_uri else "?"
    if action == "deny":
        qs = urlencode({k: v for k, v in {"error": "access_denied", "state": state}.items() if v})
        return RedirectResponse(f"{redirect_uri}{sep}{qs}", status_code=302)

    code = oauth.generate_auth_code(client_id, redirect_uri, state)
    qs = urlencode({k: v for k, v in {"code": code, "state": state}.items() if v})
    return RedirectResponse(f"{redirect_uri}{sep}{qs}", status_code=302)


async def oauth_token(request: Request) -> Response:
    form = await request.form()
    grant_type = form.get("grant_type", "")
    client_id = form.get("client_id", "")
    client_secret = form.get("client_secret", "")

    if not oauth.validate_client(client_id, client_secret):
        return JSONResponse({"error": "invalid_client"}, status_code=401)

    if grant_type == "authorization_code":
        code = form.get("code", "")
        redirect_uri = form.get("redirect_uri", "")
        if not oauth.redeem_auth_code(code, client_id, redirect_uri):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(oauth.issue_tokens(client_id))

    if grant_type == "refresh_token":
        refresh = form.get("refresh_token", "")
        result = oauth.refresh_tokens(refresh)
        if not result:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(result)

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# ── app wiring ──────────────────────────────────────────────────────────────


def build_app():
    token = os.environ.get("MCP_AUTH_TOKEN")
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN env var is required")

    oauth.init()

    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, static_token=token)

    app.router.routes.extend([
        Route("/healthz", healthz, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", oauth_authorization_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", oauth_protected_resource, methods=["GET"]),
        Route("/oauth/authorize", oauth_authorize, methods=["GET", "POST"]),
        Route("/oauth/token", oauth_token, methods=["POST"]),
    ])
    return app


def main():
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(build_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
