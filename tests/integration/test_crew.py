"""The agent crew: planner, parallel researchers, writer/coder, tester, reviewer."""

import json

import pytest

from tests.conftest import ScriptedProvider
from tests.unit.test_agent import GOOD_DIFF
from voxcode.api import services
from voxcode.config.settings import settings

pytestmark = pytest.mark.integration


def replies(prompt):
    system, user = prompt.system_prompt, prompt.user_prompt
    if "You are the PLANNER" in system:
        change = "docstring" in user
        return json.dumps(
            {
                "goal": "Document logout" if change else "Explain how logout works",
                "needs_change": change,
                "research": ["Where is logout defined?", "What calls logout?"],
                "plan": ["Add a docstring to logout"] if change else [],
            }
        )
    if "You are the REVIEWER" in system:
        return '{"approved": true, "score": 5, "issues": []}'
    if "You are the WRITER" in system:
        return "Logout is handled by `logout` in app/auth.py."
    if "unified diff" in system:
        return GOOD_DIFF
    if "review a proposed patch" in system:
        return '{"approved": true, "issues": []}'
    if '"tool"' in system and "final" in system:  # researcher (JSON tool protocol)
        if "YOU CALLED" not in user:
            return '{"tool": "search_code", "args": {"query": "logout"}}'
        return '{"final": "`logout` is defined in app/auth.py."}'
    return "ok"


@pytest.fixture
def client(isolated_storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from voxcode.crew.trace import trace_store

    monkeypatch.setattr(trace_store, "_root", tmp_path / "crew")
    monkeypatch.setattr(trace_store, "_traces", {})
    monkeypatch.setattr(settings, "PRELOAD_MODELS", False)
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    monkeypatch.setattr(settings, "DEFAULT_PROVIDER", "scripted")
    monkeypatch.setattr(settings, "API_TOKEN", "")
    monkeypatch.setattr(services.registry, "get_embedder", lambda: None)
    services.registry.set_provider("scripted", ScriptedProvider([replies]))
    from voxcode.main import app

    with TestClient(app) as test_client:
        yield test_client


def _run(client, task, repo, **extra):
    from voxcode.api.routes.crew import start_run
    from voxcode.core.repository import resolve_repository_path

    trace = start_run(task, resolve_repository_path(str(repo)), background=False, **extra)
    return client.get(f"/crews/{trace.id}").json()


def test_question_flow(client, sample_repo):
    run = _run(client, "how does logout work", sample_repo)
    assert run["status"] == "done" and run["result"]["kind"] == "answer"
    assert run["result"]["answer"] == "Logout is handled by `logout` in app/auth.py."
    assert run["result"]["quality"]["groundedness"] == 1.0
    agents = set(run["agents"])
    assert {"planner", "researcher-1", "researcher-2", "writer", "reviewer"} <= agents
    assert run["agents"]["researcher-1"]["tool_calls"] == 1 and run["total_tokens"] > 0
    kinds = [(e["agent"], e["kind"]) for e in run["events"]]
    assert kinds[0] == ("planner", "start") and kinds[-1] == ("crew", "done")


def test_change_flow_and_apply(client, sample_repo, monkeypatch):
    monkeypatch.setattr(settings, "TEST_SANDBOX", "local")
    run = _run(client, "add a docstring to logout", sample_repo, run_tests=False)
    result = run["result"]
    assert result["kind"] == "change" and result["review"]["approved"] and "app/auth.py" in result["diff"]
    assert {"coder", "reviewer"} <= set(run["agents"])
    applied = client.post(f"/crews/{run['id']}/apply").json()
    assert applied == {"status": "applied", "files_modified": ["app/auth.py"]}
    assert "End the user" in (sample_repo / "app/auth.py").read_text()
    assert client.post(f"/crews/{run['id']}/apply").status_code == 409  # patch no longer applies


def test_api_events_and_listing(client, sample_repo):
    created = client.post(
        "/crews", json={"task": "how does logout work", "repository_path": str(sample_repo), "mode": "answer"}
    ).json()
    events = client.get(f"/crews/{created['id']}/events").text  # streams until the run ends
    lines = [json.loads(line[5:]) for line in events.splitlines() if line.startswith("data:")]
    assert lines[-1] == {"type": "end", "status": "done"}
    assert any(e.get("agent") == "writer" for e in lines)
    assert client.get("/crews").json()[0]["id"] == created["id"]
    assert client.get(f"/crew/{created['id']}").status_code == 200  # page
    assert client.get("/crews/ffffffffffff").status_code == 404


def test_token_budget_stops_the_run(client, sample_repo, monkeypatch):
    monkeypatch.setattr(settings, "CREW_MAX_TOKENS", 1)
    run = _run(client, "how does logout work", sample_repo)
    assert any("Token budget" in e["text"] for e in run["events"])
    assert run["status"] == "done" and "researcher-1" not in run["agents"]
