"""Learned ranker, feedback, grounding, cascade, answer cache, contextual retrieval, intent model."""

import json
import textwrap
import time

import numpy as np
import pytest

from tests.conftest import ScriptedProvider
from voxcode.agent import cascade
from voxcode.agent.answer_cache import AnswerCache
from voxcode.config.settings import settings
from voxcode.generation import grounding
from voxcode.learning import feedback, training
from voxcode.learning.features import FEATURE_NAMES
from voxcode.learning.ranker import LinearRanker, QueryGroup, ranking_metrics
from voxcode.models.code_chunk import CodeChunk
from voxcode.models.retrieval_result import RetrievalResult

# ----------------------------------------------------------------- ranker


def test_pairwise_ranker_learns_the_useful_feature(tmp_path):
    rng = np.random.default_rng(0)
    groups = []
    for _ in range(40):
        x = rng.normal(size=(10, len(FEATURE_NAMES)))
        relevant = np.zeros(10, dtype=bool)
        relevant[int(np.argmax(x[:, 6]))] = True  # symbol_overlap decides relevance
        groups.append(QueryGroup(x, relevant))
    ranker = LinearRanker().fit(groups)
    weights = ranker.weights()
    assert max(weights, key=lambda k: abs(weights[k])) == "symbol_overlap"
    assert ranking_metrics(groups, [ranker.score(g.features) for g in groups])["mrr@10"] >= 0.95

    ranker.save(tmp_path / "r.json")
    loaded = LinearRanker.load(tmp_path / "r.json")
    assert loaded is not None and np.allclose(
        loaded.score(groups[0].features), ranker.score(groups[0].features)
    )
    data = json.loads((tmp_path / "r.json").read_text())
    data["features"] = ["something", "else"]
    (tmp_path / "r.json").write_text(json.dumps(data))
    assert LinearRanker.load(tmp_path / "r.json") is None  # stale feature set: retrain


def _chunk(name, content, path="app/x.py", kind="function"):
    return CodeChunk(
        chunk_id=f"{path}:{name}", file_path=path, language="python", chunk_type=kind,
        symbol_name=name, start_line=1, end_line=content.count("\n") + 1, content=content,
    )  # fmt: skip


def test_docstring_queries_are_removed_from_the_training_index():
    chunk = _chunk(
        "load_index",
        'def load_index(key):\n    """Read a stored repository index from disk."""\n    return 1\n',
    )
    summary, stripped = training.split_docstring(chunk)
    assert summary == "Read a stored repository index from disk"
    assert "Read a stored" not in stripped and "def load_index" in stripped
    js = _chunk("f", "/** Compute the total price of the cart items */\nfunction f() {}", "a.ts")
    assert training.split_docstring(js)[0] == "Compute the total price of the cart items"
    assert training.split_docstring(_chunk("g", 'def g():\n    """Short."""\n')) is None


DOMAINS = ["invoice", "payment", "refund", "customer", "shipment", "coupon", "basket", "warehouse",
           "supplier", "ledger", "receipt", "voucher", "pricing", "tariff", "currency", "discount"]  # fmt: skip


def documented_repo(root):
    """16 modules, each with a documented function and decoys."""
    for word in DOMAINS:
        (root / "shop").mkdir(parents=True, exist_ok=True)
        (root / "shop" / f"{word}.py").write_text(
            textwrap.dedent(
                f'''\
                def validate_{word}(record):
                    """Check that a {word} record has every required field before saving."""
                    return all(record.get(k) for k in ("id", "{word}_total"))


                def {word}_report(rows):
                    return [r for r in rows if r]


                def test_{word}_report():
                    assert {word}_report([1, 0]) == [1]
                '''
            )
        )
    return root


def test_train_saves_only_when_it_helps_and_respects_feedback_gate(isolated_storage, tmp_path):
    from voxcode.api import services

    repo = documented_repo(tmp_path / "shop-repo")
    stats = services.indexer.index(repo)
    index = services.indexer.store.load(stats.repo_key)
    gated = training.train(index, min_feedback=5)
    assert gated["saved"] is False and "waiting for feedback" in gated["reason"]

    report = training.train(index)
    assert report["docstring_queries"] == 16 and report["train_groups"] >= 10
    assert report["saved"] == (report["heldout_after"]["mrr@10"] >= report["heldout_before"]["mrr@10"])
    if report["saved"]:
        assert training.load_ranker(index.repo_key) is not None


