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
    monkeypatch.setattr(settings, "PLAN_EDITS", False)
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


def test_timings_and_usage_are_reported(client, sample_repo):
    body = client.post(
        "/agent/query", json={"query": "where is logout handled", "repository_path": str(sample_repo)}
    ).json()
    assert {"retrieval", "context", "generation"} <= set(body["timings_ms"])
    assert body["usage"]["output_tokens"] > 0

    events = client.post(
        "/agent/stream", json={"query": "where is logout", "repository_path": str(sample_repo)}
    ).text
    done = [line for line in events.splitlines() if '"type": "done"' in line][0]
    assert '"first_token"' in done and '"timings_ms"' in done and '"usage"' in done


def test_apply_on_branch(client, sample_repo):
    import subprocess

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sample_repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=sample_repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@x", "commit", "-qm", "init"],
        cwd=sample_repo,
        check=True,
    )
    edit = client.post(
        "/agent/edit", json={"query": "add a docstring to logout", "repository_path": str(sample_repo)}
    ).json()
    result = client.post(
        "/agent/apply",
        json={
            "repository_path": str(sample_repo),
            "diff": edit["diff"],
            "target": "branch",
            "title": "Docstring",
        },
    ).json()
    assert result["status"] == "committed" and result["branch"].startswith("voxcode/docstring-")
    assert "End the user" not in (sample_repo / "app/auth.py").read_text()


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


def test_rust_repository_end_to_end(client, rust_repo, monkeypatch):
    monkeypatch.setattr(settings, "RUST_ANALYZER", "off")
    stats = client.post("/index/", json={"repository_path": str(rust_repo)}).json()
    assert stats["files_indexed"] == 6 and stats["functions_found"] >= 8
    from voxcode.api import services
    from voxcode.codebase.indexer.indexer import repository_key

    index = services.indexer.store.load(repository_key(rust_repo.resolve()))
    assert index is not None and len(index.call_edges) >= 5  # Rust call graph stored with the index

    body = client.post(
        "/agent/query", json={"query": "where are item names normalized", "repository_path": str(rust_repo)}
    ).json()
    assert "src/util.rs" in {s["file_path"] for s in body["sources"]}


def test_feedback_and_patch_acceptance_train_the_ranker(client, sample_repo, monkeypatch):
    from voxcode.core.repository import repository_key
    from voxcode.learning import feedback

    monkeypatch.setattr(settings, "RANKER_MIN_FEEDBACK", 2)
    key = repository_key(sample_repo.resolve())
    body = client.post(
        "/agent/feedback",
        json={"repository_path": str(sample_repo), "query": "where is logout", "files": ["app/auth.py"]},
    ).json()
    assert body == {"recorded": True, "feedback_entries": 1, "min_for_training": 2}
    assert not client.post(
        "/agent/feedback", json={"repository_path": str(sample_repo), "query": "x", "helpful": False}
    ).json()["recorded"]

    edit = client.post(
        "/agent/edit", json={"query": "add a docstring to logout", "repository_path": str(sample_repo)}
    ).json()
    client.post(
        "/agent/apply",
        json={
            "repository_path": str(sample_repo),
            "diff": edit["diff"],
            "query": "add a docstring to logout",
        },
    )
    kinds = [e["kind"] for e in feedback.load(key)]
    assert kinds == ["vote", "accepted_patch"]


def test_answers_carry_quality_routing_and_cache_flags(client, sample_repo):
    first = client.post(
        "/agent/query",
        json={"query": "where is logout", "repository_path": str(sample_repo), "session_id": "cache-a"},
    ).json()
    assert first["quality"]["groundedness"] == 1.0 and first["quality"]["citations"] == 1
    assert first["routing"]["tier"] == "main" and first["cached"] is False
    again = client.post(
        "/agent/query",
        json={"query": "where is the logout", "repository_path": str(sample_repo), "session_id": "cache-b"},
    ).json()
    assert again["cached"] is True and again["summary"] == first["summary"]


def test_speak_reports_a_missing_tts_engine(client, monkeypatch):
    """Regression: /voice/speak answered "ok" and stayed silent when no engine was installed."""
    from voxcode.voice.tts.tts_service import tts_service

    monkeypatch.setattr(tts_service, "available", lambda: False)
    reply = client.post("/voice/speak", json={"text": "hello", "play": True})
    assert reply.status_code == 503 and "not installed" in reply.json()["detail"]
    assert client.post("/voice/speak", json={"text": "hello", "play": False}).status_code == 200
