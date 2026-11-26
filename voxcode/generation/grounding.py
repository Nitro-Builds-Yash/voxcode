"""Is an answer grounded in the code it was given?

A cheap, model-free hallucination check run on every answer: code
identifiers the answer mentions (`backticked` spans, snake_case, CamelCase,
dotted.names, calls()) must appear in the retrieved context or exist in the
repository, and cited file paths must exist. The score drives cascade
escalation and is returned with each answer (and in `voxcode eval --answers`).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

_BACKTICK = re.compile(r"`([^`\n]{1,120})`")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_CODEY = re.compile(
    r"\b(?:[a-z]+_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]+|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]+|"
    r"[A-Za-z_]\w*\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*|[A-Za-z_]\w*(?=\(\)))"
)
_PATH = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|pyi|js|jsx|ts|tsx|rs|go|java|kt|rb|php|cs|cpp|c|h|toml|json|ya?ml|md|cfg|ini))"
    r"(?::\d+(?:-\d+)?)?(?![\w/])"
)
# Words that look like code but are general knowledge, not repository claims.
_COMMON = {
    "e.g", "i.e", "etc", "vs", "self", "cls", "this", "None", "True", "False", "null", "true", "false",
    "print", "len", "str", "int", "dict", "list", "set", "tuple", "bool", "float", "bytes", "object",
    "range", "open", "isinstance", "super", "async", "await", "return", "import", "from", "def", "class",
    "pytest", "numpy", "np", "os.path", "os.environ", "sys.argv", "json.loads", "json.dumps", "re.compile",
    "typing", "dataclass", "Optional", "Exception", "ValueError", "TypeError", "KeyError", "RuntimeError",
    "README.md", "git", "pip", "npm", "cargo", "http", "https", "localhost", "api_key", "e2e",
}  # fmt: skip


@dataclass
class Grounding:
    groundedness: float = 1.0
    checked: int = 0
    unsupported: list[str] = field(default_factory=list)
    citations: int = 0
    bad_citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _claims(answer: str) -> set[str]:
    claims: set[str] = set()
    for span in _BACKTICK.findall(answer):
        if " " in span.strip() and not span.strip().endswith(")"):
            continue  # a phrase or shell command, not one identifier
        for ident in _IDENT.findall(span):
            claims.add(ident)
    prose = _BACKTICK.sub(" ", answer)
    prose = re.sub(r"```.*?```", " ", prose, flags=re.S)
    claims.update(m.group(0) for m in _CODEY.finditer(prose))
    return {c.rstrip(".") for c in claims if len(c) > 2 and c not in _COMMON and not _PATH.fullmatch(c)}


def check(answer: str, context: str, symbols: set[str], files: set[str]) -> Grounding:
    """`symbols`: short and qualified names defined in the repository."""
    context_words = set(_IDENT.findall(context))
    context_parts = {p for w in context_words for p in w.split(".")}
    known = context_words | context_parts | symbols
    unsupported = []
    claims = _claims(answer)
    for claim in sorted(claims):
        parts = claim.split(".")
        if claim in known or parts[-1] in known or (len(parts) > 1 and all(p in known for p in parts)):
            continue
        if any(claim in f or claim == f.rsplit("/", 1)[-1].rsplit(".", 1)[0] for f in files):
            continue  # a module or file name
        unsupported.append(claim)
    cited = {m.group(1) for m in _PATH.finditer(answer)}
    bad = sorted(
        c for c in cited if c not in files and not any(f.endswith("/" + c) or f.endswith(c) for f in files)
    )
    checked = len(claims)
    score = 1.0 if checked == 0 else (checked - len(unsupported)) / checked
    if cited:  # a made-up file is a strong hallucination signal
        score *= (len(cited) - len(bad)) / len(cited)
    return Grounding(round(score, 3), checked, unsupported[:15], len(cited), bad[:10])


def repository_names(chunks) -> tuple[set[str], set[str]]:
    """(symbol names, file paths) of an index's chunks."""
    symbols: set[str] = set()
    for chunk in chunks:
        name = chunk.symbol_name.split(" (part")[0]
        symbols.add(name)
        symbols.update(name.split("."))
    return symbols, {c.file_path for c in chunks}
