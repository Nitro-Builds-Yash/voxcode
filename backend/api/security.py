"""Protects the local API from other websites and (optionally) other users.

* Cross-site requests: a page on any website can make your browser send
  requests to http://localhost:8000. Requests carrying an Origin header that
  is neither the server's own origin nor in ALLOWED_ORIGINS are rejected.
  Clients without a browser (VS Code extension, curl) send no Origin.
* API_TOKEN: when set, every request must carry `X-VoxCode-Token`
  (WebSockets may use `?token=`).
"""

from __future__ import annotations

import hmac
import json
from urllib.parse import parse_qs

from backend.config.settings import settings

_PUBLIC_PATHS = ("/health", "/static/", "/favicon.ico")
_PUBLIC_EXACT = {"/"}


class LocalSecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        path = scope.get("path", "")

        origin = headers.get("origin")
        if origin and not _origin_allowed(origin, headers.get("host", ""), scope):
            return await _deny(scope, receive, send, 403, f"Origin {origin} is not allowed")

        if settings.API_TOKEN and not (path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PATHS)):
            token = headers.get("x-voxcode-token", "")
            if not token and scope["type"] == "websocket":
                token = parse_qs(scope.get("query_string", b"").decode()).get("token", [""])[0]
            if not hmac.compare_digest(token, settings.API_TOKEN):
                return await _deny(scope, receive, send, 401, "Missing or invalid X-VoxCode-Token")

        return await self.app(scope, receive, send)


def _origin_allowed(origin: str, host: str, scope) -> bool:
    if origin in settings.allowed_origins:
        return True
    scheme = "https" if scope.get("scheme") in ("https", "wss") else "http"
    return origin == f"{scheme}://{host}"


async def _deny(scope, receive, send, status: int, message: str):
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4403 if status == 403 else 4401})
        return
    body = json.dumps({"detail": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
