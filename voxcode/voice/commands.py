"""Spoken editor commands that are handled instantly, without the LLM."""

from __future__ import annotations

import re

COMMANDS: dict[str, tuple[str, ...]] = {
    "accept": ("accept( the)?( change| patch| diff)?", "apply( it| the change| the patch)?", "looks good"),
    "reject": ("reject( the)?( change| patch| diff)?", "discard( it| that)?", "cancel"),
    "undo": ("undo( that| the last change| last change)?", "revert( that| the last change)?"),
    "next_file": ("next file", "show( me)? the next file"),
    "previous_file": ("previous file", "go back a file"),
    "run_tests": ("run( the)? tests", "test it"),
    "reindex": ("re-?index( the)?( workspace| repo| repository)?",),
    "stop": ("stop( listening)?", "never ?mind"),
}

_PATTERNS = {
    name: re.compile(r"^\s*(?:" + "|".join(options) + r")\s*[.!]?\s*$", re.IGNORECASE)
    for name, options in COMMANDS.items()
}


def parse_command(transcript: str) -> str | None:
    """Return a command name if the whole utterance is a known command."""
    text = re.sub(r"^(hey |ok |okay )?vox[,]?\s*", "", transcript.strip(), flags=re.IGNORECASE)
    for name, pattern in _PATTERNS.items():
        if pattern.match(text):
            return name
    return None
