# Changelog

All notable changes to VoxCode. Versions follow [Semantic Versioning](https://semver.org).

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
