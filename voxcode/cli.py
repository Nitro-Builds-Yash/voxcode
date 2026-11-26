"""`voxcode` command line.

voxcode serve                         start the API server and web UI
voxcode index  [PATH] [--full]        index a repository (default: current folder)
voxcode search "query" [--repo PATH]  show matching code
voxcode ask    "question" [--repo PATH]       streamed answer
voxcode edit   "instruction" [--repo PATH] [--tests]   propose a validated patch
voxcode eval   [--repo PATH] [--answers | --learning]  retrieval / answer / learned-ranker benchmark
voxcode train-ranker [--repo PATH]    train the learned ranker (docstrings + feedback)
voxcode mcp    [--repo PATH]          run as an MCP server (Claude, Cursor, ...)
voxcode swebench INSTANCES.jsonl      SWE-bench style evaluation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="voxcode", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="start the API server and web UI")

    index = sub.add_parser("index", help="index a repository")
    index.add_argument("path", nargs="?", default=".")
    index.add_argument("--full", action="store_true", help="rebuild from scratch")

    for name, help_text in (
        ("search", "show matching code"),
        ("ask", "ask a question (streamed)"),
        ("edit", "propose a validated patch (nothing is written)"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("text")
        command.add_argument("--repo", default=".")
        if name == "edit":
            command.add_argument("--tests", action="store_true", help="run tests against the patch")

    evaluate = sub.add_parser("eval", help="retrieval or answer quality benchmark")
    evaluate.add_argument("--repo", default=".")
    evaluate.add_argument(
        "--answers", action="store_true", help="LLM-judged answer quality instead of retrieval"
    )
    evaluate.add_argument("--embedding-models", default="", help="comma-separated models to compare")
    evaluate.add_argument("--learning", action="store_true", help="leave-one-out test of the learned ranker")

    trainer = sub.add_parser("train-ranker", help="train the learned ranker for a repository")
    trainer.add_argument("--repo", default=".")
    trainer.add_argument(
        "--min-feedback",
        type=int,
        default=0,
        help="require this many feedback entries (default: train anyway)",
    )

    mcp = sub.add_parser("mcp", help="run as a Model Context Protocol server")
    mcp.add_argument("--repo", default=None, help="default repository (default: current folder)")
    mcp.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8765)

    swe = sub.add_parser("swebench", help="SWE-bench style evaluation (localization and resolve rate)")
    swe.add_argument("instances", help="JSONL file of SWE-bench instances")
    swe.add_argument("--limit", type=int, default=0)
    swe.add_argument("--workdir", default="", help="where to clone repositories")
    swe.add_argument("--resolve", action="store_true", help="also generate patches and run the tests")
    swe.add_argument("--output", default="swebench_results.jsonl")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from voxcode.main import main as serve

        serve()
        return

    if args.command == "train-ranker":
        import json as _json

        from voxcode.api import services
        from voxcode.learning.training import train

        stats = services.index_repository(Path(args.repo).resolve())
        repo_index = services.indexer.store.load(stats.repo_key)
        assert repo_index is not None
        report = train(repo_index, min_feedback=args.min_feedback)
        print(_json.dumps({k: v for k, v in report.items() if k != "features"}, indent=2))
        return

    if args.command == "eval":
        if args.learning:
            from voxcode.evaluation import learning_eval

            learning_eval.main(["--repo", args.repo])
            return
        if args.answers:
            from voxcode.evaluation import answer_eval

            answer_eval.main(["--repo", args.repo])
        else:
            from voxcode.evaluation import retrieval_eval

            extra = ["--embedding-models", args.embedding_models] if args.embedding_models else []
            retrieval_eval.main(["--repo", args.repo, *extra])
        return

    if args.command == "mcp":
        try:
            from voxcode.integrations.mcp_server import run
        except ImportError:
            parser.exit(1, "voxcode: the MCP server needs the 'mcp' extra: pip install 'voxcode[mcp]'\n")
        run(args.repo, args.transport, args.host, args.port)
        return

    if args.command == "swebench":
        from voxcode.evaluation import swebench

        argv = [args.instances, "--output", args.output]
        if args.limit:
            argv += ["--limit", str(args.limit)]
        if args.workdir:
            argv += ["--workdir", args.workdir]
        if args.resolve:
            argv.append("--resolve")
        swebench.main(argv)
        return

    from voxcode.agent.agent_request import AgentRequest
    from voxcode.agent.runtime import runtime_cache
    from voxcode.api import services
    from voxcode.core.repository import RepositoryPathError, resolve_repository_path

    try:
        repo = resolve_repository_path(args.path if args.command == "index" else args.repo)
    except RepositoryPathError as exc:
        parser.exit(2, f"voxcode: {exc}\n")

    if args.command == "index":
        stats = services.index_repository(repo, full=args.full)
        print(
            f"Indexed {stats.files_indexed} files ({stats.files_changed} changed, "
            f"{stats.files_removed} removed) into {stats.chunks_created} chunks in "
            f"{stats.duration_seconds}s · {'semantic + keyword' if stats.semantic_indexed else 'keyword only'}"
        )
        return

    key = services.ensure_indexed(repo)
    request = AgentRequest(session_id="cli", repository_key=key, query=args.text)

    if args.command == "search":
        retriever = runtime_cache.retriever(key)
        for hit in retriever.expand(retriever.search(args.text, 8)):
            print(f"[{hit.source:8}] {hit.file_path}:{hit.start_line}  {hit.symbol_name}")
    elif args.command == "ask":
        for event in services.pipeline().stream(request):
            if event["type"] == "token":
                sys.stdout.write(event["text"])
                sys.stdout.flush()
        print()
    else:
        edit = services.edit_agent().run(request, repo, run_tests=args.tests)
        print(f"[{edit.status}] {edit.summary}\n")
        print(edit.diff or "(no patch)")


if __name__ == "__main__":
    main()
