"""Run a review from the command line or an agent hook.

Uses the running VoxCode server when there is one; otherwise starts the app
in this process on REVIEW_PORT (fixed, so a review page keeps working across
hook calls and over SSH port forwarding), opens the browser, and blocks until
the reviewer decides.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

import httpx

from voxcode.config.settings import settings


def _headers() -> dict:
    return {"X-VoxCode-Token": settings.API_TOKEN} if settings.API_TOKEN else {}


def _is_voxcode(base: str) -> bool:
    try:
        return httpx.get(f"{base}/health", timeout=1.5).json().get("service") == "voxcode"
    except (httpx.HTTPError, ValueError):
        return False


def _free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def remote_session() -> bool:
    return bool(
        os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY") or os.environ.get("VOXCODE_REMOTE")
    )


def ensure_server() -> str:
    """Base URL of a VoxCode server, starting one in-process if needed."""
    for base in (f"http://127.0.0.1:{settings.PORT}", f"http://127.0.0.1:{settings.REVIEW_PORT}"):
        if _is_voxcode(base):
            return base
    import uvicorn

    port = settings.REVIEW_PORT if settings.REVIEW_PORT and _free(settings.REVIEW_PORT) else 0
    if not port:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    config = uvicorn.Config("voxcode.main:app", host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="review-server").start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        if _is_voxcode(base):
            return base
        time.sleep(0.1)
    raise RuntimeError(f"review server did not start on {base}")


def open_page(url: str) -> None:
    if remote_session() or os.environ.get("VOXCODE_NO_BROWSER"):
        print(f"VoxCode review: {url}", file=sys.stderr)
        return
    if not webbrowser.open(url):
        print(f"VoxCode review: {url}", file=sys.stderr)


def wait(base: str, review_id: str, revision: int, timeout: float | None = None) -> dict:
    deadline = time.monotonic() + timeout if timeout else None
    with httpx.Client(timeout=130, headers=_headers()) as client:
        while True:
            reply = client.get(
                f"{base}/reviews/{review_id}/wait", params={"revision": revision, "timeout": 55}
            ).json()
            if reply.get("decided"):
                return reply
            if deadline and time.monotonic() > deadline:
                return {"decided": False}


def review_text(
    content: str,
    kind: str = "plan",
    title: str = "",
    source: str = "cli",
    meta: dict | None = None,
    open_browser: bool = True,
) -> dict:
    """Create (or, for a known agent session, revise) a review and wait for the decision."""
    base = ensure_server()
    with httpx.Client(timeout=30, headers=_headers()) as client:
        reply = client.post(
            f"{base}/reviews",
            json={"kind": kind, "content": content, "title": title, "source": source, "meta": meta or {}},
        )
        reply.raise_for_status()
        created = reply.json()
    url = f"{base}{created['url']}"
    if created.get("revised"):
        print(f"VoxCode review updated to v{created['revision'] + 1}: {url}", file=sys.stderr)
    elif open_browser:
        open_page(url)
    decision = wait(base, created["id"], created["revision"])
    decision.update(url=url, id=created["id"])
    return decision
