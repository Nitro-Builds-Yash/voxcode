"""A small learned intent classifier.

Multinomial logistic regression over hashed word unigrams and bigrams (plus
the first word and a question-mark flag), trained on `intent_examples.tsv`
at first use in a few milliseconds. Phrasing matters more than keywords:
"the login is broken when..." is a fix with no fix keyword, and "make the
parser faster" is a refactor although "make" usually means new code.
Low-confidence predictions fall back to the keyword rules.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from functools import lru_cache
from pathlib import Path

import numpy as np

DATA = Path(__file__).with_name("intent_examples.tsv")
DIM = 2**12
_WORD = re.compile(r"[a-z0-9_]+")


def _features(text: str) -> np.ndarray:
    words = _WORD.findall(text.lower().replace("'", ""))
    grams = [f"w:{w}" for w in words] + [f"b:{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
    if words:
        grams.append(f"first:{words[0]}")
    if text.strip().endswith("?"):
        grams.append("qmark")
    if len(words) <= 2:
        grams.append("short")
    x = np.zeros(DIM)
    for gram in grams:
        x[zlib.crc32(gram.encode()) % DIM] += 1.0
    norm = np.linalg.norm(x)
    return x / norm if norm else x


def load_examples(path: Path = DATA) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            label, text = line.split("\t", 1)
            rows.append((label.strip(), text.strip()))
    return rows


class IntentModel:
    def __init__(self, labels: list[str], weights: np.ndarray, bias: np.ndarray):
        self.labels, self.weights, self.bias = labels, weights, bias

    @classmethod
    def fit(
        cls, examples: list[tuple[str, str]], epochs: int = 1000, lr: float = 4.0, l2: float = 1e-4
    ) -> IntentModel:
        labels = sorted({label for label, _ in examples})
        index = {label: i for i, label in enumerate(labels)}
        x = np.stack([_features(text) for _, text in examples])
        y = np.zeros((len(examples), len(labels)))
        y[np.arange(len(examples)), [index[label] for label, _ in examples]] = 1.0
        w = np.zeros((DIM, len(labels)))
        b = np.zeros(len(labels))
        for _ in range(epochs):  # full-batch gradient descent on softmax cross-entropy
            logits = x @ w + b
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(axis=1, keepdims=True)
            grad = (p - y) / len(examples)
            w -= lr * (x.T @ grad + l2 * w)
            b -= lr * grad.sum(axis=0)
        return cls(labels, w, b)

    def predict(self, text: str) -> tuple[str, float]:
        logits = _features(text) @ self.weights + self.bias
        p = np.exp(logits - logits.max())
        p /= p.sum()
        best = int(p.argmax())
        return self.labels[best], float(p[best])


@lru_cache(maxsize=1)
def default_model() -> IntentModel:
    """Trained once per install (about 3 s), then loaded from the cache in milliseconds."""
    from voxcode.config import paths

    digest = hashlib.sha1(DATA.read_bytes() + f"{DIM}".encode()).hexdigest()[:16]
    cache = paths.CACHE_DIR / f"intent-model-{digest}.npz"
    try:
        data = np.load(cache)
        return IntentModel(list(data["labels"]), data["weights"], data["bias"])
    except (OSError, ValueError, KeyError):
        pass
    model = IntentModel.fit(load_examples())
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, labels=np.array(model.labels), weights=model.weights, bias=model.bias)
    except OSError:
        pass
    return model


def cross_validate(examples: list[tuple[str, str]], folds: int = 5, seed: int = 0) -> dict[str, float]:
    """Accuracy of the model and of the keyword rules on held-out folds."""
    from voxcode.agent.planner.task_classifier import rule_classify

    order = np.random.default_rng(seed).permutation(len(examples))
    model_hits = rule_hits = 0
    for fold in range(folds):
        test_idx = set(order[fold::folds].tolist())
        train = [e for i, e in enumerate(examples) if i not in test_idx]
        model = IntentModel.fit(train)
        for i in test_idx:
            label, text = examples[i]
            model_hits += model.predict(text)[0] == label
            rule_hits += rule_classify(text).value == label
    n = len(examples)
    return {
        "model_accuracy": round(model_hits / n, 3),
        "rules_accuracy": round(rule_hits / n, 3),
        "examples": n,
    }
