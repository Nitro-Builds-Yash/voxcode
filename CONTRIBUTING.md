# Contributing to VoxCode

Thanks for helping out! The project is small enough to understand in an afternoon; [docs/architecture.md](docs/architecture.md) is the best place to start.

## Setup

```bash
git clone https://github.com/Nitro-Builds-Yash/voxcode && cd voxcode
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ,ml or ,voice to work on semantic search or speech
pre-commit install
```

The VS Code extension lives in `vscode-extension/`: `npm install && npm run compile`, then press <kbd>F5</kbd> in VS Code to launch it in a development window.

## Before you open a pull request

```bash
pytest                                  # tests need no models or network
ruff check . && ruff format --check .
mypy
voxcode eval                            # retrieval changes: compare before/after
```

* Put unit tests in `tests/unit/` and HTTP-level tests in `tests/integration/`.
* Tests must not download models or call real LLMs. Use `FakeEmbedder` and `ScriptedProvider` from `tests/conftest.py`.
* If you change chunking or parsing, bump `INDEX_FORMAT_VERSION` in `voxcode/codebase/indexer/index_store.py` so existing indexes are rebuilt.
* New settings go in `voxcode/config/settings.py`, `.env.example` and `docs/configuration.md`.

## Where things live

| You want to… | Look in |
|---|---|
| Support a new language | `voxcode/codebase/parser/` (`languages.py`, `symbols.py`) |
| Add an LLM provider | `voxcode/generation/providers/` |
| Change how answers are built | `voxcode/agent/pipeline.py`, `voxcode/generation/` |
| Change the edit loop | `voxcode/agent/edit_agent.py`, `voxcode/execution/` |
| Add an endpoint | `voxcode/api/routes/` |
| Add a voice command | `voxcode/voice/commands.py` and `vscode-extension/src/extension.ts` |
