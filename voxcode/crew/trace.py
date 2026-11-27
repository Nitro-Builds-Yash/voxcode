"""Traces: what each agent did, when, and what it cost."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from voxcode.config import paths


@dataclass
class Event:
    seq: int
    t: float  # seconds since the run started
    agent: str
    kind: str  # start | thought | tool | result | message | error | done
    text: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class AgentStats:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    tool_calls: int = 0
    first: float | None = None
    last: float | None = None
    cost_usd: float = 0.0


class Trace:
    def __init__(self, task: str, repository_path: str, mode: str, trace_id: str | None = None):
        self.id = trace_id or uuid.uuid4().hex[:12]
        self.task, self.repository_path, self.mode = task, repository_path, mode
        self.status = "running"
        self.created = time.time()
        self.events: list[Event] = []
        self.agents: dict[str, AgentStats] = {}
        self.result: dict = {}
        self._lock = threading.Condition()

    def _stats(self, agent: str, t: float) -> AgentStats:
        stats = self.agents.setdefault(agent, AgentStats())
        stats.first = t if stats.first is None else stats.first
        stats.last = t
        return stats

    def emit(self, agent: str, kind: str, text: str = "", **data) -> Event:
        with self._lock:
            t = round(time.time() - self.created, 3)
            event = Event(len(self.events), t, agent, kind, text, data)
            self.events.append(event)
            stats = self._stats(agent, t)
            if kind == "tool":
                stats.tool_calls += 1
            self._lock.notify_all()
            return event

    def add_usage(self, agent: str, model: str, input_tokens: int, output_tokens: int) -> None:
        from voxcode.generation.providers.types import Usage
        from voxcode.monitoring import Metrics

        cost = Metrics.estimate_cost(model, Usage(input_tokens, output_tokens)) or 0.0
        with self._lock:
            stats = self._stats(agent, round(time.time() - self.created, 3))
            stats.input_tokens += input_tokens
            stats.output_tokens += output_tokens
            stats.calls += 1
            stats.cost_usd += cost

    @property
    def total_tokens(self) -> int:
        return sum(s.input_tokens + s.output_tokens for s in self.agents.values())

    def finish(self, status: str, **result) -> None:
        with self._lock:
            self.status = status
            self.result.update(result)
            self._lock.notify_all()

    def events_after(self, seq: int, timeout: float) -> list[Event]:
        """Events with seq > `seq`, waiting up to `timeout` for new ones while running."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while len(self.events) <= seq + 1 and self.status == "running":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._lock.wait(remaining)
            return self.events[seq + 1 :]

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "task": self.task,
                "repository_path": self.repository_path,
                "mode": self.mode,
                "status": self.status,
                "created": self.created,
                "duration": round((self.events[-1].t if self.events else 0.0), 2),
                "events": [asdict(e) for e in self.events],
                "agents": {name: asdict(s) for name, s in self.agents.items()},
                "total_tokens": self.total_tokens,
                "total_cost_usd": round(sum(s.cost_usd for s in self.agents.values()), 6),
                "result": self.result,
            }

    def summary(self) -> dict:
        data = self.to_dict()
        return {k: data[k] for k in ("id", "task", "mode", "status", "created", "duration", "total_tokens")}


class TraceStore:
    def __init__(self, root: Path | None = None):
        self._root = root
        self._traces: dict[str, Trace] = {}
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root or paths.DATA_DIR / "crew"

    def add(self, trace: Trace) -> None:
        with self._lock:
            self._traces[trace.id] = trace

    def save(self, trace: Trace) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{trace.id}.json").write_text(json.dumps(trace.to_dict(), indent=1))

    def get(self, trace_id: str) -> dict | None:
        trace = self._traces.get(trace_id)
        if trace is not None:
            return trace.to_dict()
        path = self.root / f"{trace_id}.json"
        if re.fullmatch(r"[a-f0-9]{12}", trace_id) and path.exists():
            return json.loads(path.read_text())
        return None

    def live(self, trace_id: str) -> Trace | None:
        return self._traces.get(trace_id)

    def list(self, limit: int = 50) -> list[dict]:
        items = {t.id: t.summary() for t in self._traces.values()}
        if self.root.exists():
            for path in self.root.glob("*.json"):
                if path.stem not in items:
                    try:
                        data = json.loads(path.read_text())
                        items[path.stem] = {
                            k: data.get(k)
                            for k in ("id", "task", "mode", "status", "created", "duration", "total_tokens")
                        }
                    except ValueError:
                        continue
        return sorted(items.values(), key=lambda r: r["created"] or 0, reverse=True)[:limit]


trace_store = TraceStore()
