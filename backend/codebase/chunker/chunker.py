"""Turns a source file into search chunks.

Code files are split along symbol boundaries:

* every function / method becomes its own chunk (long ones are split),
* a class chunk holds the class header (signature, fields, docstring) only,
  so method bodies are not indexed twice,
* top-level code outside any symbol (imports, constants, scripts) becomes
  `module` chunks, so files without functions are still searchable.

Markdown is split by headings; other text files by fixed line windows.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from backend.codebase.parser.languages import CODE_LANGUAGES
from backend.codebase.parser.symbols import Symbol, extract_symbols
from backend.models.code_chunk import CodeChunk

CLASS_HEADER_MAX_LINES = 40

_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "catch",
    "with",
    "print",
    "elif",
    "function",
    "def",
    "class",
    "new",
    "super",
    "typeof",
    "sizeof",
    "await",
    "assert",
    "lambda",
    "not",
    "and",
    "or",
    "in",
    "yield",
    "match",
    "isinstance",
    "len",
    "str",
    "int",
    "float",
    "list",
    "dict",
    "set",
    "tuple",
    "range",
    "fn",
    "import",
    "async",
    "from",
    "except",
    "else",
    "try",
    "raise",
    "del",
}
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)")


def build_chunks(
    file_path: str,
    content: str,
    language: str,
    max_lines: int = 120,
) -> list[CodeChunk]:
    lines = content.splitlines()
    if not any(line.strip() for line in lines):
        return []

    if language in CODE_LANGUAGES:
        return _code_chunks(file_path, lines, language, max_lines)
    if language == "markdown":
        return _markdown_chunks(file_path, lines, max_lines)
    return _window_chunks(file_path, lines, language, "section", file_path, 1, max_lines)


# ----------------------------------------------------------------- code
def _code_chunks(file_path: str, lines: list[str], language: str, max_lines: int) -> list[CodeChunk]:
    symbols = extract_symbols("\n".join(lines), language)
    chunks: list[CodeChunk] = []
    covered: set[int] = set()

    methods_by_class: dict[str, list[Symbol]] = {}
    for symbol in symbols:
        if symbol.kind == "method" and symbol.parent:
            methods_by_class.setdefault(symbol.parent, []).append(symbol)

    for symbol in symbols:
        covered.update(range(symbol.start_line, symbol.end_line + 1))
        start, end = symbol.start_line, symbol.end_line

        if symbol.kind == "class":
            first_method = min(
                (m.start_line for m in methods_by_class.get(symbol.name, []) if m.start_line > start),
                default=end + 1,
            )
            end = min(end, first_method - 1, start + CLASS_HEADER_MAX_LINES - 1)

        chunks.extend(
            _window_chunks(
                file_path,
                lines[start - 1 : end],
                language,
                symbol.kind,
                symbol.qualified_name,
                start,
                max_lines,
            )
        )

    chunks.extend(_module_chunks(file_path, lines, language, covered, max_lines))
    return sorted(chunks, key=lambda c: (c.start_line, c.end_line))


def _module_chunks(
    file_path: str, lines: list[str], language: str, covered: set[int], max_lines: int
) -> list[CodeChunk]:
    """Chunks for contiguous runs of top-level code outside every symbol."""
    chunks: list[CodeChunk] = []
    run: list[int] = []

    def flush() -> None:
        if sum(1 for n in run if lines[n - 1].strip()) >= 2:
            chunks.extend(
                _window_chunks(
                    file_path,
                    [lines[n - 1] for n in run],
                    language,
                    "module",
                    _module_name(file_path),
                    run[0],
                    max_lines,
                )
            )
        run.clear()

    for number in range(1, len(lines) + 1):
        if number in covered:
            flush()
        else:
            run.append(number)
    flush()
    return chunks


def _module_name(file_path: str) -> str:
    return file_path.rsplit("/", 1)[-1]


# -------------------------------------------------------------- markdown
def _markdown_chunks(file_path: str, lines: list[str], max_lines: int) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    title = _module_name(file_path)
    start = 1
    for index, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if match and index > start:
            chunks.extend(
                _window_chunks(
                    file_path, lines[start - 1 : index - 1], "markdown", "section", title, start, max_lines
                )
            )
            start = index
        if match:
            title = match.group(1).strip() or title
    chunks.extend(
        _window_chunks(file_path, lines[start - 1 :], "markdown", "section", title, start, max_lines)
    )
    return [c for c in chunks if c.content.strip()]


# --------------------------------------------------------------- helpers
def _window_chunks(
    file_path: str,
    lines: list[str],
    language: str,
    chunk_type: str,
    name: str,
    first_line: int,
    max_lines: int,
) -> list[CodeChunk]:
    """Split `lines` into chunks of at most `max_lines` lines."""
    if not lines:
        return []
    windows = [lines[i : i + max_lines] for i in range(0, len(lines), max_lines)]
    chunks = []
    for index, window in enumerate(windows):
        start = first_line + index * max_lines
        symbol_name = name if len(windows) == 1 else f"{name} (part {index + 1})"
        chunks.append(_make_chunk(file_path, language, chunk_type, symbol_name, start, window))
    return chunks


def _make_chunk(
    file_path: str, language: str, chunk_type: str, name: str, start: int, lines: list[str]
) -> CodeChunk:
    content = "\n".join(lines)
    end = start + len(lines) - 1
    digest = hashlib.md5(f"{file_path}\0{chunk_type}\0{name}\0{start}\0{end}\0{content}".encode()).hexdigest()
    base_name = name.rsplit(".", 1)[-1].split(" ")[0]
    calls = sorted(
        {match for match in _CALL_RE.findall(content) if match not in _KEYWORDS and match != base_name}
    )
    return CodeChunk(
        chunk_id=str(uuid.UUID(hex=digest)),
        file_path=file_path,
        language=language,
        chunk_type=chunk_type,
        symbol_name=name,
        start_line=start,
        end_line=end,
        content=content,
        calls=calls if language in CODE_LANGUAGES else [],
    )
