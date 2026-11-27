# Configuration

Every setting can be set as an environment variable or in a `.env` file in the folder you start VoxCode from (copy [`.env.example`](../.env.example)). All of them are optional. They are defined in [`voxcode/config/settings.py`](../voxcode/config/settings.py).

## LLM providers

| Setting | Default | |
|---|---|---|
| `DEFAULT_PROVIDER` | `ollama` | `ollama`, `anthropic`, `openai`, `gemini`, `groq` or `openrouter`. Can be changed at runtime via `POST /config/update` or the web UI. |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | `http://localhost:11434` / `qwen3:8b` | Local models; nothing leaves your machine. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | – / `claude-sonnet-5-5` | |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | – / `gpt-4.1` | |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-2.5-flash` | |
| `GROQ_API_KEY` / `GROQ_MODEL` | – / `llama-3.3-70b-versatile` | |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | – / `deepseek/deepseek-chat` | |
| `PROVIDER_TIMEOUT_SECONDS` | `120` | Per request. |
| `MAX_OUTPUT_TOKENS` | `8192` | Answer length limit (Anthropic, Gemini). |

## Search and indexing

| Setting | Default | |
|---|---|---|
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Needs the `ml` extra. Without it, search is keyword-only. |
| `RERANK_ENABLED` / `RERANK_MODEL` | `true` / `BAAI/bge-reranker-base` | Cross-encoder reranking (needs the `ml` extra). |
| `DEFAULT_TOP_K` | `8` | Chunks passed to the model after reranking. |
| `RETRIEVAL_CANDIDATES` | `30` | Chunks fetched by BM25 and vectors before reranking. |
| `GRAPH_EXPANSION` | `4` | Extra callers/callees added from the call graph (0 disables). |
| `CONTEXT_MAX_TOKENS` | `12000` | Prompt context budget. |
| `MAX_FILE_BYTES` | `1000000` | Larger files are not indexed. |
| `MAX_CHUNK_LINES` | `120` | Longer symbols are split. Changing it rebuilds indexes. |
| `WATCH_ENABLED` | `false` | Re-index every repository on file changes (the VS Code extension turns this on per workspace). |

## Storage

| Setting | Default | |
|---|---|---|
| `VOXCODE_HOME` | `~/.voxcode` | Indexes, vectors and the embedding cache. Nothing is written into your repositories. |
| `QDRANT_MODE` | `local` | `local` stores vectors under `VOXCODE_HOME`; `server` uses `QDRANT_HOST:QDRANT_PORT`. |
| `MEMORY_BACKEND` | `auto` | `auto` uses Redis when reachable, else in-process memory. `redis` or `memory` to force one. |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` | |
| `MEMORY_TTL_SECONDS` / `MEMORY_MAX_MESSAGES` | 7 days / `50` | Conversation history per session. |

## Voice

| Setting | Default | |
|---|---|---|
| `WHISPER_MODEL` | `base` | Any faster-whisper size (`tiny` … `large-v3`). |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `cpu` / `int8` | Use `cuda` / `float16` on a GPU. |
| `WHISPER_BEAM_SIZE` | `1` | Higher is slightly more accurate and slower. |
| `TTS_ENGINE` | `auto` | `piper`, `pyttsx3` or `none`. `auto` uses Piper when `PIPER_MODEL` is set. |
| `PIPER_MODEL` | – | Path to a Piper `.onnx` voice. |
| `EMOTION_ENABLED` | `true` | Adapt reply style to tone (keyword heuristic; only affects the system prompt). |

## Code edits

| Setting | Default | |
|---|---|---|
| `EDIT_MAX_ATTEMPTS` | `3` | Coder → check → review rounds before giving up. |
| `TEST_TIMEOUT_SECONDS` | `180` | Per test run. |

## Server and security

| Setting | Default | |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Keep `127.0.0.1` unless you also set `API_TOKEN`. |
| `API_TOKEN` | – | Require `X-VoxCode-Token` on every request. |
| `ALLOWED_REPO_ROOTS` | – | Comma-separated folders that may be indexed or edited. |
| `ALLOWED_ORIGINS` | – | Extra browser origins allowed to call the API. |
| `PRELOAD_MODELS` | `true` | Load models in the background at startup. |
| `RELOAD` / `DEBUG` | `false` | Development only. |

## v0.5 settings

| Setting | Default | Purpose |
|---|---|---|
| `LOCAL_ONLY` | `false` | Block every non-localhost LLM provider. |
| `REDACT_SECRETS` | `true` | Replace API keys, private keys, JWTs and connection strings with placeholders before context leaves the machine. |
| `MODEL_PRICES` | built-in table | JSON map `model -> [input, output]` USD per 1M tokens, used for `voxcode_llm_cost_usd_total`. |
| `OPENAI_BASE_URL` | OpenAI | Point the OpenAI provider at any compatible server. |
| `NATIVE_TOOL_CALLING` | `true` | Use provider-native function calling in agent mode; falls back to the JSON protocol. |
| `PLAN_EDITS` | `true` | Draft a file-level plan before generating a diff. |
| `QUERY_REWRITE` | `false` | LLM rewrites the question into extra keyword/code queries before retrieval. |
| `PRECISE_CALL_GRAPH` | `true` | Resolve call edges with Jedi (needs the `analysis` extra). |
| `GIT_HISTORY_COMMITS` | `300` | Commits indexed for "when/why did X change" questions. |
| `TEST_SANDBOX` | `auto` | `docker` runs tests in a network-isolated container; `auto` uses Docker when available. |
| `SANDBOX_IMAGE` / `SANDBOX_MEMORY` / `SANDBOX_CPUS` | `python:3.12-slim` / `2g` / `2` | Sandbox container limits. |
| `GITHUB_TOKEN` | – | Required for `apply` with `target=pull_request`. |
| `VAD_SILENCE_SECONDS` / `VAD_ENERGY_THRESHOLD` | `0.8` / `0.015` | Hands-free utterance segmentation. |
| `WAKE_WORD` / `WAKE_WORD_THRESHOLD` | – / `0.5` | openWakeWord model name (needs the `wakeword` extra). |
| `OTEL_ENABLED` / `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` | `false` / `http://localhost:4317` / `voxcode` | OpenTelemetry tracing of every pipeline stage. |

