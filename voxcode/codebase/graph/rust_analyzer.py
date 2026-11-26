"""Exact Rust call edges from rust-analyzer's call hierarchy (LSP).

Used when a working `rust-analyzer` binary is available (RUST_ANALYZER =
"auto" finds it on PATH; or give a path; "off" disables it). The server
indexes the workspace, then for each function `textDocument/prepareCallHierarchy`
+ `callHierarchy/outgoingCalls` give the definitions it really calls, through
traits, generics and macros. Returns None when rust-analyzer is unusable so
the caller falls back to the tree-sitter resolver.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import unquote, urlparse

from voxcode.config.settings import settings
from voxcode.models.code_chunk import CodeChunk

logger = logging.getLogger(__name__)

_FN_NAME = re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)")


@lru_cache(maxsize=4)
def find_binary(setting: str) -> str | None:
    if setting.lower() == "off":
        return None
    binary = shutil.which("rust-analyzer") if setting.lower() == "auto" else setting
    if not binary:
        return None
    try:  # a rustup proxy without the component installed exits non-zero
        ok = subprocess.run([binary, "--version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return binary if ok else None


class LspClient:
    """Minimal JSON-RPC-over-stdio client (Content-Length framing)."""

    def __init__(self, command: list[str], cwd: Path):
        self.proc = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self._id = 0
        self._responses: dict[int, Queue] = defaultdict(Queue)
        self.notifications: Queue = Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        assert self.proc.stdin is not None
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.proc.stdin.flush()

    def _read_loop(self) -> None:
        stream = self.proc.stdout
        assert stream is not None
        while True:
            length = None
            while True:
                line = stream.readline()
                if not line:
                    return
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1])
            if length is None:
                continue
            message = json.loads(stream.read(length))
            if "id" in message and "method" in message:  # server -> client request
                self._send({"jsonrpc": "2.0", "id": message["id"], "result": None})
            elif "id" in message:
                self._responses[message["id"]].put(message)
            else:
                self.notifications.put(message)

    def request(self, method: str, params: dict, timeout: float = 30.0):
        self._id += 1
        request_id = self._id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            message = self._responses[request_id].get(timeout=timeout)
        except Empty:
            raise TimeoutError(method) from None
        finally:
            self._responses.pop(request_id, None)
        if "error" in message:
            raise RuntimeError(f"{method}: {message['error'].get('message')}")
        return message.get("result")

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def wait_until_ready(self, timeout: float) -> bool:
        """rust-analyzer reports `quiescent` once indexing is done."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.notifications.get(timeout=max(0.1, deadline - time.monotonic()))
            except Empty:
                break
            if message.get("method") == "experimental/serverStatus" and message["params"].get("quiescent"):
                return True
        return False

    def close(self) -> None:
        try:
            self.request("shutdown", {}, timeout=5)
            self.notify("exit", {})
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def _uri_to_rel(uri: str, repo: Path) -> str | None:
    path = Path(unquote(urlparse(uri).path))
    try:
        return path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return None  # std / dependency


def call_edges(
    repo: Path, chunks: list[CodeChunk], targets: set[str], binary: str | None = None
) -> dict[str, list[str]] | None:
    binary = binary or find_binary(settings.RUST_ANALYZER)
    if not binary:
        return None
    repo = repo.resolve()
    by_file: dict[str, list[CodeChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.language == "rust" and chunk.chunk_type in ("function", "method", "class", "module"):
            by_file[chunk.file_path].append(chunk)

    def locate(rel: str, line: int) -> CodeChunk | None:
        return min(
            (
                c
                for c in by_file.get(rel, [])
                if c.chunk_type != "module" and c.start_line <= line <= c.end_line
            ),
            key=lambda c: c.end_line - c.start_line,
            default=None,
        )

    try:
        client = LspClient([binary], repo)
    except OSError as exc:
        logger.info("rust-analyzer could not start: %s", exc)
        return None
    edges: dict[str, list[str]] = {}
    started = time.monotonic()
    try:
        client.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": repo.as_uri(),
                "capabilities": {"experimental": {"serverStatusNotification": True}},
                "initializationOptions": {"checkOnSave": False, "cargo": {"buildScripts": {"enable": False}}},
            },
            timeout=60,
        )
        client.notify("initialized", {})
        if not client.wait_until_ready(settings.RUST_ANALYZER_TIMEOUT):
            logger.warning("rust-analyzer did not finish indexing in time; using partial results")
        for rel in sorted(targets):
            uri = (repo / rel).as_uri()
            for chunk in by_file.get(rel, []):
                if chunk.chunk_type not in ("function", "method"):
                    continue
                if time.monotonic() - started > settings.RUST_ANALYZER_TIMEOUT * 3:
                    logger.warning("rust-analyzer: time budget reached")
                    return edges
                position = _name_position(chunk)
                if position is None:
                    continue
                items = client.request(
                    "textDocument/prepareCallHierarchy",
                    {"textDocument": {"uri": uri}, "position": position},
                )
                callees: list[str] = []
                for item in items or []:
                    for call in client.request("callHierarchy/outgoingCalls", {"item": item}) or []:
                        to = call.get("to", {})
                        target_rel = _uri_to_rel(to.get("uri", ""), repo)
                        if target_rel is None:
                            continue
                        line = to.get("selectionRange", to.get("range", {})).get("start", {}).get("line")
                        target = locate(target_rel, line + 1) if line is not None else None
                        if target and target.chunk_id != chunk.chunk_id and target.chunk_id not in callees:
                            callees.append(target.chunk_id)
                if callees:
                    edges[chunk.chunk_id] = callees
        return edges
    except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
        logger.warning("rust-analyzer failed (%s); falling back to tree-sitter resolution", exc)
        return None
    finally:
        client.close()


def _name_position(chunk: CodeChunk) -> dict[str, int] | None:
    for offset, line in enumerate(chunk.content.splitlines()[:10]):
        match = _FN_NAME.search(line)
        if match:
            return {"line": chunk.start_line - 1 + offset, "character": match.start(1)}
    return None
