import pytest
from fastapi.testclient import TestClient

from tests.conftest import ScriptedProvider
from tests.unit.test_agent import GOOD_DIFF
from voxcode.api import services
from voxcode.config.settings import settings

pytestmark = pytest.mark.integration


def _replies(prompt):
    if "unified diff" in prompt.system_prompt:
        return GOOD_DIFF
    if "review a proposed patch" in prompt.system_prompt:
        return '{"approved": true, "issues": []}'
    return "Logout is in app/auth.py:14."


@pytest.fixture
def client(isolated_storage, monkeypatch):
    monkeypatch.setattr(settings, "PRELOAD_MODELS", False)
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    monkeypatch.setattr(settings, "DEFAULT_PROVIDER", "scripted")
    monkeypatch.setattr(settings, "API_TOKEN", "")
    services.registry.set_model(f"embedder:{settings.EMBEDDING_MODEL}", None)
    services.registry._models.pop(f"embedder:{settings.EMBEDDING_MODEL}")
    monkeypatch.setattr(services.registry, "get_embedder", lambda: None)
    services.registry.set_provider("scripted", ScriptedProvider([_replies]))

    from voxcode.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_health_and_metrics(client):
    assert client.get("/health").json()["status"] == "healthy"
    assert client.get("/metrics/").status_code == 200


def test_index_and_answer(client, sample_repo):
    stats = client.post("/index/", json={"repository_path": str(sample_repo)}).json()
    assert stats["files_indexed"] == 3 and stats["functions_found"] >= 4

    response = client.post(
        "/agent/query", json={"query": "where is logout handled", "repository_path": str(sample_repo)}
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body["summary"] == "Logout is in app/auth.py:14."
    assert body["mode"] == "answer"
    assert any(s["symbol_name"] == "AuthService.logout" for s in body["sources"])


def test_query_auto_indexes_and_edits(client, sample_repo):
    body = client.post(
        "/agent/query", json={"query": "add a docstring to logout", "repository_path": str(sample_repo)}
    ).json()
    assert body["mode"] == "edit" and body["status"] == "ready"
    assert body["files_modified"] == ["app/auth.py"]

    applied = client.post("/agent/apply", json={"repository_path": str(sample_repo), "diff": body["diff"]})
    assert applied.status_code == 200
    assert "End the user" in (sample_repo / "app/auth.py").read_text()
    assert client.post("/agent/undo", json={"repository_path": str(sample_repo)}).status_code == 200
    assert "End the user" not in (sample_repo / "app/auth.py").read_text()


def test_stream_emits_tokens(client, sample_repo):
    response = client.post(
        "/agent/stream", json={"query": "where is logout", "repository_path": str(sample_repo)}
    )
    events = [line for line in response.text.splitlines() if line.startswith("data:")]
    assert '"mode": "answer"' in events[0]
    assert '"type": "sources"' in events[1]
    assert any('"type": "token"' in e for e in events)
    assert '"type": "done"' in events[-1]


def test_stream_edit_event(client, sample_repo):
    response = client.post(
        "/agent/stream", json={"query": "add a docstring to logout", "repository_path": str(sample_repo)}
    )
    events = [line for line in response.text.splitlines() if line.startswith("data:")]
    assert '"mode": "edit"' in events[0]
    assert '"type": "edit"' in events[1] and '"status": "ready"' in events[1]


def test_bad_paths_and_config_are_rejected(client):
    assert client.post("/index/", json={"repository_path": "/definitely/missing"}).status_code == 400
    assert client.post("/index/", json={"repository_path": "/"}).status_code == 400
    assert client.post("/config/update", json={"default_provider": "evil"}).status_code == 400
    assert client.post("/config/update", json={"ollama_host": "file:///etc"}).status_code == 400
    assert "api_key" not in str(client.get("/config/").json())


def test_cross_site_requests_blocked(client):
    blocked = client.post("/config/update", json={}, headers={"Origin": "https://evil.example"})
    assert blocked.status_code == 403
    same_origin = client.post("/config/update", json={}, headers={"Origin": "http://testserver"})
    assert same_origin.status_code == 200


def test_api_token(client, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", "s3cret")
    assert client.get("/config/").status_code == 401
    assert client.get("/config/", headers={"X-VoxCode-Token": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200
    assert "s3cret" in client.get("/").text  # injected for the same-origin UI
