"""Review sessions: kept in memory, persisted as an archive, awaitable."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from voxcode.config import paths
from voxcode.config.settings import settings
from voxcode.review.feedback import format_feedback
from voxcode.review.models import Annotation, Decision, Review

logger = logging.getLogger(__name__)


class ReviewNotFound(KeyError):
    pass


class ReviewStore:
    def __init__(self, root: Path | None = None):
        self._root = root
        self._reviews: dict[str, Review] = {}
        self._changed = threading.Condition()
        self._listeners: list = []  # callbacks(review, decision) run after a decision

    @property
    def root(self) -> Path:
        return self._root or paths.DATA_DIR / "reviews"

    # ---------------------------------------------------------- storage
    def _save(self, review: Review) -> None:
        review.updated = time.time()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / f"{review.id}.json").write_text(review.model_dump_json(indent=1))
        except OSError as exc:
            logger.warning("Could not archive review %s: %s", review.id, exc)

    def get(self, review_id: str) -> Review:
        review = self._reviews.get(review_id)
        if review is None:
            path = self.root / f"{review_id}.json"
            if not re.fullmatch(r"[a-f0-9]{12}", review_id) or not path.exists():
                raise ReviewNotFound(review_id)
            review = Review.model_validate_json(path.read_text())
            self._reviews[review_id] = review
        return review

    def list(self, limit: int = 100) -> list[dict]:
        seen = {r.id: r.summary() for r in self._reviews.values()}
        if self.root.exists():
            for path in self.root.glob("*.json"):
                if path.stem not in seen:
                    try:
                        seen[path.stem] = Review.model_validate_json(path.read_text()).summary()
                    except ValueError:
                        continue
        return sorted(seen.values(), key=lambda r: r["updated"], reverse=True)[:limit]

    def find_open(self, **meta) -> Review | None:
        """A review that is waiting for a revision, matched on meta (e.g. a Claude session).

        Looks in the archive too: each hook call may run in a fresh process."""
        if self.root.exists():
            for path in self.root.glob("*.json"):
                if path.stem not in self._reviews:
                    try:
                        self._reviews[path.stem] = Review.model_validate_json(path.read_text())
                    except ValueError:
                        continue
        for review in sorted(self._reviews.values(), key=lambda r: r.updated, reverse=True):
            if review.status in ("denied", "revising", "pending") and all(
                review.meta.get(k) == v for k, v in meta.items()
            ):
                return review
        return None

    # -------------------------------------------------------- mutations
    def create(
        self, kind: str, content: str, title: str, source: str = "voxcode", meta: dict | None = None
    ) -> Review:
        review = Review(kind=kind, title=title, source=source, revisions=[content], meta=meta or {})  # type: ignore[arg-type]
        with self._changed:
            self._reviews[review.id] = review
            self._save(review)
            self._changed.notify_all()
        return review

    def revise(self, review_id: str, content: str) -> Review:
        with self._changed:
            review = self.get(review_id)
            review.revisions.append(content)
            review.status = "pending"
            review.general_comment = ""
            self._save(review)
            self._changed.notify_all()
        return review

    def annotate(self, review_id: str, annotation: Annotation) -> Annotation:
        with self._changed:
            review = self.get(review_id)
            annotation.revision = review.revision
            review.annotations.append(annotation)
            self._save(review)
            self._changed.notify_all()
        return annotation

    def remove_annotation(self, review_id: str, annotation_id: str) -> None:
        with self._changed:
            review = self.get(review_id)
            review.annotations = [a for a in review.annotations if a.id != annotation_id]
            self._save(review)
            self._changed.notify_all()

    def update(self, review_id: str, **fields) -> Review:
        with self._changed:
            review = self.get(review_id)
            for key, value in fields.items():
                if key == "meta":
                    review.meta.update(value)
                else:
                    setattr(review, key, value)
            self._save(review)
            self._changed.notify_all()
        return review

    def decide(self, review_id: str, decision: str, general: str = "") -> Decision:
        with self._changed:
            review = self.get(review_id)
            if review.status not in ("pending",):
                raise ValueError(f"review is {review.status}, not waiting for a decision")
            review.general_comment = general.strip()
            feedback = "" if decision == "approved" else format_feedback(review, general)
            result = Decision(revision=review.revision, decision=decision, feedback=feedback)  # type: ignore[arg-type]
            review.decisions.append(result)
            review.status = decision  # type: ignore[assignment]
            self._save(review)
            self._changed.notify_all()
        if decision == "approved" and review.kind in ("plan", "document"):
            export_to_obsidian(review)
        for listener in list(self._listeners):
            try:
                listener(review, result)
            except Exception as exc:  # a failing follow-up must not lose the decision
                logger.warning("Review follow-up failed: %s", exc)
        return result

    def on_decision(self, callback) -> None:
        self._listeners.append(callback)

    def wait(
        self, review_id: str, after_revision: int | None = None, timeout: float = 60.0
    ) -> Decision | None:
        """Block until the review (at `after_revision`, default current) is decided."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                review = self.get(review_id)
                revision = review.revision if after_revision is None else after_revision
                for decision in reversed(review.decisions):
                    if decision.revision == revision:
                        return decision
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._changed.wait(min(remaining, 5.0))


def export_to_obsidian(review: Review) -> Path | None:
    """Approved plans go to an Obsidian vault with YAML frontmatter (OBSIDIAN_VAULT)."""
    if not settings.OBSIDIAN_VAULT:
        return None
    vault = Path(settings.OBSIDIAN_VAULT).expanduser()
    if not vault.is_dir():
        logger.warning("OBSIDIAN_VAULT %s is not a folder", vault)
        return None
    folder = vault / settings.OBSIDIAN_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    day = datetime.fromtimestamp(review.created).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", review.title.lower()).strip("-")[:60] or review.id
    repo = review.meta.get("repository_path") or review.meta.get("cwd") or ""
    project = Path(repo).name if repo else ""
    frontmatter = [
        "---",
        f"title: {json.dumps(review.title)}",
        f"date: {day}",
        f"source: {review.source}",
        f"revisions: {len(review.revisions)}",
        f"project: {json.dumps(project)}",
        "tags: [voxcode, plan" + (f", {re.sub(r'[^a-z0-9-]', '', project.lower())}" if project else "") + "]",
        "---",
        "",
    ]
    path = folder / f"{day}-{slug}.md"
    path.write_text("\n".join(frontmatter) + review.content + "\n", encoding="utf-8")
    return path


review_store = ReviewStore()
