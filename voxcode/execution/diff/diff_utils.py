"""Helpers for unified diffs produced by a model."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_FENCE_RE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)


class UnsafeDiffError(ValueError):
    pass


def extract_diff(text: str) -> str | None:
    """Pull a unified diff out of a model reply."""
    for block in _FENCE_RE.findall(text):
        if "--- " in block and "+++ " in block:
            return normalize_diff(block)
    start = text.find("--- ")
    if start != -1 and "+++ " in text[start:]:
        return normalize_diff(text[start:])
    return None


def normalize_diff(diff: str) -> str:
    lines = diff.strip("\n").splitlines()
    # Drop anything before the first file header.
    for index, line in enumerate(lines):
        if line.startswith(("diff --git", "--- ")):
            lines = lines[index:]
            break

    # Models usually omit `diff --git` lines. Without them `git apply --recount`
    # cannot tell where one file's last hunk ends and the next "--- " header
    # starts, so add them.
    output: list[str] = []
    for index, line in enumerate(lines):
        is_header = (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
            and not (output and output[-1].startswith(("diff --git", "index ", "new file", "deleted file")))
        )
        if is_header:
            old = line[4:].split("\t")[0].strip()
            new = lines[index + 1][4:].split("\t")[0].strip()
            path = (new if new != "/dev/null" else old).removeprefix("a/").removeprefix("b/")
            output.append(f"diff --git a/{path} b/{path}")
            if old == "/dev/null":
                output.append("new file mode 100644")
            elif new == "/dev/null":
                output.append("deleted file mode 100644")
        output.append(line)
    return "\n".join(output) + "\n"


def _clean_path(raw: str) -> str | None:
    path = raw.strip().split("\t")[0]
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not path:
        raise UnsafeDiffError(f"Refusing patch path outside the repository: {raw!r}")
    return pure.as_posix()


def files_in_diff(diff: str) -> list[tuple[str | None, str | None]]:
    """(old_path, new_path) per file; None means /dev/null (create/delete)."""
    files = []
    lines = diff.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("--- ") and index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
            files.append((_clean_path(line[4:]), _clean_path(lines[index + 1][4:])))
    return files


def touched_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for old, new in files_in_diff(diff):
        for path in (old, new):
            if path and path not in paths:
                paths.append(path)
    return paths
