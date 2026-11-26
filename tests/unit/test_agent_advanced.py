import subprocess
from pathlib import Path

import httpx
import pytest

from tests.conftest import ScriptedProvider
from tests.unit.test_agent import GOOD_DIFF
from voxcode.agent.agent_request import AgentRequest
from voxcode.agent.edit_agent import EditAgent
from voxcode.agent.memory.memory_manager import MemoryManager
from voxcode.agent.memory.stores import InMemoryStore
from voxcode.agent.pipeline import AgentPipeline
from voxcode.agent.runtime import RuntimeCache
from voxcode.agent.tool_agent import ToolAgent
from voxcode.codebase.git_history import CommitSearch, has_history_intent, read_commits
from voxcode.codebase.graph.code_graph import CodeGraph
from voxcode.codebase.indexer.index_store import IndexStore
from voxcode.codebase.indexer.indexer import RepositoryIndexer
from voxcode.config.settings import settings
from voxcode.core.model_registry import ModelRegistry
from voxcode.execution.testing import docker_sandbox
from voxcode.execution.testing.test_runner import failure_files
from voxcode.generation.providers.types import ChatTurn, ToolCall
from voxcode.integrations.github import commit_on_branch, open_pull_request, parse_github_remote


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def init_git(repo: Path) -> Path:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@x", "add", "-A")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@x", "commit", "-q", "-m", "Initial import")
    (repo / "app/auth.py").write_text(
        (repo / "app/auth.py").read_text() + "\n\ndef rate_limit():\n    return 5\n"
    )
    git(
        repo,
        "-c",
        "user.name=Ana",
        "-c",
        "user.email=a@x",
        "commit",
        "-qam",
        "Add login rate limiting after brute-force incident",
    )
    return repo


def setup(repo, tmp_path, provider):
    store = IndexStore(tmp_path / "idx")
    stats = RepositoryIndexer(store).index(repo)
    registry = ModelRegistry()
    registry.set_provider("scripted", provider)
    return stats, registry, RuntimeCache(store, registry)


@pytest.fixture(autouse=True)
def scripted(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_PROVIDER", "scripted")
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)


# ------------------------------------------------------------------ git history
def test_git_history_is_indexed_and_used_for_why_questions(sample_repo, tmp_path):
    init_git(sample_repo)
    commits = read_commits(sample_repo, 10)
    assert [c.subject for c in commits][0].startswith("Add login rate limiting")
    assert "app/auth.py" in commits[0].files
    assert CommitSearch(commits).search("brute force", limit=1)[0].author == "Ana"
    assert CommitSearch(commits).search(path="app/auth.py")[0].author == "Ana"
    assert has_history_intent("why did we add rate limiting?") and not has_history_intent("add a docstring")

    provider = ScriptedProvider(["Because of a brute-force incident."])
    stats, registry, runtimes = setup(sample_repo, tmp_path, provider)
    pipeline = AgentPipeline(registry, MemoryManager(InMemoryStore(60)), runtimes)
    pipeline.ask(
        AgentRequest(
            session_id="s", repository_key=stats.repo_key, query="why was rate limiting added to login?"
        )
    )
    assert (
        "RELEVANT COMMITS" in provider.prompts[-1].user_prompt
        and "brute-force" in provider.prompts[-1].user_prompt
    )


# ------------------------------------------------------------- precise graph
def test_precise_call_graph_resolves_the_right_definition(tmp_path):
    repo = tmp_path / "proj"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/__init__.py").write_text("")
    (repo / "pkg/a.py").write_text("def save(x):\n    return x\n")
    (repo / "pkg/b.py").write_text("def save(x):\n    return None\n")
    (repo / "pkg/main.py").write_text("from pkg.b import save\n\n\ndef run():\n    return save(1)\n")
    store = IndexStore(tmp_path / "idx")
    index = store.load(RepositoryIndexer(store).index(repo).repo_key)
    by_id = {c.chunk_id: c for c in index.chunks}
    run = next(c for c in index.chunks if c.symbol_name == "run")
    callees = [by_id[i].file_path for i in CodeGraph(index.chunks, index.call_edges).callees(run.chunk_id)]
    assert callees == ["pkg/b.py"]  # name matching would also return pkg/a.py


# ------------------------------------------------------------- query rewrite
def test_query_rewrite_adds_search_queries(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "QUERY_REWRITE", True)
    provider = ScriptedProvider(
        [
            '{"keywords": "verify_password hashlib sha256", "code": "def verify_password(user, pw): ..."}',
            "answer",
        ]
    )
    stats, registry, runtimes = setup(sample_repo, tmp_path, provider)
    pipeline = AgentPipeline(registry, MemoryManager(InMemoryStore(60)), runtimes)
    pipeline.ask(
        AgentRequest(
            session_id="s", repository_key=stats.repo_key, query="how do we check people's secret words"
        )
    )
    assert "verify_password" in provider.prompts[-1].user_prompt


