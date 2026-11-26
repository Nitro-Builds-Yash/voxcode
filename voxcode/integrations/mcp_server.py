"""VoxCode as a Model Context Protocol (MCP) server.

Lets Claude Desktop, Claude Code, Cursor and other MCP clients use VoxCode's
index: hybrid code search, file reading, references, git history, grounded
answers and validated patches.

    voxcode mcp --repo /path/to/project            # stdio (desktop clients)
    voxcode mcp --transport streamable-http        # http://127.0.0.1:8765/mcp

Read-only tools are marked so clients can run them without asking;
`apply_patch` is marked destructive, so clients ask before running it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

INSTRUCTIONS = """VoxCode indexes a code repository. Prefer `search_code` to find relevant
functions before reading files; use `find_references` to see callers, and
`git_history` for why/when questions. `propose_edit` returns a validated
diff without changing files; only call `apply_patch` after the user agrees."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)


def _default_repo() -> str:
    return os.environ.get("VOXCODE_MCP_REPO") or os.getcwd()


def _context(repository_path: str | None):
    from voxcode.agent.runtime import runtime_cache
    from voxcode.api import services
    from voxcode.core.repository import resolve_repository_path

    repo = resolve_repository_path(repository_path or _default_repo())
    key = services.ensure_indexed(repo)
    return repo, key, runtime_cache.retriever(key)


def _tools(repository_path: str | None):
    from voxcode.agent.tool_agent import CodeTools

    repo, _, retriever = _context(repository_path)
    return CodeTools(repo, retriever)


def build_server() -> MCPServer:
    server = MCPServer(name="voxcode", title="VoxCode", instructions=INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    def search_code(query: str, limit: int = 8, repository_path: str | None = None) -> str:
        """Hybrid search (BM25 + vectors + call graph) over the repository.
        Returns matching functions/classes with file paths and line numbers."""
        return _tools(repository_path).call("search_code", {"query": query, "limit": limit})

    @server.tool(annotations=READ_ONLY)
    def read_file(path: str, start: int = 1, end: int = 200, repository_path: str | None = None) -> str:
        """Read lines start..end of a file (path relative to the repository root)."""
        return _tools(repository_path).call("read_file", {"path": path, "start": start, "end": end})

    @server.tool(annotations=READ_ONLY)
    def find_references(name: str, repository_path: str | None = None) -> str:
        """Every line that references an identifier, as path:line."""
        return _tools(repository_path).call("find_references", {"name": name})

    @server.tool(annotations=READ_ONLY)
    def list_files(pattern: str = "*", repository_path: str | None = None) -> str:
        """Indexed files matching a glob pattern such as '*.py' or 'src/*'."""
        return _tools(repository_path).call("list_files", {"pattern": pattern})

    @server.tool(annotations=READ_ONLY)
    def git_history(
        query: str = "", path: str = "", limit: int = 10, repository_path: str | None = None
    ) -> str:
        """Recent commits, optionally filtered by words in the message or a file path."""
        return _tools(repository_path).call("git_history", {"query": query, "path": path, "limit": limit})

    @server.tool(annotations=READ_ONLY)
    def ask(question: str, repository_path: str | None = None) -> dict[str, Any]:
        """Answer a question about the code with VoxCode's own model, citing sources."""
        from voxcode.agent.agent_request import AgentRequest
        from voxcode.api import services

        _, key, _ = _context(repository_path)
        response = services.pipeline().ask(AgentRequest(session_id="mcp", repository_key=key, query=question))
        return {"answer": response.answer, "sources": [s.model_dump() for s in response.sources]}

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def propose_edit(
        instruction: str, run_tests: bool = False, repository_path: str | None = None
    ) -> dict[str, Any]:
        """Write a patch for the instruction, check it with `git apply`, optionally
        run the tests in a sandbox, and have it reviewed. Never modifies files."""
        from voxcode.agent.agent_request import AgentRequest
        from voxcode.api import services

        repo, key, _ = _context(repository_path)
        result = services.edit_agent().run(
            AgentRequest(session_id="mcp", repository_key=key, query=instruction), repo, run_tests=run_tests
        )
        return {
            "status": result.status,
            "summary": result.summary,
            "plan": result.plan,
            "diff": result.diff,
            "files": [f["path"] for f in result.files],
        }

    @server.tool(annotations=WRITES)
    def apply_patch(diff: str, repository_path: str | None = None) -> dict[str, Any]:
        """Apply a unified diff to the working tree (the last one can be undone
        with `undo_last_patch`). Only call after the user approved the diff."""
        from voxcode.api import services
        from voxcode.execution.patch import patch_service

        repo, _, _ = _context(repository_path)
        files = patch_service.apply(repo, diff)
        services.index_repository(repo)
        return {"applied": files}

    @server.tool(annotations=WRITES)
    def undo_last_patch(repository_path: str | None = None) -> dict[str, Any]:
        """Revert the last patch applied through VoxCode."""
        from voxcode.api import services
        from voxcode.execution.patch import patch_service

        repo, _, _ = _context(repository_path)
        files = patch_service.undo_last(repo)
        services.index_repository(repo)
        return {"reverted": files}

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
    )
    def index_repository(full: bool = False, repository_path: str | None = None) -> dict[str, Any]:
        """(Re)index the repository. Incremental unless full=True."""
        from voxcode.api import services
        from voxcode.core.repository import resolve_repository_path

        repo = resolve_repository_path(repository_path or _default_repo())
        return services.index_repository(repo, full=full).to_dict()

    return server


def run(repo: str | None = None, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    import logging
    import sys

    # stdout is the protocol channel for stdio: logs must go to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    if repo:
        os.environ["VOXCODE_MCP_REPO"] = str(Path(repo).expanduser().resolve())
    server = build_server()
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run(transport, host=host, port=port)  # type: ignore[call-overload]
