"""Run tests inside a throwaway Docker container.

Phase 1 installs dependencies with network access; the container is then
disconnected from every network before the (untrusted) test suite runs, with
memory, CPU and process limits, as a non-root user, and is always removed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path

from voxcode.config.settings import settings


@lru_cache(maxsize=1)
def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def use_docker() -> bool:
    mode = settings.TEST_SANDBOX.lower()
    if mode == "docker":
        if not docker_available():
            raise RuntimeError("TEST_SANDBOX=docker but Docker is not available")
        return True
    return mode == "auto" and docker_available()


def install_command(repo: Path) -> str | None:
    """Shell command that installs the project's dependencies (needs network)."""
    if (repo / "package.json").exists():
        return "npm ci --no-audit --no-fund || npm install --no-audit --no-fund"
    if (repo / "go.mod").exists():
        return "go mod download"
    if (repo / "Cargo.toml").exists():
        return "cargo fetch --locked || cargo fetch"
    steps = ["pip install -q pytest"]
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        steps.append("(pip install -q -e '.[dev]' || pip install -q -e '.[test]' || pip install -q -e .)")
    if (repo / "requirements.txt").exists():
        steps.append("pip install -q -r requirements.txt")
    for extra in ("requirements-dev.txt", "requirements-test.txt"):
        if (repo / extra).exists():
            steps.append(f"pip install -q -r {extra}")
    return " && ".join(steps)


def container_test_command(command: list[str]) -> list[str]:
    """Host command -> the same command inside the container."""
    if len(command) > 2 and command[1:3] == ["-m", "pytest"]:
        return ["python", *command[1:]]
    if command[:2] == ["cargo", "test"]:
        return [*command, "--offline"]  # the network is gone by then; deps were fetched
    return command


# Default images when SANDBOX_IMAGE is left at its Python default.
_LANGUAGE_IMAGES = {"Cargo.toml": "rust:1-slim", "go.mod": "golang:1", "package.json": "node:22-slim"}
_DEFAULT_IMAGE = "python:3.12-slim"


def sandbox_image(repo: Path) -> str:
    """The configured image, or one that has the repository's toolchain."""
    if settings.SANDBOX_IMAGE != _DEFAULT_IMAGE:
        return settings.SANDBOX_IMAGE
    for marker, image in _LANGUAGE_IMAGES.items():
        if (repo / marker).exists():
            return image
    return _DEFAULT_IMAGE


def build_run_args(name: str, workdir: Path, image: str) -> list[str]:
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    gid = os.getgid() if hasattr(os, "getgid") else 1000
    return [
        "docker", "run", "-d", "--name", name,
        "--memory", settings.SANDBOX_MEMORY,
        "--cpus", settings.SANDBOX_CPUS,
        "--pids-limit", "512",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "-e", "HOME=/tmp", "-e", "CI=1", "-e", "PIP_USER=1", "-e", "PYTHONUSERBASE=/tmp/.local",
        "-e", "CARGO_HOME=/tmp/.cargo", "-e", "CARGO_TARGET_DIR=/tmp/target", "-e", "GOPATH=/tmp/go",
        "-e", "GOCACHE=/tmp/.gocache", "-e", "npm_config_cache=/tmp/.npm",
        "-e", "PATH=/tmp/.local/bin:/usr/local/cargo/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--user", f"{uid}:{gid}",
        "-v", f"{workdir}:/work",
        "-w", "/work",
        image, "sleep", "infinity",
    ]  # fmt: skip


def run_in_docker(workdir: Path, command: list[str], timeout: int, image: str | None = None):
    """Install deps, cut the network, run `command`. Returns (exit code, output, timed_out)."""
    image = image or sandbox_image(workdir)
    name = f"voxcode-test-{uuid.uuid4().hex[:10]}"

    def docker(*args: str, check_timeout: int = 900) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=check_timeout)

    started = subprocess.run(
        build_run_args(name, workdir, image), capture_output=True, text=True, timeout=300
    )
    if started.returncode != 0:
        return 1, f"Could not start sandbox container: {started.stderr.strip()}", False
    try:
        install = install_command(workdir)
        if install:
            result = docker("exec", name, "sh", "-c", install)
            if result.returncode != 0:
                return (
                    result.returncode,
                    f"Dependency install failed:\n{result.stdout}\n{result.stderr}"[-4000:],
                    False,
                )
        # No network for the tests themselves.
        networks = docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", name).stdout.strip()
        for network in json.loads(networks or "{}"):
            docker("network", "disconnect", "-f", network, name)
        try:
            result = docker("exec", name, *container_test_command(command), check_timeout=timeout)
        except subprocess.TimeoutExpired:
            return 1, "Timed out", True
        return result.returncode, (result.stdout + "\n" + result.stderr).strip(), False
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
