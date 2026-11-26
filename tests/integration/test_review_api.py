import time

import pytest

from tests.conftest import ScriptedProvider
from tests.unit.test_agent import GOOD_DIFF
from voxcode.api import services
from voxcode.config.settings import settings

pytestmark = pytest.mark.integration

PLAN_V1 = '{"summary": "Add a docstring to logout", "steps": [{"file": "app/auth.py", "change": "document logout"}]}'
PLAN_V2 = '{"summary": "Add a Google-style docstring to logout", "steps": [{"file": "app/auth.py", "change": "Google style"}]}'


def _replies(prompt):
    system, user = prompt.system_prompt, prompt.user_prompt
    if "You plan code changes" in system:
        return PLAN_V2 if "rejected the previous plan" in user else PLAN_V1
    if "unified diff" in system:
        return GOOD_DIFF
    if "review a proposed patch" in system:
        return '{"approved": true, "issues": []}'
    return "ok"


@pytest.fixture
def client(isolated_storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from voxcode.review.store import review_store

    monkeypatch.setattr(review_store, "_root", tmp_path / "reviews")
    monkeypatch.setattr(review_store, "_reviews", {})
    monkeypatch.setattr(settings, "PRELOAD_MODELS", False)
    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    monkeypatch.setattr(settings, "DEFAULT_PROVIDER", "scripted")
    monkeypatch.setattr(settings, "API_TOKEN", "")
    monkeypatch.setattr(services.registry, "get_embedder", lambda: None)
    services.registry.set_provider("scripted", ScriptedProvider([_replies]))
    from voxcode.main import app

    with TestClient(app) as test_client:
        yield test_client


def _until(client, review_id, check, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/reviews/{review_id}").json()
        if check(body):
            return body
        time.sleep(0.1)
    raise AssertionError(f"timed out; last state: {body['status']} {body['meta']}")


def test_review_api_round_trip(client):
    created = client.post(
        "/reviews", json={"kind": "plan", "content": "# Plan\n\nDo it.", "source": "cli"}
    ).json()
    rid = created["id"]
    assert created["url"] == f"/review/{rid}" and client.get(f"/review/{rid}").status_code == 200
    ann = client.post(
        f"/reviews/{rid}/annotations", json={"kind": "comment", "quote": "Do it.", "comment": "How?"}
    ).json()
    assert client.post(f"/reviews/{rid}/annotations", json={"kind": "comment"}).status_code == 422
    assert client.get(f"/reviews/{rid}/wait", params={"timeout": 0}).json() == {"decided": False}
    decision = client.post(
        f"/reviews/{rid}/decision", json={"decision": "deny", "comment": "Be specific"}
    ).json()
    assert decision["decision"] == "denied" and '"Do it.": How?' in decision["feedback"]
    assert client.post(f"/reviews/{rid}/decision", json={"decision": "approve"}).status_code == 409
    assert client.get(f"/reviews/{rid}/wait", params={"revision": 0}).json()["decided"]
    client.delete(f"/reviews/{rid}/annotations/{ann['id']}")
    view = client.post(f"/reviews/{rid}/revisions", json={"content": "# Plan\n\nDo it with tests."}).json()
    assert (
        view["status"] == "pending"
        and view["revision"] == 1
        and any(c["op"] == "add" for c in view["changes"])
    )
    assert client.get("/reviews").json()[0]["id"] == rid
    assert client.get("/reviews/ffffffffffff").status_code == 404


def test_agent_sessions_revise_the_same_review(client):
    meta = {"claude_session": "sess-1", "cwd": "/w"}
    first = client.post("/reviews", json={"content": "# v1", "source": "claude-code", "meta": meta}).json()
    client.post(f"/reviews/{first['id']}/decision", json={"decision": "deny", "comment": "no"})
    second = client.post("/reviews", json={"content": "# v2", "source": "claude-code", "meta": meta}).json()
    assert second == {"id": first["id"], "url": first["url"], "revision": 1, "revised": True}
    other = client.post(
        "/reviews", json={"content": "# x", "source": "claude-code", "meta": {"claude_session": "sess-2"}}
    ).json()
    assert other["id"] != first["id"]


def test_review_pages_are_public_but_the_api_is_not(client, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", "secret")
    assert client.get("/review").status_code == 200 and client.get("/review/abc123abc123").status_code == 200
    assert 'window.VOXCODE_TOKEN = "secret"' in client.get("/review").text
    assert client.get("/reviews").status_code == 401
    assert client.get("/reviews", headers={"X-VoxCode-Token": "secret"}).status_code == 200


def test_voxcode_plan_to_patch_flow(client, sample_repo):
    plan = client.post(
        "/reviews/plan", json={"query": "add a docstring to logout", "repository_path": str(sample_repo)}
    ).json()
    review = client.get(f"/reviews/{plan['id']}").json()
    assert review["kind"] == "plan" and review["content"].startswith("Add a docstring to logout")

    client.post(
        f"/reviews/{plan['id']}/annotations",
        json={"kind": "comment", "quote": "logout", "comment": "Google style"},
    )
    client.post(f"/reviews/{plan['id']}/decision", json={"decision": "deny"})
    review = _until(client, plan["id"], lambda r: r["status"] == "pending" and r["revision"] == 1)
    assert review["content"].startswith("Add a Google-style docstring")

    client.post(f"/reviews/{plan['id']}/decision", json={"decision": "approve"})
    review = _until(client, plan["id"], lambda r: r["meta"].get("next") or r["meta"].get("error"))
    assert "error" not in review["meta"], review["meta"]
    diff = client.get(f"/reviews/{review['meta']['next']}").json()
    assert (
        diff["kind"] == "diff" and "app/auth.py" in diff["content"] and diff["meta"]["parent"] == plan["id"]
    )

    client.post(f"/reviews/{diff['id']}/decision", json={"decision": "approve"})
    diff = _until(client, diff["id"], lambda r: r["meta"].get("applied") or r["meta"].get("error"))
    assert diff["meta"]["applied"] == ["app/auth.py"]
    assert "End the user" in (sample_repo / "app/auth.py").read_text()
