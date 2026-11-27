"""Run a repository's tests against a patch, in a throwaway copy."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from voxcode.codebase.indexer.scanner import IGNORED_DIRS
from voxcode.execution.diff.diff_utils import normalize_diff
from voxcode.execution.testing.docker_sandbox import container_test_command, run_in_docker, use_docker

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
    cargo = repo / "Cargo.toml"
    if cargo.exists():
        workspace = "[workspace]" in cargo.read_text(errors="ignore")
        return ["cargo", "test", "--quiet", *(["--workspace"] if workspace else [])]
    pyproject = repo / "pyproject.toml"
    if (
        (repo / "pytest.ini").exists()
        or (repo / "conftest.py").exists()
        or (repo / "tests").is_dir()
        or (pyproject.exists() and "[tool.pytest" in pyproject.read_text(errors="ignore"))
    ):
        return [sys.executable, "-m", "pytest", "-x", "-q", "--no-header"]
    return None


def _copy_repo(repo: Path, target: Path, link_dependencies: bool = True) -> None:
    """Copy the repo, linking (not copying) dependency folders at any depth.

    With link_dependencies=False (Docker) they are skipped instead: host
    paths do not exist inside the container and deps are installed there.
    """
    total = 0
    links: list[Path] = []

    def ignore(directory: str, names: list[str]) -> set[str]:
        nonlocal total
        here = Path(directory)
        skipped: set[str] = set()
        for name in names:
            path = here / name
            if name in _LINKED and path.is_dir():
                links.append(path.relative_to(repo))
                skipped.add(name)
            elif name in IGNORED_DIRS:
                skipped.add(name)
            elif path.is_file():
                total += path.stat().st_size
        if total > _MAX_COPY_BYTES:
            raise RuntimeError("Repository too large to copy for a sandboxed test run")
        return skipped

    shutil.copytree(repo, target, ignore=ignore, symlinks=True)
    if not link_dependencies:
        return
    for rel in links:
        os.symlink(repo / rel, target / rel, target_is_directory=True)


def run_tests_with_patch(repo: Path, diff: str, timeout: int) -> TestReport | None:
    """Apply `diff` to a copy of the repo and run its tests there.

    Uses a network-less Docker container when TEST_SANDBOX allows it,
    otherwise a temporary directory on this machine.
    """
    command = detect_test_command(repo)
    if command is None:
        return None
    in_docker = use_docker()
    label = ("docker: " if in_docker else "") + " ".join(
        container_test_command(command) if in_docker else command
    )
    with tempfile.TemporaryDirectory(prefix="voxcode-tests-") as tmp:
        sandbox = Path(tmp) / "repo"
        _copy_repo(repo, sandbox, link_dependencies=not in_docker)
        applied = subprocess.run(
            ["git", "apply", "--recount", "-"],
            cwd=sandbox,
            input=normalize_diff(diff),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if applied.returncode != 0:
            return TestReport(label, False, applied.stderr)
        if in_docker:
            code, output, timed_out = run_in_docker(sandbox, command, timeout)
            return TestReport(label, code == 0 and not timed_out, output[-4000:], timed_out=timed_out)
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
            return TestReport(label, False, output[-4000:], timed_out=True)
        output = (result.stdout + "\n" + result.stderr).strip()
        return TestReport(label, result.returncode == 0, output[-4000:])


_FAILURE_PATTERNS = [
    re.compile(r'File "([^"]+)", line (\d+)'),  # Python tracebacks
    re.compile(r"^(?:FAILED|ERROR) ([\w./-]+\.py)::", re.M),  # pytest summary
    re.compile(r"^([\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java)):(\d+)", re.M),  # path:line
    re.compile(r"\(([\w./-]+\.(?:ts|tsx|js|jsx)):(\d+):\d+\)"),  # JS stack frames
    re.compile(r"panicked at ([\w./-]+\.rs):(\d+):\d+"),  # Rust test panics
    re.compile(r"^\s*--> ([\w./-]+\.rs):(\d+):\d+", re.M),  # rustc errors
    re.compile(r"^---- ([\w:]+) stdout ----", re.M),  # failing Rust test name (resolved below)
]


def failure_files(output: str, repo: Path, sandbox_prefix: str = "") -> list[str]:
    """Repository files mentioned in a failing test run (tests and traces)."""
    found: list[str] = []
    for pattern in _FAILURE_PATTERNS:
        for match in pattern.finditer(output):
            raw = match.group(1)
            if "::" in raw or (pattern.pattern.startswith("^----")):
                raw = _rust_test_file(raw, repo) or ""
            for prefix in (sandbox_prefix, "/work/", str(repo) + "/"):
                if prefix and raw.startswith(prefix):
                    raw = raw[len(prefix) :]
            if "voxcode-tests-" in raw:
                raw = raw.split("/repo/", 1)[-1]
            rel = raw.lstrip("./")
            if rel and not rel.startswith("/") and (repo / rel).is_file() and rel not in found:
                found.append(rel)
    return found[:5]


def _rust_test_file(test_path: str, repo: Path) -> str | None:
    """`store::tests::saves` -> the file defining `fn saves` (src/store.rs)."""
    *modules, name = test_path.split("::")
    modules = [m for m in modules if m != "tests"]
    candidates = [f"src/{'/'.join(modules)}.rs", f"src/{'/'.join(modules)}/mod.rs"] if modules else []
    candidates += ["src/lib.rs", "src/main.rs"]
    if not modules:
        candidates += [p.relative_to(repo).as_posix() for p in sorted((repo / "tests").glob("*.rs"))]
    for rel in candidates:
        path = repo / rel
        if path.is_file() and re.search(rf"\bfn\s+{re.escape(name)}\b", path.read_text(errors="ignore")):
            return rel
    return None


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
