import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from voxcode.codebase.chunker.chunker import build_chunks
from voxcode.codebase.graph import rust_analyzer
from voxcode.codebase.graph.precise import build_call_edges
from voxcode.codebase.graph.rust_graph import RustResolver, module_of
from voxcode.config.settings import settings
from voxcode.execution.testing import docker_sandbox
from voxcode.execution.testing.test_runner import (
    detect_test_command,
    failure_files,
    run_tests,
    run_tests_with_patch,
)

pytest.importorskip("tree_sitter_language_pack")


def chunks_of(repo: Path):
    out = []
    for path in sorted(repo.rglob("*.rs")):
        out += build_chunks(path.relative_to(repo).as_posix(), path.read_text(), "rust")
    return out


def named_edges(chunks, edges):
    name = {c.chunk_id: f"{c.file_path}:{c.symbol_name}" for c in chunks}
    return {name[k]: sorted(name[v] for v in vs) for k, vs in edges.items()}


def test_module_paths():
    assert module_of("src/lib.rs", ".") == (".",)
    assert module_of("src/a/mod.rs", ".") == (".", "a")
    assert module_of("src/a/b.rs", ".") == (".", "a", "b")
    assert module_of("crates/core/src/x.rs", "crates/core") == ("crates/core", "x")
    assert module_of("src/main.rs", ".") == (".#main",)
    assert module_of("src/bin/tool.rs", ".") == (".#bin/tool",)
    assert module_of("tests/it.rs", ".") == (".#tests/it",)


def test_rust_symbols_and_chunks(rust_repo):
    names = {c.symbol_name for c in chunks_of(rust_repo)}
    assert {
        "Store.open",
        "Store.add",
        "Store.validate",
        "open_default",
        "normalize",
        "tests.adds_items",
    } <= names


def test_rust_call_graph_follows_the_module_system(rust_repo, monkeypatch):
    monkeypatch.setattr(settings, "RUST_ANALYZER", "off")
    chunks = chunks_of(rust_repo)
    edges = named_edges(chunks, build_call_edges(rust_repo, chunks, {c.file_path for c in chunks}))
    assert edges["src/lib.rs:open_default"] == ["src/store.rs:Store.open", "src/util.rs:log"]
    assert edges["src/store.rs:Store.open"] == [
        "src/util.rs:log",
        "src/util.rs:normalize",
    ]  # `normalize as norm`
    assert edges["src/store.rs:Store.add"] == ["src/store.rs:Store.validate", "src/util.rs:normalize"]
    assert edges["src/store.rs:tests.adds_items"] == [
        "src/store.rs:Store.add",
        "src/store.rs:Store.count",
        "src/store.rs:Store.open",
    ]
    assert edges["src/main.rs:main"] == ["src/lib.rs:open_default", "src/store.rs:Store.count"]
    assert edges["tests/integration.rs:opens_empty"] == [
        "src/store.rs:Store.count",
        "src/store.rs:Store.open",
    ]
    # std methods are never guessed: `items.len()` / `items.push()` link nowhere
    assert "src/store.rs:Store.count" not in edges


def test_rust_edges_are_incremental(rust_repo, monkeypatch):
    monkeypatch.setattr(settings, "RUST_ANALYZER", "off")
    chunks = chunks_of(rust_repo)
    full = build_call_edges(rust_repo, chunks, {c.file_path for c in chunks})
    again = build_call_edges(rust_repo, chunks, {"src/main.rs"}, full)
    assert again == full


def test_cargo_test_command_and_sandbox_image(rust_repo, monkeypatch):
    assert detect_test_command(rust_repo) == ["cargo", "test", "--quiet"]
    (rust_repo / "Cargo.toml").write_text((rust_repo / "Cargo.toml").read_text() + "\n[workspace]\n")
    assert detect_test_command(rust_repo)[-1] == "--workspace"
    assert docker_sandbox.sandbox_image(rust_repo) == "rust:1-slim"
    assert docker_sandbox.container_test_command(["cargo", "test", "--quiet"])[-1] == "--offline"
    monkeypatch.setattr(settings, "SANDBOX_IMAGE", "my/rust:dev")
    assert docker_sandbox.sandbox_image(rust_repo) == "my/rust:dev"
    args = docker_sandbox.build_run_args("n", rust_repo, "rust:1-slim")
    assert "CARGO_HOME=/tmp/.cargo" in args


