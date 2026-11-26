"""Assembles the prompt context: request, conversation history, code."""

from __future__ import annotations

from collections import OrderedDict

from voxcode.config.settings import settings
from voxcode.generation.context.context_result import ContextResult
from voxcode.generation.context.context_window import ContextWindow
from voxcode.models.retrieval_result import RetrievalResult


def number_lines(content: str, first_line: int) -> str:
    return "\n".join(f"{number:>4}| {line}" for number, line in enumerate(content.splitlines(), first_line))


class ContextBuilder:
    def __init__(self, max_tokens: int | None = None):
        self.max_tokens = max_tokens or settings.CONTEXT_MAX_TOKENS

    def build(
        self,
        query: str,
        retrieved_chunks: list[RetrievalResult],
        conversation_history: str | None = None,
        extra_sections: list[str] | None = None,
    ) -> ContextResult:
        window = ContextWindow(self.max_tokens)
        header = f"USER REQUEST\n============\n{query}\n"
        window.try_add(header)
        sections = [header]

        if conversation_history:
            history = f"CONVERSATION SO FAR\n===================\n{conversation_history}\n"
            if window.try_add(history):
                sections.append(history)

        for extra in extra_sections or []:
            if window.try_add(extra):
                sections.append(extra)

        # Keep retrieval order (best first) but group chunks under their file.
        by_file: OrderedDict[str, list[RetrievalResult]] = OrderedDict()
        seen: set[str] = set()
        for chunk in retrieved_chunks:
            if chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                by_file.setdefault(chunk.file_path, []).append(chunk)

        code_sections: list[str] = []
        included = 0
        files: list[str] = []
        for file_path, chunks in by_file.items():
            parts = []
            for chunk in sorted(chunks, key=lambda c: c.start_line):
                block = (
                    f"--- {chunk.symbol_name} ({chunk.chunk_type}, lines "
                    f"{chunk.start_line}-{chunk.end_line})\n"
                    f"{number_lines(chunk.content, chunk.start_line)}\n"
                )
                if not window.try_add(block):
                    break
                parts.append(block)
                included += 1
            if parts:
                files.append(file_path)
                code_sections.append(f"FILE: {file_path}\n" + "".join(parts))

        sections.append(
            "REPOSITORY CONTEXT\n==================\n"
            + ("\n".join(code_sections) if code_sections else "(no matching code found)\n")
        )
        return ContextResult(
            context="\n".join(sections),
            query=query,
            num_chunks=included,
            num_files=len(files),
            context_size=window.used,
            files=files,
        )
