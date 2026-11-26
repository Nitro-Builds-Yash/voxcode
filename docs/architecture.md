# Architecture

```mermaid
flowchart LR
    mic([🎙️ Voice]) --> stt[Whisper STT]
    txt([⌨️ Text]) --> intent
    stt --> cmd{Voice command?}
    cmd -- yes --> action[Editor action<br/>accept · undo · next file · run tests]
    cmd -- no --> intent{Question or change?}

    subgraph retrieval [Retrieval]
        direction TB
        bm25[BM25<br/>code-aware tokens] --> rrf[Reciprocal Rank Fusion]
        vec[Vector search<br/>Qdrant] --> rrf
        rrf --> rerank[Cross-encoder rerank] --> graph[Call-graph expansion]
    end

    intent -- question --> retrieval
    intent -- change --> retrieval
    graph --> ctx[Token-budgeted context]

    ctx --> llm[LLM, streamed] --> answer([💬 Answer + spoken reply])
    ctx --> coder[Coder: unified diff]
    coder --> check{git apply --check}
    check -- fails --> coder
    check -- ok --> tests[Tests in a sandbox copy] --> review{LLM review}
    review -- rejected --> coder
    review -- approved --> diff([🔍 Diff view: accept / reject / undo])
```

## Indexing

1. **Scan.** Walk the repository, skipping dependency, cache and VCS folders (`node_modules`, `.venv`, `.git`, …) everywhere, and output folders (`build`, `dist`, …) at the root. Files over `MAX_FILE_BYTES` are skipped.
2. **Diff.** Hash every file and compare with the previous index; only new or changed files are parsed.
3. **Chunk.** tree-sitter extracts functions, methods and classes (Python, JS, TS/TSX, Java, Go, Rust). Each method is its own chunk, a class chunk holds only its header, top-level code becomes `module` chunks, Markdown splits on headings. Each chunk records the names it calls.
4. **Embed.** New chunks are embedded (cached by text hash in SQLite) and upserted into a per-repository Qdrant collection; vectors for changed or deleted files are removed.
5. **Store.** Chunks and file hashes go to `VOXCODE_HOME/indexes/<repo>.json`. The repository key includes a hash of its path, so two checkouts with the same folder name never share an index.

A file watcher (watchdog, debounced) repeats this when files change.

## Answering

`AgentPipeline` runs an `ExecutionPlan` through `StepExecutor`: retrieve → rerank → expand graph → build context → build prompt → generate. The per-repository retriever (BM25 index, graph, vector client) is cached until the index changes. Tone analysis only adds a line to the system prompt; it never touches the search query.

## Editing

`EditAgent` gives the coder the retrieved chunks plus the top files in full with line numbers, then loops up to `EDIT_MAX_ATTEMPTS` times:

* extract the diff from the reply (adding `diff --git` headers so multi-file patches parse),
* `git apply --check` (paths outside the repository are rejected),
* optionally run the test suite against the patch in a temporary copy (dependency folders are symlinked),
* ask the model to review the patch with the check and test output.

Failures are fed back together with the rejected patch. Nothing is written until the user calls `/agent/apply`; the last applied patch can be reverted with `/agent/undo`.

## Agentic mode

`ToolAgent` lets the model call `search_code`, `read_file`, `find_references` and `list_files` (up to six calls) before answering. It uses one JSON object per turn instead of provider-specific function calling, so it works with every provider, including small local models.

## Source map

| Folder | |
|---|---|
| [`voxcode/api`](../voxcode/api) | FastAPI routes, service singletons, local-security middleware |
| [`voxcode/agent`](../voxcode/agent) | planner, executor, Q&A pipeline, edit loop (`coder/`, `reviewer/`), tool agent, memory |
| [`voxcode/codebase`](../voxcode/codebase) | scanner, tree-sitter parser, chunker, incremental indexer, call graph, watcher |
| [`voxcode/retrieval`](../voxcode/retrieval) | BM25 + tokenizer, embeddings and cache, Qdrant store, fusion, reranker |
| [`voxcode/generation`](../voxcode/generation) | context builder, prompts, streaming LLM providers |
| [`voxcode/execution`](../voxcode/execution) | diff parsing, `git apply` patch service, sandboxed test runner |
| [`voxcode/voice`](../voxcode/voice) | Whisper STT, microphone recorder, voice commands, TTS, tone analysis |
| [`voxcode/core`](../voxcode/core) | model registry, repository identity and path checks, JSON extraction |
| [`voxcode/evaluation`](../voxcode/evaluation) | retrieval benchmark and its question set |
| [`voxcode/ui`](../voxcode/ui) | single-page web UI served at `/` |

## v0.5: agent platform

```
voice (VAD/wake word/prosody) ─┐
VS Code @vox / MCP clients ────┼─> API ─> pipeline ─> retrieval (BM25 + vectors + Jedi call graph + git history [+ query rewrite])
                               │                    └> agent: native tool calling │ plan → edit → sandboxed tests → repair
                               │                    └> providers: prompt caching, usage/cost, secret redaction, LOCAL_ONLY
                               └> apply: working tree │ branch (worktree) │ pull request
metrics (Prometheus) + traces (OpenTelemetry) per stage ─> Grafana / Jaeger  (docker compose -f deploy/docker-compose.yml --profile observability up)
```

## v0.6: Rust

**Native core** (`native/`, PyO3 + maturin, abi3 wheel for Python 3.10+). `voxcode/native.py` imports `voxcode_native` when present; every caller keeps its Python implementation as the fallback and `tests/unit/test_native.py` checks both give identical output.

| Hot path | Python | Rust |
|---|---|---|
| Tokenizer | regex | hand-written camelCase splitter, parallel over chunks (rayon) |
| BM25 | `rank_bm25`, scores every document per query term | inverted index, only documents containing a term are touched; same scores |
| Repository scan | `os.walk` | `read_dir` walk, same skip rules and ordering |
| Secret redaction | `re` | `regex` crate (linear time), same patterns |
| Voice activity detection | per-frame numpy | state machine in Rust, `feed_many` for whole buffers |

**Rust repositories.** Symbols cover `impl` blocks (methods attach to the type, generics stripped), traits, inline `mod`s and `macro_rules!`. The call graph comes from rust-analyzer's call hierarchy over LSP when available (`codebase/graph/rust_analyzer.py`); otherwise `codebase/graph/rust_graph.py` resolves calls through the module system: file layout and `Cargo.toml` crates (lib, `src/bin`, `tests`, `examples`), `use` trees with aliases and globs, `crate::`/`self::`/`super::`/`Self::` paths, `self.method()`, and calls inside macro arguments. Tests run with `cargo test` (plus `--workspace`), offline inside the `rust:1-slim` sandbox after `cargo fetch`; panics, rustc `-->` locations and failing test names map back to source files for the repair step.
