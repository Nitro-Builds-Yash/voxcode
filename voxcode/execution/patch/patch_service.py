"""Validate, preview, apply and revert unified diffs with `git apply`.

`git apply` works in any directory, not just git repositories.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from voxcode.config.paths import CACHE_DIR
from voxcode.core.repository import repository_key
from voxcode.execution.diff.diff_utils import files_in_diff, normalize_diff, touched_paths

_GIT_APPLY = ["git", "apply", "--recount", "--whitespace=nowarn"]


class PatchError(RuntimeError):
    pass


@dataclass
class FileChange:
    path: str
    original: str
    proposed: str
    status: str  # modified | added | deleted


def _run_apply(cwd: Path, diff: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_GIT_APPLY, *extra, "-"],
        cwd=cwd,
        input=normalize_diff(diff),
        capture_output=True,
        text=True,
        timeout=60,
    )


def check(repo: Path, diff: str) -> tuple[bool, str]:
    touched_paths(diff)  # raises on unsafe paths
    if not files_in_diff(diff):
        return False, "No file headers (--- a/... / +++ b/...) found in the patch."
    result = _run_apply(repo, diff, "--check")
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def preview(repo: Path, diff: str) -> list[FileChange]:
    """Apply the patch to copies of the touched files and return before/after."""
    with tempfile.TemporaryDirectory(prefix="voxcode-preview-") as tmp:
        sandbox = Path(tmp)
        for rel in touched_paths(diff):
            source = repo / rel
            if source.is_file():
                target = sandbox / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        result = _run_apply(sandbox, diff)
        if result.returncode != 0:
            raise PatchError(result.stderr.strip() or "git apply failed")

        changes = []
        for old, new in files_in_diff(diff):
            path = new or old or ""
            before = (
                (repo / old).read_text(encoding="utf-8", errors="replace")
                if old and (repo / old).is_file()
                else ""
            )
            after = (
                (sandbox / new).read_text(encoding="utf-8", errors="replace")
                if new and (sandbox / new).is_file()
                else ""
            )
            status = "added" if old is None else "deleted" if new is None else "modified"
            changes.append(FileChange(path=path, original=before, proposed=after, status=status))
        return changes


def _undo_path(repo: Path) -> Path:
    return CACHE_DIR / "patches" / f"{repository_key(repo)}.last.diff"


def apply(repo: Path, diff: str) -> list[str]:
    ok, output = check(repo, diff)
    if not ok:
        raise PatchError(output)
    result = _run_apply(repo, diff)
    if result.returncode != 0:
        raise PatchError(result.stderr.strip())
    undo = _undo_path(repo)
    undo.parent.mkdir(parents=True, exist_ok=True)
    undo.write_text(diff, encoding="utf-8")
    return touched_paths(diff)


def undo_last(repo: Path) -> list[str]:
    undo = _undo_path(repo)
    if not undo.exists():
        raise PatchError("No applied patch to undo for this repository.")
    diff = undo.read_text(encoding="utf-8")
    result = _run_apply(repo, diff, "--reverse")
    if result.returncode != 0:
        raise PatchError(f"Could not undo (files changed since?): {result.stderr.strip()}")
    undo.unlink()
    return touched_paths(diff)
