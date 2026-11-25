"""File-extension to language mapping."""

from pathlib import PurePath

EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".md": "markdown",
    ".toml": "config",
    ".yaml": "config",
    ".yml": "config",
}

# Languages whose structure we extract with tree-sitter (or `ast` for Python).
CODE_LANGUAGES = {"python", "javascript", "typescript", "tsx", "java", "go", "rust"}


def detect_language(path: str | PurePath) -> str | None:
    return EXTENSION_LANGUAGES.get(PurePath(path).suffix.lower())
