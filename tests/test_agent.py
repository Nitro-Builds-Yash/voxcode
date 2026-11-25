import time

from backend.agent.agent_request import AgentRequest
from backend.agent.edit_agent import EditAgent
from backend.agent.memory.memory_manager import MemoryManager
from backend.agent.memory.stores import InMemoryStore
from backend.agent.planner.execution_plan import TaskType
from backend.agent.planner.task_classifier import TaskClassifier
from backend.agent.reviewer.reviewer import parse_review
from backend.agent.runtime import RuntimeCache
from backend.agent.tool_agent import ToolAgent
from backend.codebase.indexer.index_store import IndexStore
from backend.codebase.indexer.indexer import RepositoryIndexer
from backend.core.model_registry import ModelRegistry
from tests.conftest import ScriptedProvider

GOOD_DIFF = """Add a docstring to logout.
```diff
--- a/app/auth.py
+++ b/app/auth.py
@@ -12,4 +12,5 @@
         return verify_password(user, password)
 
     def logout(self, session):
+        \"\"\"End the user's session.\"\"\"
         session.clear()
```"""

BAD_DIFF = """```diff
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,2 +1,2 @@
-this line does not exist
+nope
```"""


def test_classifier_uses_whole_words():
    c = TaskClassifier()
    assert c.classify("show me the login code") == TaskType.SEARCH
    assert c.classify("how do I add a route?") == TaskType.EXPLAIN
    assert c.classify("add docstrings to auth.py") == TaskType.GENERATE
    assert c.classify("fix the address parser") == TaskType.FIX
    assert c.classify("hello there") == TaskType.CHAT


def test_memory_caps_and_expires():
    memory = MemoryManager(InMemoryStore(ttl_seconds=60), max_messages=4)
    for i in range(5):
        memory.add_messages("s", ("user", f"q{i}"), ("assistant", f"a{i}"))
    history = memory.get_history("s").messages
    assert [m.content for m in history] == ["q3", "a3", "q4", "a4"]

    expiring = InMemoryStore(ttl_seconds=0)
    expiring.save("k", {"v": 1})
    time.sleep(0.01)
    assert expiring.load("k") is None


def test_parse_review():
    assert parse_review('{"approved": true, "issues": []}').approved
    verdict = parse_review('Sure: {"approved": false, "issues": ["breaks login"]}')
    assert not verdict.approved and verdict.issues == ["breaks login"]


def _setup(sample_repo, tmp_path, replies):
    store = IndexStore(tmp_path / "idx")
    stats = RepositoryIndexer(store).index(sample_repo)
    registry = ModelRegistry()
    provider = ScriptedProvider(replies)
    registry.set_provider("scripted", provider)
    return stats, registry, provider, RuntimeCache(store, registry)


def test_edit_agent_retries_until_patch_applies(sample_repo, tmp_path, monkeypatch):
    from backend.config.settings import settings

    monkeypatch.setattr(settings, "DEFAULT_PROVIDER", "scripted")
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    stats, registry, provider, runtimes = _setup(
        sample_repo, tmp_path, [BAD_DIFF, GOOD_DIFF, '{"approved": true, "issues": []}']
    )
    agent = EditAgent(registry, MemoryManager(InMemoryStore(60)), runtimes)
    result = agent.run(
        AgentRequest(session_id="s", repository_key=stats.repo_key, query="add a docstring to logout"),
        sample_repo,
    )
    assert result.status == "ready"
    assert [a.patch_applies for a in result.attempts] == [False, True]
    assert "git apply --check" in provider.prompts[1].user_prompt  # feedback was sent
    assert '"""End the user\'s session."""' in result.files[0]["proposed"]
    assert "End the user" not in (sample_repo / "app/auth.py").read_text()  # nothing written


def test_tool_agent_calls_tools_then_answers(sample_repo, tmp_path, monkeypatch):
    from backend.config.settings import settings

    monkeypatch.setattr(settings, "DEFAULT_PROVIDER", "scripted")
    stats, registry, provider, runtimes = _setup(
        sample_repo,
        tmp_path,
        [
            '{"tool": "find_references", "args": {"name": "verify_password"}}',
            '{"tool": "read_file", "args": {"path": "app/auth.py", "start": 18, "end": 21}}',
            '{"final": "verify_password is in app/auth.py:18"}',
        ],
    )
    agent = ToolAgent(registry, MemoryManager(InMemoryStore(60)), runtimes)
    events = list(
        agent.run(
            AgentRequest(session_id="s", repository_key=stats.repo_key, query="where is hashing done"),
            sample_repo,
        )
    )
    assert [e["type"] for e in events] == ["tool", "tool", "done"]
    assert "app/auth.py:12" in events[0]["observation"]
    assert events[-1]["answer"] == "verify_password is in app/auth.py:18"
    assert "outside the repository" in agent_read_outside(sample_repo, runtimes, stats.repo_key)


def agent_read_outside(repo, runtimes, key):
    from backend.agent.tool_agent import _Tools

    return _Tools(repo, runtimes.retriever(key)).call("read_file", {"path": "../../etc/passwd"})
