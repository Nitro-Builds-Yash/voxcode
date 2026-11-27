"""Protects the local API from other websites and (optionally) other users.

* DNS rebinding: a website can point its own domain at 127.0.0.1, so its
  pages become "same origin" with this server. Requests must therefore be
  addressed to localhost / 127.0.0.1 / ::1 / HOST or an ALLOWED_HOSTS name.
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

from voxcode.config.settings import settings

_PUBLIC_PATHS = ("/health", "/static/", "/favicon.ico", "/review/")
_PUBLIC_EXACT = {"/", "/review"}


class LocalSecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        path = scope.get("path", "")

        if not _host_allowed(headers.get("host", "")):
            return await _deny(
                scope, receive, send, 421, "Requests must be addressed to localhost (see ALLOWED_HOSTS)"
            )

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


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _host_allowed(host_header: str) -> bool:
    allowed = settings.allowed_hosts
    if "*" in allowed:
        return True
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:8000
        host = host[1 : host.find("]")] if "]" in host else host
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    if not host:
        return True  # HTTP/1.0 clients without Host can't be a browser rebinding attack
    configured = settings.HOST.lower()
    wildcard = configured in ("0.0.0.0", "::")  # nosec B104: a comparison, not a bind
    return host in _LOCAL_HOSTS or host in allowed or (not wildcard and host == configured)


def _origin_allowed(origin: str, host: str, scope) -> bool:
    if origin in settings.allowed_origins:
        return True
    scheme = "https" if scope.get("scheme") in ("https", "wss") else "http"
    return origin == f"{scheme}://{host}"


async def _deny(scope, receive, send, status: int, message: str):
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": {403: 4403, 401: 4401}.get(status, 4400)})
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
