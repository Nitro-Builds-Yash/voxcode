import pytest

from backend.execution.diff.diff_utils import UnsafeDiffError, extract_diff, files_in_diff
from backend.execution.patch import patch_service
from backend.execution.testing.test_runner import detect_test_command, run_tests_with_patch

RAW_DIFF = """--- a/app/auth.py
+++ b/app/auth.py
@@ -23,2 +23,3 @@
 def find_user(username):
+    # TODO: query the database
     return None
--- /dev/null
+++ b/app/new.py
@@ -0,0 +1 @@
+VALUE = 1
"""
DIFF = extract_diff(RAW_DIFF)


def test_extract_diff_from_fenced_reply():
    reply = f"Here you go:\n```diff\n{RAW_DIFF}```\nThanks"
    extracted = extract_diff(reply)
    assert extracted.startswith("diff --git a/app/auth.py b/app/auth.py\n--- a/app/auth.py")
    assert "diff --git a/app/new.py b/app/new.py\nnew file mode 100644\n--- /dev/null" in extracted
    assert extract_diff("no patch here") is None
    assert files_in_diff(DIFF) == [("app/auth.py", "app/auth.py"), (None, "app/new.py")]


def test_unsafe_paths_rejected(sample_repo):
    evil = "--- a/../outside.py\n+++ b/../outside.py\n@@ -0,0 +1 @@\n+x\n"
    with pytest.raises(UnsafeDiffError):
        patch_service.check(sample_repo, evil)


def test_check_preview_apply_undo(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(patch_service, "CACHE_DIR", tmp_path / "cache")
    ok, output = patch_service.check(sample_repo, DIFF)
    assert ok, output

    changes = {c.path: c for c in patch_service.preview(sample_repo, DIFF)}
    assert changes["app/new.py"].status == "added"
    assert "TODO" in changes["app/auth.py"].proposed and "TODO" not in changes["app/auth.py"].original
    assert not (sample_repo / "app/new.py").exists()

    patch_service.apply(sample_repo, DIFF)
    assert (sample_repo / "app/new.py").exists()
    patch_service.undo_last(sample_repo)
    assert not (sample_repo / "app/new.py").exists()
    assert "TODO" not in (sample_repo / "app/auth.py").read_text()


def test_tests_run_against_patch_in_sandbox(sample_repo):
    (sample_repo / "tests").mkdir()
    (sample_repo / "tests/test_value.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\n"
        "def test_value():\n    from app.new import VALUE\n    assert VALUE == 1\n"
    )
    assert detect_test_command(sample_repo)[-4:] == ["pytest", "-x", "-q", "--no-header"]
    report = run_tests_with_patch(sample_repo, DIFF, timeout=120)
    assert report.passed, report.output
    assert not (sample_repo / "app/new.py").exists()