def test_failure_files_from_cargo_output(rust_repo):
    output = textwrap.dedent(
        """\
        error[E0308]: mismatched types
          --> src/util.rs:3:5
        ---- store::tests::adds_items stdout ----
        thread 'store::tests::adds_items' panicked at src/store.rs:40:9:
        assertion `left == right` failed
        ---- opens_empty stdout ----
        """
    )
    assert failure_files(output, rust_repo) == ["src/store.rs", "src/util.rs", "tests/integration.rs"]


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")
def test_patch_is_tested_with_cargo(rust_repo, monkeypatch):
    monkeypatch.setattr(settings, "TEST_SANDBOX", "local")
    subprocess.run(["git", "init", "-q"], cwd=rust_repo, check=True)
    good = run_tests(rust_repo, timeout=300)
    assert good is not None and good.passed, good.output
    import difflib

    original = (rust_repo / "src/store.rs").read_text()
    broken = original.replace("assert!(!item.is_empty()", "assert!(item.is_empty()")
    breaking = "".join(
        difflib.unified_diff(
            original.splitlines(True), broken.splitlines(True), "a/src/store.rs", "b/src/store.rs"
        )
    )
    report = run_tests_with_patch(rust_repo, breaking, timeout=300)
    assert report is not None and not report.passed
    assert "src/store.rs" in failure_files(report.output, rust_repo)
    assert (rust_repo / "src/store.rs").read_text() == original  # original untouched


FAKE_RA = r"""
import json, sys

def read():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if line.strip() == b"":
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
    return json.loads(sys.stdin.buffer.read(length))

def send(msg):
    body = json.dumps(msg).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()

root = None
while True:
    msg = read()
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        root = msg["params"]["rootUri"]
        send({"jsonrpc": "2.0", "id": mid, "result": {"capabilities": {}}})
    elif method == "initialized":
        send({"jsonrpc": "2.0", "method": "experimental/serverStatus", "params": {"health": "ok", "quiescent": True}})
    elif method == "textDocument/prepareCallHierarchy":
        pos = msg["params"]["position"]
        send({"jsonrpc": "2.0", "id": mid, "result": [{"name": "f", "uri": msg["params"]["textDocument"]["uri"], "line": pos["line"]}]})
    elif method == "callHierarchy/outgoingCalls":
        item = msg["params"]["item"]
        result = []
        if item["uri"].endswith("src/lib.rs") and item["line"] == 6:  # open_default
            result = [{"to": {"uri": root + "/src/store.rs", "selectionRange": {"start": {"line": 8, "character": 11}}}},
                      {"to": {"uri": "file:///rustlib/std/fmt.rs", "selectionRange": {"start": {"line": 1, "character": 0}}}}]
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": mid, "result": None})
    elif method == "exit":
        sys.exit(0)
"""


def test_rust_analyzer_client_speaks_lsp(rust_repo, tmp_path):
    server = tmp_path / "fake_ra.py"
    server.write_text(FAKE_RA)
    launcher = tmp_path / "rust-analyzer"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {server}\n")
    launcher.chmod(0o755)
    chunks = chunks_of(rust_repo)
    edges = rust_analyzer.call_edges(rust_repo, chunks, {"src/lib.rs"}, binary=str(launcher))
    assert named_edges(chunks, edges) == {"src/lib.rs:open_default": ["src/store.rs:Store.open"]}


def test_missing_rust_analyzer_falls_back(monkeypatch):
    rust_analyzer.find_binary.cache_clear()
    monkeypatch.setattr(settings, "RUST_ANALYZER", "/nonexistent/rust-analyzer")
    assert rust_analyzer.find_binary(settings.RUST_ANALYZER) is None
    assert rust_analyzer.find_binary("off") is None


def test_resolver_without_cargo_toml(tmp_path):
    (tmp_path / "a.rs").write_text("fn helper() {}\nfn main() { helper(); }\n")
    chunks = build_chunks("a.rs", (tmp_path / "a.rs").read_text(), "rust")
    resolver = RustResolver(tmp_path, chunks)
    resolver.collect()
    assert named_edges(chunks, resolver.edges_for({"a.rs"})) == {"a.rs:main": ["a.rs:helper"]}
