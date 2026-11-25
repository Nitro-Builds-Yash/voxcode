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
