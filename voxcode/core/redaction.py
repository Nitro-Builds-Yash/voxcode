"""Remove secrets from text before it is sent to a cloud LLM.

Source files and configs often hold API keys or passwords. When the active
provider is remote and REDACT_SECRETS is on, matches are replaced with
`[REDACTED:<kind>]` so they never leave the machine.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("groq-key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("connection-string", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]{3,}@")),
]

# key = "value" / KEY: value assignments whose name suggests a secret
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|private[_-]?key|token)[\w.-]*
     \s*[:=]\s*)
    (["']?)([^\s"',;]{6,})\2
    """
)
_PLACEHOLDERS = {"changeme", "change-me", "example", "xxxxxx", "your-key", "placeholder", "redacted"}


def redact(text: str) -> tuple[str, int]:
    """Return (redacted text, number of secrets removed)."""
    count = 0

    def sub(kind: str):
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return f"[REDACTED:{kind}]"

        return replace

    for kind, pattern in _PATTERNS:
        text = pattern.sub(sub(kind), text)

    def assignment(match: re.Match[str]) -> str:
        nonlocal count
        value = match.group(3)
        if value.lower() in _PLACEHOLDERS or value.startswith(
            ("[REDACTED", "os.environ", "settings.", "process.env", "$", "{", "<")
        ):
            return match.group(0)
        count += 1
        return f"{match.group(1)}{match.group(2)}[REDACTED:secret]{match.group(2)}"

    text = _ASSIGNMENT.sub(assignment, text)
    return text, count
