"""Repository identity and path validation shared by indexing and agents."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from backend.config.settings import settings


class RepositoryPathError(ValueError):
    """Raised when a repository path is missing or not allowed."""


def resolve_repository_path(path: str) -> Path:
    """Resolve and validate a repository path supplied by a client."""
    if not path:
        raise RepositoryPathError("repository_path is required")

    resolved = Path(path).expanduser().resolve()

    if not resolved.is_dir():
        raise RepositoryPathError(f"Not a directory: {resolved}")

    if resolved == Path(resolved.anchor):
        raise RepositoryPathError("Refusing to use the filesystem root as a repository")

    roots = [Path(root).expanduser().resolve() for root in settings.allowed_repo_roots]
    if roots and not any(resolved == root or root in resolved.parents for root in roots):
        raise RepositoryPathError(f"{resolved} is outside ALLOWED_REPO_ROOTS ({', '.join(map(str, roots))})")

    return resolved


def repository_key(path: Path | str) -> str:
    """Stable, filesystem-safe id for a repository.

    Includes a hash of the absolute path so two checkouts that share a folder
    name never share an index.
    """
    resolved = Path(path).expanduser().resolve()
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", resolved.name).strip("-").lower() or "repo"
    digest = hashlib.sha1(str(resolved).encode()).hexdigest()[:8]
    return f"{slug}-{digest}"
