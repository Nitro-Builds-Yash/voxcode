"""Plan and patch review: feedback, store, Claude Code hook, Obsidian export."""

import io
import json
import threading
import time

import pytest

from voxcode.config.settings import settings
from voxcode.review import hooks
from voxcode.review.feedback import format_feedback, revision_diff, title_from
from voxcode.review.models import Annotation, Review
from voxcode.review.store import ReviewStore, export_to_obsidian

PLAN = "# Add rate limiting\n\n1. Add a token bucket.\n2. Store counters in a global dict.\n"


def test_feedback_lists_every_annotation_kind():
    review = Review(revisions=[PLAN])
    review.annotations = [
        Annotation(
            kind="replace",
            quote="Store counters in a global dict.",
            replacement="Use Redis.",
            comment="Multiple workers.",
        ),
        Annotation(kind="delete", quote="token bucket"),
        Annotation(kind="comment", quote="rate limiting", comment="Per route, please."),
        Annotation(kind="line", file="app/auth.py", line=7, quote="return x", comment="Use a salt."),
        Annotation(kind="comment", comment="old", revision=5),  # another revision: ignored
    ]
    text = format_feedback(review, "Exempt /health.")
    assert '1. Replace "Store counters in a global dict." with "Use Redis.". Multiple workers.' in text
    assert '2. Remove "token bucket".' in text
    assert '3. On "rate limiting": Per route, please.' in text
    assert '4. On `app/auth.py` line 7 "return x": Use a salt.' in text
    assert "## Overall\nExempt /health." in text and "old" not in text
    assert "without comments" in format_feedback(Review(revisions=[PLAN]))


def test_revision_diff_and_title():
    ops = revision_diff("a\nb\nc", "a\nB\nc\nd")
    assert [o["op"] for o in ops] == ["equal", "remove", "add", "equal", "add"]
    assert title_from("\n\n## My plan\nbody") == "My plan" and title_from("", "x") == "x"


def test_store_decide_wait_revise_and_archive(tmp_path):
    store = ReviewStore(tmp_path)
    review = store.create("plan", PLAN, "Rate limiting", "claude-code", {"claude_session": "s1"})
    store.annotate(review.id, Annotation(comment="Why a dict?"))

    results = []
    waiter = threading.Thread(target=lambda: results.append(store.wait(review.id, 0, timeout=5)))
    waiter.start()
    time.sleep(0.2)
    store.decide(review.id, "denied", "Use Redis")
    waiter.join()
    assert results[0].decision == "denied" and "Why a dict?" in results[0].feedback
    with pytest.raises(ValueError):
        store.decide(review.id, "approved")

    fresh = ReviewStore(tmp_path)  # a new process (the next hook call) finds it on disk
    found = fresh.find_open(claude_session="s1")
    assert found is not None and found.id == review.id
    revised = fresh.revise(review.id, PLAN + "3. Use Redis.\n")
    assert revised.status == "pending" and revised.revision == 1 and not revised.current_annotations()
    assert fresh.wait(review.id, 1, timeout=0.1) is None
    assert [r["id"] for r in fresh.list()] == [review.id]


def test_approved_plans_are_exported_to_obsidian(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(settings, "OBSIDIAN_VAULT", str(vault))
    store = ReviewStore(tmp_path / "reviews")
    review = store.create("plan", PLAN, "Add rate limiting", "voxcode", {"repository_path": "/work/shop-api"})
    store.decide(review.id, "approved")
    note = next((vault / "VoxCode" / "Plans").glob("*-add-rate-limiting.md"))
    text = note.read_text()
    assert text.startswith('---\ntitle: "Add rate limiting"') and "tags: [voxcode, plan, shop-api]" in text
    assert "Store counters" in text
    monkeypatch.setattr(settings, "OBSIDIAN_VAULT", "")
    assert export_to_obsidian(review) is None


def test_claude_hook_install_is_idempotent_and_keeps_other_hooks(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks.Path, "home", lambda: tmp_path)
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PermissionRequest": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "other"}]}
                    ]
                },
            }
        )
    )
    hooks.install_claude()
    hooks.install_claude()
    data = json.loads(path.read_text())
    entries = data["hooks"]["PermissionRequest"]
    assert data["model"] == "opus" and len(entries) == 2
    assert entries[1] == {
        "matcher": "ExitPlanMode",
        "hooks": [{"type": "command", "command": "voxcode hook claude-plan", "timeout": 345600}],
    }
    assert list(path.parent.glob("settings.json.bak-*"))
    assert hooks.uninstall_claude() and not hooks.uninstall_claude()
    assert json.loads(path.read_text())["hooks"]["PermissionRequest"] == entries[:1]


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ({"decision": "approved"}, {"behavior": "allow"}),
        (
            {"decision": "denied", "feedback": "1. Use Redis."},
            {"behavior": "deny", "message": "1. Use Redis."},
        ),
    ],
)
def test_claude_plan_hook_output(decision, expected):
    seen = {}

    def fake_review(content, kind, title, source, meta):
        seen.update(content=content, title=title, source=source, meta=meta)
        return decision

    payload = {"session_id": "abc", "cwd": "/work", "tool_name": "ExitPlanMode", "tool_input": {"plan": PLAN}}
    out = io.StringIO()
    assert hooks.claude_plan_hook(io.StringIO(json.dumps(payload)), out, fake_review) == 0
    assert json.loads(out.getvalue()) == {
        "hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": expected}
    }
    assert seen["title"] == "Add rate limiting" and seen["source"] == "claude-code"
    assert seen["meta"] == {"claude_session": "abc", "cwd": "/work", "repository_path": "/work"}


def test_claude_plan_hook_ignores_empty_plans():
    out = io.StringIO()
    assert (
        hooks.claude_plan_hook(io.StringIO('{"tool_input": {}}'), out, lambda *a: pytest.fail("no review"))
        == 0
    )
    assert out.getvalue() == ""
