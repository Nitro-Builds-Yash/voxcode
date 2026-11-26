"""Contextual retrieval: give every chunk a one-sentence description.

A chunk on its own ("def _apply(self, d): ...") often lacks the words a
question uses. At index time the LLM reads each changed file once and writes
one sentence per symbol saying what it does and where it fits; the sentence
is indexed with the chunk for BM25 and embeddings. Results are cached by
file content and model, so unchanged files never cost another call.

Off by default (CONTEXTUAL_CHUNKS=true): it costs one LLM call per changed
file. A local Ollama model makes it free.
"""

from __future__ import annotations

import hashlib
import json
import logging

from voxcode.config import paths
from voxcode.core.json_extract import first_json_with
from voxcode.generation.prompts.prompt_result import PromptResult
from voxcode.models.code_chunk import CodeChunk

logger = logging.getLogger(__name__)

SYSTEM = """You write search annotations for code. For each listed symbol, write ONE plain sentence
(max 25 words) saying what it does and how it fits into the file, using words a developer would
search for. Reply with JSON only: {"contexts": {"<symbol>": "<sentence>", ...}}"""

MAX_SOURCE_CHARS = 12_000
MAX_SYMBOLS = 60


def _base(name: str) -> str:
    return name.split(" (part")[0]


class ChunkContextualizer:
    def __init__(self, provider):
        self.provider = provider
        self.cache_dir = paths.CACHE_DIR / "contexts"
        self.calls = 0

    def _cache_path(self, rel: str, source: str):
        digest = hashlib.sha1(f"{self.provider.model}\0{rel}\0{source}".encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def annotate(self, rel: str, source: str, chunks: list[CodeChunk]) -> int:
        """Set `chunk.context` in place; returns how many chunks got one."""
        names = list(dict.fromkeys(_base(c.symbol_name) for c in chunks if c.chunk_type != "section"))[
            :MAX_SYMBOLS
        ]
        if not names:
            return 0
        cache = self._cache_path(rel, source)
        contexts: dict[str, str] | None = None
        if cache.exists():
            try:
                contexts = json.loads(cache.read_text())
            except ValueError:
                contexts = None
        if contexts is None:
            contexts = self._ask(rel, source, names)
            if contexts:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(contexts))
        count = 0
        for chunk in chunks:
            sentence = contexts.get(_base(chunk.symbol_name)) if contexts else None
            if sentence:
                chunk.context = str(sentence).strip()[:300]
                count += 1
        return count

    def _ask(self, rel: str, source: str, names: list[str]) -> dict[str, str]:
        user = f"File: {rel}\n\n```\n{source[:MAX_SOURCE_CHARS]}\n```\n\nSymbols:\n" + "\n".join(
            f"- {n}" for n in names
        )
        self.calls += 1
        try:
            reply = self.provider.generate(
                PromptResult(system_prompt=SYSTEM, user_prompt=user, full_prompt=user, prompt_size=len(user))
            ).response
        except Exception as exc:
            logger.warning("Contextual annotation failed for %s: %s", rel, exc)
            return {}
        data = first_json_with(reply, "contexts") or {}
        contexts = data.get("contexts")
        if not isinstance(contexts, dict):
            return {}
        return {
            str(k): str(v) for k, v in contexts.items() if k in names and isinstance(v, str) and v.strip()
        }
