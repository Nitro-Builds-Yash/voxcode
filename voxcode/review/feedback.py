"""Turn annotations into feedback an agent can act on, and diff revisions."""

from __future__ import annotations

import difflib

from voxcode.review.models import Review


def _quote(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_feedback(review: Review, general: str = "") -> str:
    """Markdown feedback for the agent, one numbered item per annotation."""
    noun = {"plan": "plan", "diff": "changes", "document": "document"}[review.kind]
    lines = [f"# Review of the {noun}: changes requested", ""]
    items = review.current_annotations()
    for i, a in enumerate(items, 1):
        where = f"`{a.file}`" + (f" line {a.line}" if a.line else "") if a.file else ""
        quote = f'"{_quote(a.quote)}"' if a.quote else ""
        target = " ".join(p for p in (where, quote) if p)
        if a.kind == "delete":
            text = f"Remove {target}." + (f" {a.comment}" if a.comment else "")
        elif a.kind == "replace":
            text = f'Replace {target} with "{_quote(a.replacement)}".' + (
                f" {a.comment}" if a.comment else ""
            )
        elif a.kind == "insert":
            text = f'After {target}, add: "{_quote(a.replacement)}".' + (f" {a.comment}" if a.comment else "")
        else:
            text = f"On {target}: {a.comment}" if target else a.comment
        lines.append(f"{i}. {text.strip()}")
    general = (general or review.general_comment).strip()
    if general:
        lines += ["", "## Overall", general]
    if not items and not general:
        lines.append("The reviewer denied this without comments. Reconsider the approach.")
    lines += ["", f"Revise the {noun} to address every point above, then present it again."]
    return "\n".join(lines)


def revision_diff(old: str, new: str) -> list[dict]:
    """Line-level diff for the UI: [{op: equal|add|remove, text}]."""
    ops = []
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        tag, text = line[:2], line[2:]
        if tag == "  ":
            ops.append({"op": "equal", "text": text})
        elif tag == "+ ":
            ops.append({"op": "add", "text": text})
        elif tag == "- ":
            ops.append({"op": "remove", "text": text})
    return ops


def title_from(markdown: str, fallback: str = "Plan") -> str:
    for line in markdown.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return fallback
