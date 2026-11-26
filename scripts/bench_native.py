"""Compare the Rust extension with the pure-Python paths.

python scripts/bench_native.py [repo]   (defaults to this repository)
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import numpy as np

from voxcode.config.settings import settings


def timed(fn, repeat: int = 5) -> float:
    runs = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        runs.append(time.perf_counter() - start)
    return statistics.median(runs) * 1000


def main() -> None:
    from voxcode.codebase.chunker.chunker import build_chunks
    from voxcode.codebase.indexer.scanner import scan_repository
    from voxcode.codebase.parser.languages import detect_language
    from voxcode.core.redaction import redact
    from voxcode.retrieval.bm25.bm25_retriever import BM25Retriever
    from voxcode.voice.listener import FRAME, SAMPLE_RATE, make_segmenter

    repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    files = scan_repository(repo)
    chunks = []
    for path in files:
        lang = detect_language(path)
        if lang:
            text = path.read_text(errors="ignore")
            chunks += build_chunks(path.relative_to(repo).as_posix(), text, lang)
    # Scale the corpus up so the numbers reflect a mid-sized repository.
    corpus = chunks * max(1, 20_000 // max(1, len(chunks)))
    big_text = "\n".join(c.content for c in chunks)[:2_000_000]
    queries = [
        "where is the password verified",
        "stream tokens from provider",
        "build the call graph",
        "redact secrets",
    ]
    audio = np.random.default_rng(0).normal(0, 0.01, SAMPLE_RATE * 60).astype(np.float32)

    def feed_frames(segmenter) -> None:
        for i in range(0, len(audio) - FRAME + 1, FRAME):
            segmenter.feed(audio[i : i + FRAME])

    def suite() -> dict[str, float]:
        retriever = BM25Retriever(corpus)
        return {
            f"scan repository ({len(files)} files)": timed(lambda: scan_repository(repo)),
            f"build BM25 index ({len(corpus):,} chunks)": timed(lambda: BM25Retriever(corpus), 3),
            "BM25 query (x4)": timed(lambda: [retriever.search(q, 10) for q in queries]),
            f"redact {len(big_text) / 1e6:.1f} MB": timed(lambda: redact(big_text), 3),
            "VAD over 60 s audio (frame by frame)": timed(lambda: feed_frames(make_segmenter()), 3),
            "VAD over 60 s audio (one buffer)": timed(lambda: make_segmenter().feed_many(audio), 3),
        }

    settings.NATIVE_ACCEL = "off"
    python = suite()
    settings.NATIVE_ACCEL = "auto"
    rust = suite()
    print(f"{'operation':<36}{'python ms':>12}{'rust ms':>12}{'speed-up':>10}")
    for name in python:
        print(f"{name:<36}{python[name]:>12.1f}{rust[name]:>12.1f}{python[name] / rust[name]:>9.1f}x")


if __name__ == "__main__":
    main()
