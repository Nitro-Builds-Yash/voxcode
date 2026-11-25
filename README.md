<p align="center">
  <img src="./assets/hero.svg" width="100%" alt="voxcode — voice-native coding engine">
</p>

<p align="center">
  <a href="https://github.com/Nitro-Builds-Yash/voxcode/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Nitro-Builds-Yash/voxcode/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/PYTHON-3.10%2B-3776ab?style=for-the-badge&logo=python&logoColor=white">
  <a href="./LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/LICENSE-MIT-8b949e?style=for-the-badge"></a>
</p>

<p align="center">
  <strong>Talk to your codebase. Get answers and validated patches.</strong><br>
  VoxCode indexes your repository, finds the right code with hybrid search, and either answers your question or writes a patch, checks that it applies, optionally runs your tests against it, and shows you the diff before anything is written.
</p>

---

## What it does

| | |
|---|---|
| **Voice in, voice out** | Push-to-talk from VS Code (Ctrl+Alt+V), local Whisper transcription, instant voice commands ("accept", "undo", "next file", "run the tests"), and optional spoken replies (Piper neural TTS or pyttsx3). |
| **Code-aware indexing** | tree-sitter symbol extraction for Python, JavaScript, TypeScript/TSX, Java, Go and Rust, plus Markdown sections. Incremental: only changed files are re-parsed and re-embedded, and a file watcher keeps the index fresh. |
| **Hybrid search** | BM25 with a code-aware tokenizer (`getUserById` → `get user by id`) and dense vectors, merged with Reciprocal Rank Fusion, reranked with a cross-encoder, then expanded with callers and callees from a call graph. |
| **Validated edits** | The model writes a unified diff → `git apply --check` → optional test run in a throwaway copy → LLM review → retry with the errors as feedback. You review a side-by-side diff and accept, reject or undo. |
| **Any model** | Ollama (local), Anthropic, OpenAI, Gemini, Groq, OpenRouter, with streaming for all of them; switch at runtime. |
| **Agentic mode** | The model can call `search_code`, `read_file`, `find_references` and `list_files` before answering. |

Without the optional ML extras, everything except semantic search and reranking still works (keyword search only). With a local Ollama model, nothing leaves your machine.

## Quick start

```bash
git clone https://github.com/Nitro-Builds-Yash/voxcode && cd voxcode
pip install -r requirements.txt        # everything; see "Install options" for lighter installs
cp .env.example .env                   # pick a provider / add an API key
python -m backend.main                 # http://127.0.0.1:8000  (web UI)  ·  /docs (API)
```

Then either open the web UI, install the VS Code extension (below), or use the CLI:

```bash
python scripts/voxcode_cli.py index  .
python scripts/voxcode_cli.py search . "where are patches validated"
python scripts/voxcode_cli.py ask    . "how does incremental indexing work?"
python scripts/voxcode_cli.py edit   . "add a docstring to reciprocal_rank_fusion" --tests
```

### Install options

| Command | Gives you |
|---|---|
| `pip install -e .` | Server, indexing, BM25 search, all LLM providers (fast install, no models) |
| `pip install -e ".[ml]"` | + semantic search and cross-encoder reranking (downloads models on first use) |
| `pip install -e ".[voice]"` | + Whisper speech-to-text, microphone capture, Piper / pyttsx3 speech |
| `pip install -r requirements-cuda.txt` | Everything with CUDA PyTorch |

Redis and a Qdrant server are optional: by default conversation memory is kept in-process and vectors are stored in `storage/qdrant`. `docker compose up` runs the server with Redis and Qdrant (CPU PyTorch by default); put the code you want to index under `./playground` or set `WORKSPACE_DIR`, and index it by its container path, e.g. `/workspace/my-project`. Microphone capture and local speech playback need the server running directly on your machine, not in Docker.

## VS Code extension

```bash
cd vscode-extension && npm install && npm run compile
npx @vscode/vsce package --allow-missing-repository   # builds voxcode-0.3.0.vsix
code --install-extension voxcode-0.3.0.vsix
```

The server must be running (`python -m backend.main`); the extension talks to it at `voiceCoding.backendUrl`.

* **Ctrl+Alt+V** starts/stops recording (the server records from your microphone); **Ctrl+Alt+T** types an instruction instead.
* Questions stream into the *VoxCode* output panel. Change requests open a side-by-side diff; say or click **accept**, **reject**, or **next file**. **undo** reverts the last applied patch.
* The workspace is indexed on startup and re-indexed when files change.
* Settings: `voiceCoding.backendUrl`, `apiToken`, `autoApplyDiffs`, `runTestsOnEdit`, `speakReplies`, `watchWorkspace`.

