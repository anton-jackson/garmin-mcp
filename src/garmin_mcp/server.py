"""MCP server entrypoint. Streamable HTTP + bearer-token auth, registers all tools."""
from __future__ import annotations

import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .tools import activities, hrv, sleep

mcp = FastMCP("garmin-mcp")

activities.register(mcp)
sleep.register(mcp)
hrv.register(mcp)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self._expected = f"Bearer {token}"

    async def dispatch(self, request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        if request.headers.get("authorization") != self._expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app():
    token = os.environ.get("MCP_AUTH_TOKEN")
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN env var is required")
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=token)

    async def healthz(_request):
        return JSONResponse({"ok": True})

    app.add_route("/healthz", healthz)
    return app


def main():
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(build_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
