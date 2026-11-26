"""LLM query rewriting for code search.

Spoken questions are vague ("where do we deal with the login stuff"). The
model turns them into (1) a keyword query with likely identifiers and (2) a
short hypothetical code snippet (HyDE); both are searched and fused with the
original query.
"""

from __future__ import annotations

import logging

from voxcode.core.json_extract import first_json_with
from voxcode.generation.prompts.prompt_result import PromptResult

logger = logging.getLogger(__name__)

SYSTEM = """You turn a developer's question into code-search queries. Reply with JSON only:
{"keywords": "likely identifiers and terms, space separated", "code": "a short hypothetical code snippet (max 12 lines) that would answer it"}
Use naming conventions typical for the language. Do not explain."""


def rewrite_query(provider, query: str) -> list[str]:
    """Extra queries to search with (empty on any failure)."""
    try:
        reply = provider.generate(
            PromptResult(system_prompt=SYSTEM, user_prompt=query, full_prompt=query, prompt_size=len(query))
        ).response
    except Exception as exc:
        logger.debug("Query rewrite failed: %s", exc)
        return []
    data = first_json_with(reply, "keywords", "code") or {}
    extra = [str(data.get(k, "")).strip() for k in ("keywords", "code")]
    return [q for q in extra if q and q.lower() != query.lower()]