## API

| Endpoint | Purpose |
|---|---|
| `POST /index/` | Incrementally index a repository (`full`, `watch` options) |
| `GET /index/repos` | Indexed repositories |
| `POST /agent/query` | `mode`: `auto` (answer or edit by intent), `answer`, `edit`, `agentic` |
| `POST /agent/stream` | Same, as server-sent events (tokens, tool calls, or one `edit` event) |
| `POST /agent/preview` · `/apply` · `/undo` | Inspect, apply and revert patches |
| `POST /agent/test` | Run the repository's tests |
| `POST /voice/record/start` · `/record/stop` | Push-to-talk on the server's microphone |
| `POST /voice/transcribe` · `WS /voice/stream` | Upload audio, or stream it with partial transcripts |
| `POST /voice/speak` · `/voice/synthesize` | Speak on the server, or get WAV audio back |
| `GET/POST /config/` | Switch providers, models and keys at runtime |
| `GET /metrics` | Prometheus metrics (per-stage latency, sizes, errors) |

## Architecture

```
voice ─► Whisper ─► command? ──yes──► editor action (accept / undo / next file / run tests)
                       │ no
                       ▼
               intent classifier ──► answer ─┐
                       │                     │
                       ▼ edit                ▼
  hybrid retrieval: BM25 ‖ vectors ─► RRF ─► rerank ─► call-graph expansion ─► token-budgeted context
                       │                                                            │
                       ▼                                                            ▼
     coder ─► git apply --check ─► tests (sandbox copy) ─► reviewer      LLM (streamed) ─► answer / speech
        ▲                                                     │
        └──────────────── feedback on failure ◄───────────────┘
                                                              ▼
                                              diff preview ─► accept ─► apply (undoable)
```

| Folder | Contents |
|---|---|
| `backend/codebase` | scanner, tree-sitter symbols, chunker, incremental indexer, call graph, file watcher |
| `backend/retrieval` | BM25 + tokenizer, embeddings (with SQLite cache), per-repository Qdrant collections, fusion, reranker |
| `backend/agent` | planner, step executor, Q&A pipeline, edit loop (coder / reviewer), tool-using agent, memory |
| `backend/execution` | diff parsing, `git apply` patch service, sandboxed test runner |
| `backend/generation` | context builder, prompts, streaming providers |
| `backend/voice` | Whisper STT, microphone recorder, voice commands, TTS, tone analysis |
| `backend/api` | FastAPI routes and the local-security middleware |

## Retrieval quality

`python -m backend.evaluation.retrieval_eval` runs 30 natural-language questions about this repository (`backend/evaluation/retrieval_queries.json`) and reports how often the right code comes back:

| Search | recall@5 | MRR@10 |
|---|---|---|
| BM25, whitespace tokens (previous version) | 0.50 | 0.40 |
| BM25, code-aware tokens | 0.73 | 0.56 |

With the `ml` extra installed the script also reports semantic, hybrid, and hybrid + rerank rows. The questions were written by the developers, so treat these numbers as a regression check, not a general benchmark.

## Security

The server is meant to run on your own machine and binds to `127.0.0.1`.

* Requests from other websites are rejected (Origin check), so a page in your browser cannot reconfigure VoxCode or read your code.
* Set `API_TOKEN` to require an `X-VoxCode-Token` header, and `ALLOWED_REPO_ROOTS` to restrict which folders can be indexed or edited.
* Patches never touch files outside the repository, are never applied without an explicit accept (unless you enable `autoApplyDiffs`), and the last one can always be undone.
* Testing a proposed patch runs your project's own test suite in a temporary copy of the repository; `POST /agent/test` ("run the tests") runs it in place.

## Tone adaptation

When `EMOTION_ENABLED=true`, a keyword-based analyzer guesses whether you sound rushed, frustrated, tired or curious and adjusts the reply style (for example, shorter answers with the fix first). It only shapes the system prompt; it never changes what is searched. It works on the transcript, not your voice, so it is a heuristic.

## Development

```bash
pip install -e ".[dev]" && pre-commit install
pytest                    # unit + API tests (no models or network needed)
ruff check . && ruff format --check . && mypy
```

CI runs the same checks on Python 3.10 and 3.12, the retrieval benchmark, and compiles the VS Code extension.

## License

[MIT License](./LICENSE)
