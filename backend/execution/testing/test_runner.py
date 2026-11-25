"""Run a repository's tests against a patch, in a throwaway copy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from backend.codebase.indexer.scanner import IGNORED_DIRS
from backend.execution.diff.diff_utils import normalize_diff

# Heavy directories linked (not copied) into the sandbox so tests can import them.
_LINKED = {"node_modules", ".venv", "venv", "target"}
_MAX_COPY_BYTES = 300 * 1024 * 1024


@dataclass
class TestReport:
    __test__ = False  # not a pytest test class

    command: str
    passed: bool
    output: str
    timed_out: bool = False


def detect_test_command(repo: Path) -> list[str] | None:
    package_json = repo / "package.json"
    if package_json.exists():
        try:
            script = json.loads(package_json.read_text()).get("scripts", {}).get("test", "")
        except ValueError:
            script = ""
        if script and "no test specified" not in script:
            return ["npm", "test", "--silent"]
    if (repo / "go.mod").exists():
        return ["go", "test", "./..."]
    if (repo / "Cargo.toml").exists():
        return ["cargo", "test", "--quiet"]
    pyproject = repo / "pyproject.toml"
    if (
        (repo / "pytest.ini").exists()
        or (repo / "conftest.py").exists()
        or (repo / "tests").is_dir()
        or (pyproject.exists() and "[tool.pytest" in pyproject.read_text(errors="ignore"))
    ):
        return [sys.executable, "-m", "pytest", "-x", "-q", "--no-header"]
    return None


def _copy_repo(repo: Path, target: Path) -> None:
    total = 0

    def ignore(directory: str, names: list[str]) -> set[str]:
        nonlocal total
        skipped = {n for n in names if n in IGNORED_DIRS or n in _LINKED}
        for name in names:
            if name not in skipped:
                path = Path(directory) / name
                if path.is_file():
                    total += path.stat().st_size
        if total > _MAX_COPY_BYTES:
            raise RuntimeError("Repository too large to copy for a sandboxed test run")
        return skipped

    shutil.copytree(repo, target, ignore=ignore, symlinks=True)
    for name in _LINKED:
        if (repo / name).is_dir():
            os.symlink(repo / name, target / name)


def run_tests_with_patch(repo: Path, diff: str, timeout: int) -> TestReport | None:
    command = detect_test_command(repo)
    if command is None:
        return None
    with tempfile.TemporaryDirectory(prefix="voxcode-tests-") as tmp:
        sandbox = Path(tmp) / "repo"
        _copy_repo(repo, sandbox)
        applied = subprocess.run(
            ["git", "apply", "--recount", "-"],
            cwd=sandbox,
            input=normalize_diff(diff),
            capture_output=True,
            text=True,
        )
        if applied.returncode != 0:
            return TestReport(" ".join(command), False, applied.stderr)
        try:
            result = subprocess.run(
                command,
                cwd=sandbox,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "CI": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return TestReport(" ".join(command), False, output[-4000:], timed_out=True)
        output = (result.stdout + "\n" + result.stderr).strip()
        return TestReport(" ".join(command), result.returncode == 0, output[-4000:])


def run_tests(repo: Path, timeout: int) -> TestReport | None:
    """Run the repository's tests in place (no patch)."""
    command = detect_test_command(repo)
    if command is None:
        return None
    try:
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return TestReport(" ".join(command), False, "Timed out", timed_out=True)
    output = (result.stdout + "\n" + result.stderr).strip()
    return TestReport(" ".join(command), result.returncode == 0, output[-4000:])
