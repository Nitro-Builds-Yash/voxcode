import asyncio

import pytest

from voxcode.config.settings import settings

pytest.importorskip("mcp")

pytestmark = pytest.mark.integration


def _text(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def test_mcp_server_tools(isolated_storage, sample_repo, monkeypatch):
    from voxcode.api import services
    from voxcode.integrations.mcp_server import build_server

    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    monkeypatch.setattr(services.registry, "get_embedder", lambda: None)
    server = build_server()

    async def scenario():
        tools = {t.name: t for t in await server.list_tools()}
        assert {
            "search_code",
            "read_file",
            "find_references",
            "git_history",
            "propose_edit",
            "apply_patch",
        } <= set(tools)
        assert tools["search_code"].annotations.read_only_hint is True
        assert tools["apply_patch"].annotations.destructive_hint is True

        found = _text(
            await server.call_tool(
                "search_code", {"query": "verify password", "repository_path": str(sample_repo)}
            )
        )
        assert "app/auth.py" in found and "verify_password" in found
        read = _text(
            await server.call_tool(
                "read_file",
                {"path": "app/auth.py", "start": 1, "end": 3, "repository_path": str(sample_repo)},
            )
        )
        assert "Authentication helpers" in read

    asyncio.run(scenario())
