"""A linear learning-to-rank model trained with a pairwise (RankNet) loss.

Small on purpose: 15 weights, trains in well under a second on a few
thousand pairs with plain numpy, and is easy to inspect (`weights()`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from voxcode.learning.features import FEATURE_NAMES


@dataclass
class QueryGroup:
    """Candidates for one query; `relevant[i]` marks the right answers."""

    features: np.ndarray
    relevant: np.ndarray
    weight: float = 1.0


@dataclass
class LinearRanker:
    weights_: np.ndarray = field(default_factory=lambda: np.zeros(len(FEATURE_NAMES)))
    mean_: np.ndarray = field(default_factory=lambda: np.zeros(len(FEATURE_NAMES)))
    scale_: np.ndarray = field(default_factory=lambda: np.ones(len(FEATURE_NAMES)))
    meta: dict = field(default_factory=dict)

    def score(self, features: np.ndarray) -> np.ndarray:
        if features.size == 0:
            return np.zeros(0)
        return ((features - self.mean_) / self.scale_) @ self.weights_

    def weights(self) -> dict[str, float]:
        return {name: round(float(w), 4) for name, w in zip(FEATURE_NAMES, self.weights_, strict=True)}

    # ------------------------------------------------------------ training
    def fit(
        self, groups: list[QueryGroup], epochs: int = 300, lr: float = 0.05, l2: float = 1e-3
    ) -> LinearRanker:
        groups = [g for g in groups if g.relevant.any() and not g.relevant.all()]
        if not groups:
            raise ValueError("no query has both relevant and non-relevant candidates")
        stacked = np.vstack([g.features for g in groups])
        self.mean_ = stacked.mean(axis=0)
        self.scale_ = np.where(stacked.std(axis=0) > 1e-9, stacked.std(axis=0), 1.0)
        # Pairs (positive - negative) with per-pair weights normalised per query.
        diffs, pair_weights = [], []
        for g in groups:
            x = (g.features - self.mean_) / self.scale_
            pos, neg = x[g.relevant], x[~g.relevant]
            d = (pos[:, None, :] - neg[None, :, :]).reshape(-1, x.shape[1])
            diffs.append(d)
            pair_weights.append(np.full(len(d), g.weight / len(d)))
        d = np.vstack(diffs)
        pw = np.concatenate(pair_weights)
        pw = pw / pw.sum()

        w = np.zeros(d.shape[1])
        m = np.zeros_like(w)
        v = np.zeros_like(w)
        for step in range(1, epochs + 1):  # Adam on the weighted logistic pair loss
            margin = d @ w
            grad = -(d * (pw / (1.0 + np.exp(np.clip(margin, -30, 30))))[:, None]).sum(axis=0) + l2 * w
            m = 0.9 * m + 0.1 * grad
            v = 0.999 * v + 0.001 * grad**2
            w -= lr * (m / (1 - 0.9**step)) / (np.sqrt(v / (1 - 0.999**step)) + 1e-8)
        self.weights_ = w
        return self

    # --------------------------------------------------------- persistence
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "features": FEATURE_NAMES,
            "weights": self.weights_.tolist(),
            "mean": self.mean_.tolist(),
            "scale": self.scale_.tolist(),
            "meta": self.meta,
        }
        path.write_text(json.dumps(payload, indent=2))

    @classmethod
    def load(cls, path: Path) -> LinearRanker | None:
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if payload.get("features") != FEATURE_NAMES:
            return None  # trained with another feature set: retrain
        return cls(
            np.asarray(payload["weights"]),
            np.asarray(payload["mean"]),
            np.asarray(payload["scale"]),
            payload.get("meta", {}),
        )


def ranking_metrics(groups: list[QueryGroup], scores: list[np.ndarray]) -> dict[str, float]:
    """MRR@10 and recall@5 of the first relevant candidate."""
    rr, r5 = [], []
    for g, s in zip(groups, scores, strict=True):
        order = np.argsort(-s, kind="stable")
        hits = np.flatnonzero(g.relevant[order])
        rank = int(hits[0]) + 1 if hits.size else None
        rr.append(1.0 / rank if rank and rank <= 10 else 0.0)
        r5.append(1.0 if rank and rank <= 5 else 0.0)
    return {"mrr@10": float(np.mean(rr)) if rr else 0.0, "recall@5": float(np.mean(r5)) if r5 else 0.0}