# ------------------------------------------------------- native tool calling
def test_tool_agent_uses_native_tool_calls(sample_repo, tmp_path):
    provider = ScriptedProvider(
        ["unused"],
        turns=[
            ChatTurn(
                "",
                [
                    ToolCall("c1", "find_references", {"name": "verify_password"}),
                    ToolCall("c2", "git_history", {}),
                ],
            ),
            ChatTurn("verify_password lives in app/auth.py:18."),
        ],
        supports_tools=True,
    )
    stats, registry, runtimes = setup(sample_repo, tmp_path, provider)
    events = list(
        ToolAgent(registry, MemoryManager(InMemoryStore(60)), runtimes).run(
            AgentRequest(session_id="s", repository_key=stats.repo_key, query="where is hashing?"),
            sample_repo,
        )
    )
    assert [e["type"] for e in events] == ["tool", "tool", "done"]
    assert events[-1]["tool_mode"] == "native" and events[-1]["answer"].startswith("verify_password")
    second_call_messages = provider.chats[1][1]
    assert [m["role"] for m in second_call_messages] == ["user", "assistant", "tool", "tool"]


# ------------------------------------------------------------ plan + repair
def test_plan_only_then_execute_with_approved_plan(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PLAN_EDITS", True)
    plan_reply = (
        '{"summary": "Document logout", "steps": [{"file": "app/auth.py", "change": "add a docstring"}]}'
    )
    provider = ScriptedProvider([plan_reply, GOOD_DIFF, '{"approved": true}'])
    stats, registry, runtimes = setup(sample_repo, tmp_path, provider)
    agent = EditAgent(registry, MemoryManager(InMemoryStore(60)), runtimes)
    request = AgentRequest(session_id="s", repository_key=stats.repo_key, query="add a docstring to logout")

    planned = agent.run(request, sample_repo, plan_only=True)
    assert planned.status == "planned" and "1. app/auth.py: add a docstring" in planned.plan

    result = agent.run(request, sample_repo, plan="1. app/auth.py: add a docstring (keep it short)")
    assert result.status == "ready"
    assert "keep it short" in provider.prompts[-2].user_prompt  # coder saw the approved plan


def test_failure_files_from_test_output(sample_repo):
    (sample_repo / "tests").mkdir()
    (sample_repo / "tests/test_auth.py").write_text("def test_x(): pass\n")
    output = (
        "FAILED tests/test_auth.py::test_login - AssertionError\n"
        '  File "/tmp/voxcode-tests-abc/repo/app/auth.py", line 12, in login\n'
        '  File "/usr/lib/python3/json/__init__.py", line 3\n'
    )
    assert failure_files(output, sample_repo) == ["app/auth.py", "tests/test_auth.py"] or set(
        failure_files(output, sample_repo)
    ) == {"app/auth.py", "tests/test_auth.py"}


# ------------------------------------------------------------ docker sandbox
def test_docker_sandbox_command_is_locked_down(sample_repo, monkeypatch):
    args = docker_sandbox.build_run_args("c1", sample_repo, "python:3.12-slim")
    for flag in ("--memory", "--cpus", "--pids-limit", "--cap-drop", "--user"):
        assert flag in args
    assert "no-new-privileges" in args and f"{sample_repo}:/work" in args
    assert docker_sandbox.container_test_command(["/venv/bin/python", "-m", "pytest", "-x"]) == [
        "python",
        "-m",
        "pytest",
        "-x",
    ]
    (sample_repo / "requirements.txt").write_text("requests\n")
    assert "pip install -q -r requirements.txt" in docker_sandbox.install_command(sample_repo)

    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: False)
    monkeypatch.setattr(settings, "TEST_SANDBOX", "auto")
    assert docker_sandbox.use_docker() is False
    monkeypatch.setattr(settings, "TEST_SANDBOX", "docker")
    with pytest.raises(RuntimeError):
        docker_sandbox.use_docker()


# ------------------------------------------------------------ branch + PR
def test_commit_on_branch_leaves_working_tree_alone(sample_repo):
    init_git(sample_repo)
    from voxcode.execution.diff.diff_utils import extract_diff

    before = (sample_repo / "app/auth.py").read_text()
    result = commit_on_branch(sample_repo, extract_diff(GOOD_DIFF), "Document logout")
    assert result.branch.startswith("voxcode/document-logout-")
    assert (sample_repo / "app/auth.py").read_text() == before
    assert "End the user" in git(sample_repo, "show", f"{result.branch}:app/auth.py")
    assert git(sample_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_pull_request_api(sample_repo, monkeypatch):
    init_git(sample_repo)
    git(sample_repo, "remote", "add", "origin", "git@github.com:octo/demo.git")
    assert parse_github_remote("https://github.com/octo/demo.git") == ("octo", "demo")
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "t0k")

    def handler(request):
        assert (
            request.url.path == "/repos/octo/demo/pulls" and request.headers["authorization"] == "Bearer t0k"
        )
        return httpx.Response(201, json={"html_url": "https://github.com/octo/demo/pull/7"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert (
        open_pull_request(sample_repo, "voxcode/x", "main", "T", "B", client)
        == "https://github.com/octo/demo/pull/7"
    )