def test_feedback_is_logged_and_used(isolated_storage):
    assert not feedback.record("repo", "  ", positive_files=["a.py"])
    assert not feedback.record("repo", "q", positive_files=[])
    assert feedback.record(
        "repo", "where is refund validated", positive_files=["shop/refund.py"], kind="accepted_patch"
    )
    entries = feedback.load("repo")
    assert entries[0]["kind"] == "accepted_patch" and entries[0]["positive_files"] == ["shop/refund.py"]
    examples = training.from_feedback("repo")
    assert examples[0].weight == 3.0 and examples[0].answer_files == {"shop/refund.py"}


def test_retriever_uses_a_trained_ranker(isolated_storage, tmp_path, monkeypatch):
    from voxcode.api import services
    from voxcode.retrieval.hybrid.hybrid_retriever import HybridRetriever

    repo = documented_repo(tmp_path / "shop-repo")
    index = services.indexer.store.load(services.indexer.index(repo).repo_key)
    plain = HybridRetriever(index)
    plain.ranker = None
    baseline = plain.search("refund report rows")
    ranker = LinearRanker()
    ranker.weights_[FEATURE_NAMES.index("bm25_rrank")] = -5.0  # absurd model: reverses BM25
    ranker.save(training.ranker_path(index.repo_key))
    retriever = HybridRetriever(index)
    assert retriever.ranker is not None
    reordered = retriever.search("refund report rows")
    assert reordered[0].chunk_id == baseline[-1].chunk_id != baseline[0].chunk_id
    monkeypatch.setattr(settings, "LEARNED_RANKER", False)
    assert HybridRetriever(index).ranker is None


# -------------------------------------------------------------- grounding


def test_grounding_flags_invented_identifiers_and_files():
    context = "def verify_password(user, password):\n    return check_hash(password, user.hash)\n"
    symbols, files = grounding.repository_names([_chunk("verify_password", context, "app/auth.py")])
    good = grounding.check("`verify_password` calls `check_hash` in app/auth.py.", context, symbols, files)
    assert good.groundedness == 1.0 and good.citations == 1 and not good.unsupported
    bad = grounding.check(
        "It uses `hash_password_v2` and `PasswordPolicyManager` from app/security/policy.py.",
        context,
        symbols,
        files,
    )
    assert bad.unsupported == ["PasswordPolicyManager", "hash_password_v2"]
    assert bad.bad_citations == ["app/security/policy.py"] and bad.groundedness == 0.0
    assert (
        grounding.check("It returns True when the list is empty.", context, symbols, files).groundedness
        == 1.0
    )


# ---------------------------------------------------------------- cascade


def _result(path, score):
    return RetrievalResult(chunk_id=path, file_path=path, symbol_name="f", content="", score=score)


def test_cascade_difficulty_and_escalation():
    easy = cascade.difficulty(
        "where is logout handled", "search", [_result("a.py", 1.0), _result("a.py", 0.2)]
    )
    hard = cascade.difficulty(
        "why is the cache design racy under concurrent reindexing and what are the trade-offs",
        "explain",
        [_result(f"{i}.py", 1.0 - i * 0.01) for i in range(5)],
    )
    assert easy < 0.3 < 0.7 < hard
    assert cascade.needs_escalation("ok", 1.0, 0.75) == "answer too short"
    assert cascade.needs_escalation("I don't know where that is handled in this code.", 1.0, 0.75)
    assert "groundedness" in cascade.needs_escalation("A long enough answer about the code.", 0.4, 0.75)
    assert cascade.needs_escalation("Logout is handled in app/auth.py by logout().", 0.9, 0.75) == ""


# ----------------------------------------------------------------- cache


def test_answer_cache_normalises_expires_and_scopes():
    cache = AnswerCache(size=2, ttl=60)
    scope = ("repo", 1.0, "p", "m", "")
    cache.put(scope, "Where is login handled?", "In auth.py", {"task": "search"})
    assert cache.get(scope, "where's the login handled").answer == "In auth.py"
    assert cache.get(("repo", 2.0, "p", "m", ""), "where is login handled") is None  # re-indexed
    cache.put(scope, "a b question one", "1", {})
    cache.put(scope, "c d question two", "2", {})
    assert cache.get(scope, "where is login handled") is None  # evicted (LRU, size 2)
    stale = AnswerCache(ttl=1)
    stale.put(scope, "q q", "x", {})
    stale._items[next(iter(stale._items))].created = time.time() - 5
    assert stale.get(scope, "q q") is None


# ------------------------------------------------------------- pipeline


