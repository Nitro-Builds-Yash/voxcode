"""The Rust extension must give exactly the same results as the Python code."""

import random
import string
from pathlib import Path

import numpy as np
import pytest

from voxcode.config.settings import settings

native = pytest.importorskip("voxcode_native")

REPO = Path(__file__).resolve().parents[2]
SOURCES = sorted(p for p in (REPO / "voxcode").rglob("*.py"))[:80]


def random_text(rng: random.Random, n: int) -> str:
    alphabet = string.ascii_letters + string.digits + "_ ().,:=\"'-\n" + "éß٣２"
    return "".join(rng.choice(alphabet) for _ in range(n))


def test_tokenizer_parity():
    from voxcode.retrieval.bm25.tokenizer import tokenize_python

    rng = random.Random(7)
    samples = [p.read_text() for p in SOURCES] + [random_text(rng, 400) for _ in range(300)]
    samples += ["HTTPServer getUserById parseJSON2 XMLHttpRequest a1B2c3 __init__ ABCdef", "٣٤x ２３"]
    for text in samples:
        assert native.tokenize(text) == tokenize_python(text), text[:80]
    assert native.tokenize_many(samples[:20]) == [tokenize_python(t) for t in samples[:20]]


def test_bm25_parity():
    from rank_bm25 import BM25Okapi

    from voxcode.retrieval.bm25.tokenizer import tokenize_python

    corpus = [tokenize_python(p.read_text()) or ["_empty_"] for p in SOURCES]
    reference, index = BM25Okapi(corpus), native.Bm25Index(corpus)
    for query in [
        "verify password hash",
        "build chunks symbols",
        "provider stream usage tokens",
        "the",
        "zzz",
    ]:
        tokens = tokenize_python(query) or ["the"]
        expected = np.asarray(reference.get_scores(tokens))
        np.testing.assert_allclose(index.scores(tokens), expected, rtol=1e-12, atol=1e-12)
        order = [int(i) for i in np.argsort(-expected, kind="stable")[:10] if expected[i] > 0]
        assert [i for i, _ in index.top_k(tokens, 10)] == order


def test_retriever_same_results_with_and_without_native(monkeypatch):
    from voxcode.codebase.chunker.chunker import build_chunks
    from voxcode.retrieval.bm25.bm25_retriever import BM25Retriever

    chunks = [
        c for p in SOURCES for c in build_chunks(p.relative_to(REPO).as_posix(), p.read_text(), "python")
    ]
    fast = BM25Retriever(chunks).search("retrieve hybrid search results", 15)
    monkeypatch.setattr(settings, "NATIVE_ACCEL", "off")
    slow = BM25Retriever(chunks).search("retrieve hybrid search results", 15)
    assert [r.chunk_id for r in fast] == [r.chunk_id for r in slow]
    np.testing.assert_allclose([r.score for r in fast], [r.score for r in slow], rtol=1e-12)


def test_scanner_parity(tmp_path):
    from voxcode.codebase.indexer.scanner import (
        IGNORED_DIRS,
        IGNORED_SUFFIXES,
        ROOT_IGNORED_DIRS,
        scan_repository_python,
    )
    from voxcode.codebase.parser.languages import EXTENSION_LANGUAGES

    (tmp_path / "src/target").mkdir(parents=True)
    (tmp_path / "target").mkdir()
    (tmp_path / "env2").mkdir()
    (tmp_path / "env2/pyvenv.cfg").write_text("")
    for rel in [
        "a.py",
        "B.PY",
        ".hidden.py",
        "x.min.js",
        "src/target/keep.rs",
        "target/skip.rs",
        "env2/no.py",
        "README.md",
        "noext",
    ]:
        (tmp_path / rel).write_text("x")
    (tmp_path / "big.py").write_text("x" * 50)
    (tmp_path / "link.py").symlink_to(tmp_path / "a.py")

    def scan(root, limit):
        return native.scan_repository(
            root, limit, IGNORED_DIRS, ROOT_IGNORED_DIRS, list(IGNORED_SUFFIXES), set(EXTENSION_LANGUAGES)
        )

    for root, limit in [(tmp_path, 10), (REPO, 1_000_000)]:
        assert [str(p) for p in scan(root, limit)] == [str(p) for p in scan_repository_python(root, limit)]


def test_redaction_parity():
    from voxcode.core.redaction import redact_python

    samples = [
        'API_KEY = "sk-ant-' + "a" * 30 + '"\npassword: hunter22\napi_key=changeme',
        "aws AKIAABCDEFGHIJKLMNOP and ghp_" + "x" * 36 + " and postgres://u:pass@host/db",
        'token=\'abc\' secret = "short" db_password="averylongpassword" auth_token: $ENV_VALUE',
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY----- jwt eyJabcdefghijk.eyJabcdefghijk.sigsigsigsig",
        'MY_SECRET_TOKEN="unterminated value',
    ] + [p.read_text() for p in SOURCES[:30]]
    for text in samples:
        assert native.redact(text) == redact_python(text)


def test_segmenter_parity():
    from voxcode.voice.listener import FRAME, SAMPLE_RATE, NativeSegmenter, UtteranceSegmenter

    rng = np.random.default_rng(3)
    t = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
    speech = (0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    stream = np.concatenate(
        [
            rng.normal(0, 0.002, SAMPLE_RATE).astype(np.float32),
            speech,
            rng.normal(0, 0.002, SAMPLE_RATE * 2).astype(np.float32),
        ]
    )
    py, rs = UtteranceSegmenter(0.5, 0.02), NativeSegmenter(native, 0.5, 0.02)
    for i in range(0, len(stream) - FRAME + 1, FRAME):
        a, b = py.feed(stream[i : i + FRAME]), rs.feed(stream[i : i + FRAME])
        assert (a and a.kind) == (b and b.kind)
        if a and a.audio is not None:
            np.testing.assert_array_equal(a.audio, b.audio)
    batch = NativeSegmenter(native, 0.5, 0.02).feed_many(stream)
    assert [e.kind for e in batch] == ["start", "end"] and batch[1].audio is not None


def test_native_switch(monkeypatch):
    from voxcode.native import native as backend
    from voxcode.native import status

    monkeypatch.setattr(settings, "NATIVE_ACCEL", "auto")
    assert backend() is not None and status()["active"]
    monkeypatch.setattr(settings, "NATIVE_ACCEL", "off")
    assert backend() is None and status() == {
        "installed": True,
        "version": native.__version__,
        "active": False,
    }
