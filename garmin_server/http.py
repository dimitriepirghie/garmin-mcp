"""Streamable HTTP transport with bearer-token auth (for the Pi / Funnel)."""
from __future__ import annotations

import hmac
import os
import sys
from typing import Any

from .core import mcp


class BearerAuth:
    """ASGI middleware: every HTTP request needs the token.

    Accepted as "Authorization: Bearer <token>". With path_token=True it is
    also accepted as the first path segment (/<token>/mcp), for Claude
    accounts whose connector dialog has no "Request headers" section. The
    segment is stripped before routing, so the access log never shows it.

    Open paths, touching no data: /healthz returns "ok"; /.well-known/*
    returns 404 (no OAuth here).
    """

    def __init__(self, app: Any, token: str, path_token: bool = False) -> None:
        self.app = app
        self.token = token.encode()
        self.path_token = path_token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":  # lifespan events pass through
            await self.app(scope, receive, send)
            return
        if scope["path"] == "/healthz":
            await _reply(send, 200, b"ok")
            return
        if scope["path"].startswith("/.well-known/"):
            # Claude probes OAuth discovery before connecting. A 401 here
            # reads as "OAuth required" and the connector fails to sign in;
            # 404 says this server has no OAuth, so it uses the bearer header.
            await _reply(send, 404, b"not found")
            return
        if self._header_ok(scope) or self._path_ok(scope):
            if scope["path"] == "/":  # connector URL entered without /mcp
                scope["path"], scope["raw_path"] = "/mcp", b"/mcp"
            await self.app(scope, receive, send)
            return
        await _reply(
            send, 401, b"unauthorized", [(b"www-authenticate", b'Bearer realm="garmin-mcp"')]
        )

    def _header_ok(self, scope: dict) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, credential = value.partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(
                    credential.strip(), self.token
                )
        return False

    def _path_ok(self, scope: dict) -> bool:
        if not self.path_token:
            return False
        segment, sep, rest = scope["path"].lstrip("/").partition("/")
        if not sep or not hmac.compare_digest(segment.encode(), self.token):
            return False
        # Rewrite in place: uvicorn's access log reads this same dict.
        scope["path"] = "/" + rest
        scope["raw_path"] = scope["path"].encode()
        return True


async def _reply(send: Any, status: int, body: bytes, extra: list | None = None) -> None:
    headers = [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers + (extra or [])})
    await send({"type": "http.response.body", "body": body})


def run_http() -> None:
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    token = os.environ.get("MCP_BEARER_TOKEN", "").strip()
    if len(token) < 32:
        sys.exit("MCP_BEARER_TOKEN must be set to at least 32 characters.")
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8000"))

    # DNS-rebinding protection checks the Host header against localhost only,
    # which would reject requests arriving through Tailscale Funnel. Rebinding
    # attacks rely on a browser reaching an unauthenticated local server; this
    # one rejects every request without the bearer token, so it is off here.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    try:  # mcp 2.x: options are arguments
        app = mcp.streamable_http_app(
            stateless_http=True, json_response=True, transport_security=security, host=host
        )
    except TypeError:  # mcp 1.x: options are settings
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True
        mcp.settings.transport_security = security
        app = mcp.streamable_http_app()

    path_token = os.environ.get("MCP_PATH_TOKEN", "").lower() in ("1", "true", "yes")
    uvicorn.run(BearerAuth(app, token, path_token), host=host, port=port, log_level="info")


