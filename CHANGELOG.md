# Changelog

All notable changes to VoxCode. Versions follow [Semantic Versioning](https://semver.org).

## [0.9.0] — Agent crew and semantic search without a GPU

### Added
- **Agent crew** (`voxcode crew`, `/crew` page, `/crews` API). A planner splits the task into research questions; up to three researchers investigate them in parallel with code tools; then either a writer drafts a grounded answer that a reviewer checks (revising once if under 75% grounded), or a coder writes a patch, a tester runs the suite against it in a copy of the repo, and a reviewer scores it and sends it back with the issues and test output for up to `CREW_MAX_ROUNDS` rounds. Every call is metered per agent (tokens, tool calls, cost) under a shared token budget, streamed live over SSE and saved, so past runs can be replayed.
- **Crew page.** Live agent lanes with tool calls and results, a Gantt timeline of who worked when, run stats, the answer with its groundedness or the patch with reviewer and test badges, and a one-click Apply.
- **Local semantic search.** When no embedding model is installed, VoxCode now trains a small latent semantic model (sublinear TF-IDF over code-aware tokens + truncated SVD, 128 dimensions) per repository and fuses it with BM25. It trains in under a second, is cached on disk, and needs only scipy. On the 30-question retrieval benchmark: MRR **0.490 → 0.533** (+9%), recall@5 0.633 → 0.700 (measured on this repo at v0.9.0). Set `LOCAL_SEMANTIC=false` to turn it off.

### Changed
- `scipy` is now a core dependency.

## [0.8.2] — Audit fixes

From a full re-audit: static analysis (ruff, mypy, Bandit, Vulture, Radon), dependency audit (pip-audit: no known vulnerabilities), coverage, and a manual security review.

### Security
- **DNS rebinding.** With no `API_TOKEN` (the default), a website could point its domain at 127.0.0.1 and send same-origin requests to VoxCode: read and change the config, apply patches and run tests. Requests must now be addressed to localhost, `::1`, `HOST` or a name in the new `ALLOWED_HOSTS` setting, and anything else gets `421`. Docker Compose allows the `voxcode` service name for Prometheus.
- Patches into `.git/` are rejected with a clear message (git already refused them).
- Optional `RERANK_MODEL_REVISION` pins the reranker download. Bandit now reports no medium or high findings: non-security hashes are marked as such, and the two false positives are annotated.

### Fixed
- **`voxcode index` never finished the precise call graph.** The background thread died with the CLI process. CLI commands now build it before exiting, and the server completes any graph left pending.
- **`voxcode ask` / `edit` printed a Python traceback** when the model was unreachable; they now print the error and exit 1.
- `git` subprocesses that could hang now have timeouts.

### Changed
- The CLI is split into one function per command (`main` complexity 42 → small functions) and is now tested (was 0% coverage).
- The VS Code extension README (its Marketplace page) now covers `@vox` chat, hands-free mode, plan review, branch/PR targets and all settings.
- Five settings that were documented nowhere are now in `docs/configuration.md`.

## [0.8.1] — Bug fixes

Found by smoke-testing the server, the web UI and the review page in Chrome against an OpenAI-compatible fake model.

### Fixed
- **Search found nothing in small projects.** The Okapi IDF (from `rank_bm25`) is negative for words in more than half the files, so the best matches scored below zero and were dropped; answers then had no code context. BM25 now uses Lucene's IDF, which is never negative, in Python and in the Rust core (`rank-bm25` is no longer a dependency).
- **Questions were routed to edit mode.** The learned intent model sent "explain the add function" and "summarize the readme" to refactor. It now only refines the keyword rules within the same family: a question is never turned into an edit.
- **The first question waited ~30–50 s.** The precise (Jedi) call graph now builds in the background after indexing; search uses name matching until it's ready. Indexing this repo went from 32 s to 0.3 s.
- **The web UI looked frozen while the model worked.** It now streams the answer as it's written and shows the sources it used.
- **Web UI: the mic did nothing without Chrome's online speech service.** It now falls back to VoxCode's local Whisper and says why when recording fails.
- **Web UI: "Read aloud" claimed success when no speech engine was installed, and read a placeholder instead of the answer.** `/voice/speak` now returns 503 with an install hint, and the extension shows it once.
- **Web UI: smaller fixes.** Clearer errors when the model can't be reached ("Can't reach ollama at … Is Ollama running?"), config saves check for errors, Apply hides after use and Undo is offered, the repo path is remembered, diffs render without blank lines, icons are inlined (no CDN, works offline), and there's a favicon.

## [0.8.0] — Review before it runs

Inspired by [Plannotator](https://github.com/backnotprop/plannotator).

### Added
- **Browser review for plans and patches** (`/review/<id>`). For plans: select text to comment on it, delete it or replace it, plus a version history with a "what changed" diff. For patches: a file tree and line comments. Approve or deny with structured, quoted feedback.
- **Voice review:** hold `V` to dictate a comment on the selection or overall; say "approve" or "deny, …".
- **Claude Code integration:** `voxcode hook install claude` adds a `PermissionRequest` hook on `ExitPlanMode`. Plans open in VoxCode, and denials go back to Claude as feedback. Revisions in the same session become new versions of the same review.
- **VoxCode's own loop** (`POST /reviews/plan`, plus a VS Code command): plan → feedback → re-plan → approve → patch → code review → apply. Accepted patches also feed the learned ranker.
- `voxcode review FILE` (any markdown, or a diff from a file or stdin), `voxcode reviews`, and an archive page at `/review`.
- Share links that carry the review in the URL fragment (compressed, not uploaded).
- Obsidian export of approved plans (`OBSIDIAN_VAULT`, `OBSIDIAN_FOLDER`).

## [0.7.0] — It learns

### Added
- **Learned ranker** (`voxcode/learning/`). A pairwise learning-to-rank model (13 features, numpy, trains in under a second) re-scores fused search candidates. It trains from docstrings (an inverse-cloze task: each docstring becomes a query and is hidden from the index while training) and from relevance feedback. It's saved only when it beats the retrieval order on held-out queries. `voxcode train-ranker`; `voxcode eval --learning` runs the leave-one-out benchmark (MRR@10 0.50 → 0.63 on this repo).
- **Relevance feedback:** `POST /agent/feedback`, plus the `query` field on `/agent/apply`. The ranker retrains automatically after 10 feedback entries. In VS Code, the Chat 👍/👎 buttons send this feedback.
- **Contextual retrieval** (`CONTEXTUAL_CHUNKS=true`): an LLM-written sentence per symbol, indexed with the code and cached by file content.
- **Model cascade** (`CASCADE_MODEL`): easy questions go to a small model; weak or ungrounded answers escalate to the main model.
- **Grounding check** on every answer: `quality.groundedness`, unsupported identifiers and invented file paths. VS Code warns about low groundedness.
- **Answer cache**, scoped to the index version, model and tone; near-duplicate questions match when an embedder is loaded.
- **Learned intent classifier:** logistic regression over word n-grams trained on 240 labelled requests (68% cross-validated accuracy vs. 55% for the keyword rules), with the rules as fallback.
- Metrics `voxcode_answer_cache_hits_total` and `voxcode_cascade_routes_total{tier}`.

### Changed
- README benchmark numbers re-measured on the current repository.
- README artwork builds are now reproducible.

## [0.6.0] — Rust

### Added
- **Rust core** (`native/`, optional `voxcode-native` wheel built with PyO3 + maturin). Tokenizer, BM25 search (inverted index), repository scanner, secret redaction and voice activity detection run in Rust with results identical to the Python fallbacks. `NATIVE_ACCEL=off` forces pure Python, and `/health` reports which backend is active.
- **First-class Rust repositories.** Methods attach to their `impl` type; traits, inline modules and `macro_rules!` are indexed.
- **Rust call graph.** Uses rust-analyzer's call hierarchy when it's installed. Otherwise a built-in resolver follows `use` trees, aliases, globs, and `crate::` / `super::` / `Self::` paths, including calls inside macro arguments.
- **Rust tests in the edit loop.** `cargo test` (with `--workspace` for workspaces); panics, compiler errors and failing test names are traced back to source files; tests run offline in a `rust:1-slim` Docker sandbox after `cargo fetch`.
- `scripts/bench_native.py`, a CI job for the Rust core, and release builds of native wheels for Linux, macOS and Windows.

### Changed
- Index format version 2: existing indexes are rebuilt once.
- The Docker sandbox picks an image to match the project (Rust, Go, Node, Python).

## [0.5.0] — Agent platform

### Added
- **MCP server** (`voxcode mcp`), so Claude Desktop, Claude Code, Cursor and other MCP clients can search and edit through VoxCode.
- **VS Code `@vox` chat participant**, plan review before edits, and applying a change to a branch or a pull request.
- **Hands-free voice:** voice activity detection, optional wake word, barge-in, and tone read from prosody.
- **Smarter retrieval:** precise Python call graph (Jedi), git-history search, optional query rewriting.
- **Agent:** native tool calling for every provider; plan → edit → test → repair; Docker-sandboxed tests.
- **Privacy and cost:** secret redaction, `LOCAL_ONLY` mode, prompt caching, token and cost tracking.
- **Observability:** per-stage timings, OpenTelemetry traces, and a Grafana dashboard.
- **Evaluation:** `voxcode swebench` (SWE-bench localization), `voxcode eval --answers` (LLM judge), embedding model comparison.

## [0.4.0] and earlier

Hybrid search (BM25 + vectors + reranking + call graph), validated and tested patches with undo, streaming answers, six LLM providers, the VS Code extension, the web UI and the CLI.