## v0.6 settings (Rust)

| Setting | Default | Purpose |
|---|---|---|
| `NATIVE_ACCEL` | `auto` | Use the Rust extension (`voxcode-native`) for tokenizing, BM25, scanning, redaction and VAD when it is installed. `off` forces the Python code; results are identical either way. |
| `RUST_ANALYZER` | `auto` | Rust call graph source. `auto` uses `rust-analyzer` from `PATH` if it runs, otherwise the built-in tree-sitter resolver; a path picks a specific binary; `off` always uses the resolver. |
| `RUST_ANALYZER_TIMEOUT` | `60` | Seconds to wait for rust-analyzer to finish indexing (the whole pass is capped at 3x this). |

`SANDBOX_IMAGE` left at its default now picks `rust:1-slim` for Cargo projects (`golang:1` for Go, `node:22-slim` for Node).

## v0.7 settings (learning)

| Setting | Default | Purpose |
|---|---|---|
| `LEARNED_RANKER` | `true` | Re-score search candidates with the trained ranker for the repository, if one exists. |
| `RANKER_AUTO_TRAIN` | `true` | Retrain after indexing and after feedback. |
| `RANKER_MIN_FEEDBACK` | `10` | Automatic training waits for this many feedback entries. Docstring self-supervision alone helped held-out docstring queries but not reliably real questions. `voxcode train-ranker` trains regardless. |
| `CONTEXTUAL_CHUNKS` | `false` | LLM context sentence per chunk at index time (one call per changed file, cached). Turning it on or off rebuilds the index. |
| `CASCADE_PROVIDER` / `CASCADE_MODEL` | – / – | Small model for easy questions; the cascade is off while `CASCADE_MODEL` is empty. |
| `CASCADE_MAX_DIFFICULTY` | `0.5` | Questions scored at or below this go to the small model. |
| `CASCADE_MIN_GROUNDEDNESS` | `0.75` | Small-model answers below this are regenerated by the main model. |
| `ANSWER_CACHE` / `ANSWER_CACHE_SIZE` / `ANSWER_CACHE_TTL_SECONDS` | `true` / `256` / `3600` | Answer cache for questions asked without conversation history. |
| `INTENT_MODEL` / `INTENT_MIN_CONFIDENCE` | `true` / `0.3` | Learned intent classifier; below the confidence the keyword rules decide. |

## v0.8 settings (review)

| Setting | Default | Purpose |
|---|---|---|
| `REVIEW_PORT` | `19432` | Port for the review server that `voxcode hook` / `voxcode review` start when VoxCode isn't already running. It's fixed so review pages survive between hook calls and can be forwarded over SSH. |
| `OBSIDIAN_VAULT` | – | Save approved plans to this Obsidian vault. |
| `OBSIDIAN_FOLDER` | `VoxCode/Plans` | Folder inside the vault. |

Set `VOXCODE_NO_BROWSER=1` to print review URLs instead of opening a browser. SSH sessions do this automatically.

## v0.8.1

| Setting | Default | Purpose |
|---|---|---|
| `BACKGROUND_CALL_GRAPH` | `true` | Build the precise call graph after indexing, in the background, so the first question doesn't wait for it (about 30 s on a mid-size repo). Search uses name matching until it's ready. |

## v0.8.2

| Setting | Default | Purpose |
|---|---|---|
| `ALLOWED_HOSTS` | – | Extra host names the server may be reached by (comma-separated), e.g. a LAN name when `HOST=0.0.0.0`. Requests to any other host get `421`, which blocks DNS-rebinding attacks. `localhost`, `127.0.0.1`, `::1` and `HOST` always work; `*` disables the check. |
| `RERANK_MODEL_REVISION` | – | Pin the reranker to a Hugging Face commit (supply-chain safety). |
| `EMBEDDING_BATCH_SIZE` | `64` | Chunks per embedding batch. |
| `WATCH_DEBOUNCE_SECONDS` | `2.0` | Wait this long after the last file change before re-indexing. |
| `QDRANT_COLLECTION_PREFIX` | `voxcode` | Prefix of the Qdrant collections (one per repository). |
| `APP_NAME` / `APP_VERSION` | `VoxCode` / current | Reported by `/health` and the API docs. |

## v0.9.0

| Setting | Default | Purpose |
|---|---|---|
| `LOCAL_SEMANTIC` | `true` | With no embedding model installed, train a small latent semantic model per repository (TF-IDF + SVD) and fuse it with BM25. Cached under `CACHE_DIR/lsa`. |
| `CREW_MAX_RESEARCHERS` | `3` | Most researchers the crew runs in parallel. |
| `CREW_MAX_ROUNDS` | `2` | Most coder → tester → reviewer rounds for a change. |
| `CREW_MAX_TOKENS` | `200000` | Token budget for one crew run; agents stop when it runs out. |
