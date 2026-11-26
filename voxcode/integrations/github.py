"""Apply a patch on a new git branch (and optionally open a GitHub PR)
without touching the user's working tree.

A temporary `git worktree` is created from HEAD, the patch is applied and
committed there, the branch is pushed with the user's own git credentials,
and the PR is opened through the GitHub REST API with GITHUB_TOKEN.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from voxcode.config.settings import settings
from voxcode.execution.diff.diff_utils import normalize_diff, touched_paths


class GitError(RuntimeError):
    pass


@dataclass
class BranchResult:
    branch: str
    commit: str
    files: list[str]
    pushed: bool = False
    pull_request_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _git(repo: Path, *args: str, input: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, input=input, timeout=120
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def branch_name(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "change"
    return f"voxcode/{slug}-{time.strftime('%Y%m%d-%H%M%S')}"


def parse_github_remote(url: str) -> tuple[str, str] | None:
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return (match.group(1), match.group(2)) if match else None


def commit_on_branch(
    repo: Path,
    diff: str,
    title: str,
    body: str = "",
    push: bool = False,
    create_pr: bool = False,
    base: str | None = None,
) -> BranchResult:
    if not (repo / ".git").exists():
        raise GitError("Branch and pull-request modes need a git repository")
    base = base or _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_name(title)
    files = touched_paths(diff)

    with tempfile.TemporaryDirectory(prefix="voxcode-branch-") as tmp:
        worktree = Path(tmp) / "wt"
        _git(repo, "worktree", "add", "-b", branch, str(worktree), "HEAD")
        try:
            try:
                _git(worktree, "apply", "--recount", "--whitespace=nowarn", "-", input=normalize_diff(diff))
            except GitError as exc:
                raise GitError(
                    f"{exc}. The patch was written against your working tree; commit or stash "
                    "local changes to those files first."
                ) from exc
            _git(worktree, "add", "--", *files)
            message = f"{title}\n\n{body}".strip() + "\n\nGenerated with VoxCode"
            _git(
                worktree,
                "-c", "user.name=" + (_config(repo, "user.name") or "VoxCode"),
                "-c", "user.email=" + (_config(repo, "user.email") or "voxcode@localhost"),
                "commit", "-m", message,
            )  # fmt: skip
            commit = _git(worktree, "rev-parse", "HEAD")
            result = BranchResult(branch=branch, commit=commit, files=files)
            if push or create_pr:
                _git(worktree, "push", "-u", "origin", branch)
                result.pushed = True
            if create_pr:
                result.pull_request_url = open_pull_request(repo, branch, base, title, body)
            return result
        finally:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)], capture_output=True
            )


def _config(repo: Path, key: str) -> str:
    try:
        return _git(repo, "config", "--get", key)
    except GitError:
        return ""


def open_pull_request(
    repo: Path, branch: str, base: str, title: str, body: str, client: httpx.Client | None = None
) -> str:
    if not settings.GITHUB_TOKEN:
        raise GitError("Set GITHUB_TOKEN to open pull requests (the branch was pushed)")
    remote = parse_github_remote(_git(repo, "remote", "get-url", "origin"))
    if remote is None:
        raise GitError("origin is not a GitHub repository")
    owner, name = remote
    client = client or httpx.Client(timeout=30)
    response = client.post(
        f"https://api.github.com/repos/{owner}/{name}/pulls",
        headers={"Authorization": f"Bearer {settings.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
        json={"title": title, "head": branch, "base": base, "body": body + "\n\n_Generated with VoxCode._"},
    )
    if response.status_code >= 400:
        raise GitError(f"GitHub API error {response.status_code}: {response.text[:300]}")
    return response.json()["html_url"]