@pytest.fixture
def pipeline(isolated_storage, sample_repo, monkeypatch):
    from voxcode.agent.memory.memory_manager import MemoryManager
    from voxcode.agent.pipeline import AgentPipeline
    from voxcode.api import services
    from voxcode.core.model_registry import ModelRegistry

    monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    monkeypatch.setattr(services.registry, "get_embedder", lambda: None)
    key = services.indexer.index(sample_repo).repo_key
    registry = ModelRegistry()
    main = ScriptedProvider(["Logout is handled by `logout` in app/auth.py, which ends the session."])
    monkeypatch.setattr(registry, "get_provider", lambda name=None: main)
    return AgentPipeline(registry, MemoryManager()), registry, main, key


def _request(key, query, session="s1"):
    from voxcode.agent.agent_request import AgentRequest

    return AgentRequest(session_id=session, repository_key=key, query=query)


def test_cascade_escalates_weak_small_model_answers(pipeline, monkeypatch):
    agent, registry, main, key = pipeline
    small = ScriptedProvider(["I don't know, the context does not show it."])
    small.provider_name, small.model = "scripted-small", "tiny"
    monkeypatch.setattr(registry, "get_cascade_provider", lambda: small)
    monkeypatch.setattr(settings, "CASCADE_MAX_DIFFICULTY", 0.99)
    response = agent.ask(_request(key, "where is logout handled", "a"))
    assert response.routing["tier"] == "small" and response.routing["escalated"]
    assert response.model_name == "scripted-model" and len(small.prompts) == 1 and len(main.prompts) == 1
    assert response.quality["groundedness"] == 1.0

    small.replies = ["Logout lives in `logout` in app/auth.py and clears the session."]
    response = agent.ask(_request(key, "which file has logout", "b"))
    assert response.routing == {
        "difficulty": response.routing["difficulty"],
        "tier": "small",
        "escalated": False,
        "reason": "",
    }
    assert response.model_name == "tiny"


def test_repeated_question_is_served_from_cache(pipeline):
    agent, _, main, key = pipeline
    first = agent.ask(_request(key, "Where is logout handled?", "x"))
    second = agent.ask(_request(key, "where's logout handled", "y"))
    assert not first.cached and second.cached and second.answer == first.answer
    assert len(main.prompts) == 1
    events = list(agent.stream(_request(key, "where is logout handled", "z")))
    assert (
        events[-1]["cached"] is True
        and "".join(e["text"] for e in events if e["type"] == "token") == first.answer
    )
    # A follow-up in a conversation is not cached (history can change the answer).
    assert not agent.ask(_request(key, "where is logout handled", "x")).cached


# ------------------------------------------------- contextual retrieval


def test_contextual_chunks_are_annotated_cached_and_searchable(
    isolated_storage, sample_repo, monkeypatch, tmp_path
):
    from voxcode.api import services
    from voxcode.config import paths
    from voxcode.retrieval.bm25.bm25_retriever import BM25Retriever
    from voxcode.retrieval.contextual import ChunkContextualizer

    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")

    def reply(prompt):
        names = [line[2:] for line in prompt.user_prompt.splitlines() if line.startswith("- ")]
        return json.dumps({"contexts": {n: f"Signs the member out of the portal ({n})" for n in names}})

    provider = ScriptedProvider([reply])
    contextualizer = ChunkContextualizer(provider)
    stats = services.indexer.index(sample_repo, contextualizer=contextualizer)
    index = services.indexer.store.load(stats.repo_key)
    assert index.contextual_model == "scripted-model"
    annotated = [c for c in index.chunks if c.context]
    assert annotated and "portal" in annotated[0].embedding_text()
    assert BM25Retriever(index.chunks).search("member portal sign out", 3)

    calls = contextualizer.calls
    services.indexer.index(sample_repo, contextualizer=contextualizer, full=True)
    assert contextualizer.calls == calls  # cached by file content
    plain = services.indexer.store.load(services.indexer.index(sample_repo).repo_key)
    assert plain.contextual_model is None and not any(
        c.context for c in plain.chunks
    )  # switching off rebuilds


# ---------------------------------------------------------- intent model


def test_intent_model_beats_keyword_rules():
    from voxcode.agent.planner.intent_model import cross_validate, load_examples
    from voxcode.agent.planner.task_classifier import TaskClassifier, rule_classify

    scores = cross_validate(load_examples())
    assert scores["examples"] == 240 and scores["model_accuracy"] > scores["rules_accuracy"]
    classifier = TaskClassifier()
    assert rule_classify("the login is broken when the password has spaces").value == "chat"
    assert classifier.classify("the login is broken when the password has spaces").value == "fix"
    assert rule_classify("make the parser faster").value == "generate"
    assert classifier.classify("make the parser faster").value == "refactor"
