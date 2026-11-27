# HTTP API

The server listens on `http://127.0.0.1:8000`. Interactive docs are at [`/docs`](http://127.0.0.1:8000/docs). If `API_TOKEN` is set, send it as `X-VoxCode-Token`.

## Indexing

| Endpoint | |
|---|---|
| `POST /index/` | `{"repository_path": "...", "full": false, "watch": true}` · incremental index; `watch` starts or stops re-indexing on file changes |
| `GET /index/repos` | Indexed repositories with file and chunk counts |

## Agent

| Endpoint | |
|---|---|
| `POST /agent/query` | `{"query": "...", "repository_path": "...", "mode": "auto"}` · `mode` is `auto` (answer or edit by intent), `answer`, `edit` or `agentic` |
| `POST /agent/stream` | Same body, as server-sent events (see below) |
| `POST /agent/edit` | Always run the edit loop; returns a validated diff and before/after previews. Writes nothing. |
| `POST /agent/preview` | `{"repository_path", "diff"}` · check a patch and get before/after file contents |
| `POST /agent/apply` | Apply a patch (kept for undo) and re-index |
| `POST /agent/undo` | Revert the last applied patch |
| `POST /agent/test` | Run the repository's tests (pytest, npm test, go test or cargo test) |
| `DELETE /agent/session/{id}` | Clear conversation memory |

### Stream events

```text
data: {"type": "start",   "mode": "answer", "emotion": {...}}
data: {"type": "sources", "sources": [{"file_path": "...", "symbol_name": "...", "start_line": 12, ...}]}
data: {"type": "token",   "text": "The login flow "}
data: {"type": "tool",    "tool": "read_file", "args": {...}, "observation": "..."}   (agentic mode)
data: {"type": "edit",    "status": "ready", "diff": "...", "files": [...], "attempts": [...]}  (edit mode)
data: {"type": "done",    "answer": "...", "provider": "ollama", "model_name": "qwen3:8b"}
data: {"type": "error",   "detail": "..."}
```

Edit `status` is `ready` (applies, passed review and any tests), `needs_review` (applies, but review or tests did not pass) or `failed` (no patch applied cleanly).

## Voice

| Endpoint | |
|---|---|
| `POST /voice/record/start`, `POST /voice/record/stop` | Push-to-talk using the server machine's microphone; stop returns `{"transcription", "command", "emotion"}` |
| `POST /voice/transcribe` | Upload an audio file (wav, webm, mp3, …) |
| `WS /voice/stream` | Send binary audio chunks; receive `partial` transcripts; send `{"type": "end"}` for the `final` one |
| `POST /voice/speak` | Speak text on the server machine |
| `POST /voice/synthesize` | Get speech back as `audio/wav` |
| `POST /voice/analyze-tone` | Tone analysis for a transcript |

Recognised voice commands: `accept`, `reject`, `undo`, `next file`, `previous file`, `run the tests`, `reindex`, `stop`.

## Configuration and monitoring

| Endpoint | |
|---|---|
| `GET /config/` | Active provider, models, whether keys are set (never the keys) |
| `POST /config/update` | Change provider, models, keys or `ollama_host` at runtime |
| `GET /health` | Liveness |
| `GET /metrics` | Prometheus metrics: per-stage latency, prompt/context sizes, errors |

## v0.5 additions

- `POST /agent/query` and `/agent/stream` responses include `timings_ms` (per stage) and `usage` (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cost_usd`). The stream's `done` event carries both plus `first_token`.
- `POST /agent/edit` accepts `plan_only: true` (returns `status: "planned"` with a plan) and `plan` (an approved/edited plan to execute).
- `POST /agent/apply` accepts `target`: `working_tree` (default), `branch` (commits on `voxcode/<slug>-<id>` via a temporary worktree, leaving your checkout untouched) or `pull_request` (also pushes and opens a PR; needs `GITHUB_TOKEN`), plus `title` / `description`.
- Voice: `POST /voice/stop-speaking` (barge-in), `WS /voice/listen` (stream 16 kHz float32 frames, receive `start` / `end` / `transcript` events), `POST /voice/listen/start|stop` and `GET /voice/listen/events` (SSE) for server-side hands-free listening. Voice responses include prosody-aware emotion (`source: "text+voice"`).

## v0.7 additions

- `POST /agent/feedback` — `{repository_path, query, files?, chunk_ids?, helpful}`. A helpful vote records the files or chunks that answered the question; it returns `{recorded, feedback_entries, min_for_training}`. The learned ranker retrains in the background once `RANKER_MIN_FEEDBACK` entries exist.
- `POST /agent/apply` accepts `query`; the files changed by an accepted patch become feedback for that query.
- Answers (`/agent/query` and the stream's `done` event) include:
  - `quality`: `{groundedness, checked, unsupported, citations, bad_citations}`
  - `routing`: `{difficulty, tier: small|main, escalated, reason}`
  - `cached`: `true` when served from the answer cache

## v0.8 additions

Plan and patch review endpoints (`/reviews…`) are documented in [review.md](review.md#api).

## v0.9 additions: agent crew

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/crews` | Start a run. Body: `task`, `repository_path`, `mode` (`auto`, `answer` or `change`), `run_tests` (default `true`). Returns the run id at once; the crew works in the background. |
| `GET` | `/crews` | Recent runs with status, duration and tokens. |
| `GET` | `/crews/{id}` | Full trace: events, per-agent stats, cost and the result (answer or patch). |
| `GET` | `/crews/{id}/events?after=N` | Server-sent events: `event` for each step (agent, kind, text, data), then `end`. Finished runs are replayed from disk. |
| `POST` | `/crews/{id}/apply` | Apply the run's patch to the working tree and re-index. `409` if there's no patch or it no longer applies. |

The page at `/crew` (and `/crew/{id}` for one run) shows runs live.
