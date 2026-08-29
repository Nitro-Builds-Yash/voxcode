<p align="center">
  <img src="./assets/hero.svg" width="100%" alt="voxcode — voice-native coding engine">
</p>

<p align="center">
  <a href="https://github.com/Nitro-Builds-Yash/voxcode/releases/latest"><img alt="Download voxcode" src="https://img.shields.io/badge/DOWNLOAD-v1.0-0969da?style=for-the-badge&logo=python&logoColor=white"></a>
  <a href="https://github.com/Nitro-Builds-Yash/voxcode/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Nitro-Builds-Yash/voxcode?display_name=tag&style=for-the-badge&label=RELEASE&color=238636"></a>
  <img alt="Python 3.7+" src="https://img.shields.io/badge/PYTHON-3.7%2B-3776ab?style=for-the-badge&logo=python&logoColor=white">
  <a href="./LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/LICENSE-MIT-8b949e?style=for-the-badge"></a>
</p>

<p align="center">
  <strong>Speak your intent. Get production-ready code.</strong><br>
  A voice-native coding engine that listens, indexes your codebase with AST analysis, searches with hybrid BM25 + semantic retrieval, and generates context-aware code patches live.
</p>

---

## The engine that listens before it writes

<p align="center">
  <img src="./assets/diagram.svg" width="100%" alt="Architecture pipeline diagram showing Listen, Think, Generate stages">
</p>

The voxcode agent pipeline processes incoming voice audio locally using Whisper STT, passing the transcribed intent to the 'Think' phase. Here, a hybrid search utilizing BM25 and dense vectors queries the AST symbol index of your repository. Finally, the 'Generate' phase produces a context-aware code diff that is instantly applied live to your active workspace.

## Code should know its context

Traditional AI coding assistants struggle with large codebases because they rely on simple keyword search or limited context windows. voxcode exists to bridge the gap between human intent and structural codebase intelligence, allowing you to verbally instruct an agent that truly understands the relationships between your functions, classes, and modules.

<table>
  <tr>
    <td>
      <strong>Native & offline</strong><br>
      No cloud, no accounts. Runs fully local with Whisper STT and local LLM models.
    </td>
    <td>
      <strong>AST Indexing</strong><br>
      Extracts functions, classes, and symbols across Python, JavaScript, TypeScript, and Java.
    </td>
  </tr>
  <tr>
    <td>
      <strong>Hybrid Search</strong><br>
      BM25 lexical + dense semantic vector retrieval, fused with Reciprocal Rank Fusion.
    </td>
    <td>
      <strong>Voice-native</strong><br>
      Continuous voice dictation, voice commands, and live diff sync to your editor.
    </td>
  </tr>
</table>

## Install

1. `git clone https://github.com/Nitro-Builds-Yash/voxcode`
2. `pip install -r requirements.txt`
3. `python -m backend.main`

> [!NOTE]
> Requires Python 3.7+. For voice features, a microphone must be connected and the Whisper model downloaded.

## Usage

| Command | What it does |
| --- | --- |
| `python -m backend.main` | Start the voxcode engine |
| `python scripts/indexing/test_index.py` | Index a repository and extract symbols |
| `python scripts/indexing/test_bm25.py` | Test BM25 keyword search |
| `python scripts/indexing/test_hybrid.py` | Test hybrid retrieval pipeline |
| `python scripts/testing/test_agent_pipeline.py` | Run full agent pipeline test |
| VS Code Extension (`vscode-extension/`) | Live voice dictation and diff sync in editor |

## Architecture

voxcode parses your codebase into AST symbol trees — functions, classes, imports — chunks them into semantically coherent units, builds a BM25 keyword index and a dense embedding vector store, and retrieves the most relevant context for any voice query using Reciprocal Rank Fusion. The language model generates a minimal, context-aware code patch applied as a live diff.

## License

[MIT License](./LICENSE)

<p align="center">
  <img src="./assets/live-status.svg" width="100%" alt="Live status banner indicating voxcode active">
</p>
