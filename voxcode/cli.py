"""`voxcode` command line.

voxcode serve                         start the API server and web UI
voxcode index  [PATH] [--full]        index a repository (default: current folder)
voxcode search "query" [--repo PATH]  show matching code
voxcode ask    "question" [--repo PATH]       streamed answer
voxcode edit   "instruction" [--repo PATH] [--tests]   propose a validated patch
voxcode eval   [--repo PATH] [--answers | --learning]  retrieval / answer / learned-ranker benchmark
voxcode train-ranker [--repo PATH]    train the learned ranker (docstrings + feedback)
voxcode mcp    [--repo PATH]          run as an MCP server (Claude, Cursor, ...)
voxcode review FILE.md [--diff]       annotate a plan, doc or diff in the browser; prints the feedback
voxcode reviews                       list past reviews (open http://127.0.0.1:8000/review)
voxcode hook install|uninstall claude [--project]   review Claude Code plans in VoxCode
voxcode swebench INSTANCES.jsonl      SWE-bench style evaluation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
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

    review = sub.add_parser("review", help="annotate a markdown file or diff in the browser")
    review.add_argument("file", help="markdown plan/document, or a .diff/.patch file ('-' reads stdin)")
    review.add_argument("--diff", action="store_true", help="treat the input as a unified diff")
    sub.add_parser("reviews", help="list past reviews")
    hook = sub.add_parser("hook", help="agent integrations (Claude Code plan mode)")
    hook.add_argument("action", choices=["install", "uninstall", "claude-plan"])
    hook.add_argument("agent", nargs="?", default="claude", choices=["claude"])
    hook.add_argument(
        "--project", action="store_true", help="use ./.claude/settings.json instead of ~/.claude"
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

    return parser


# ------------------------------------------------------------------ commands
# Each returns a process exit code. Repository commands run in a short-lived
# process, so the precise call graph is built before exiting (a background
# thread would die with the process and leave the index on name matching).


def _serve(args) -> int:
    from voxcode.main import main as serve

    serve()
    return 0


def _repo(parser: argparse.ArgumentParser, path: str) -> Path:
    from voxcode.core.repository import RepositoryPathError, resolve_repository_path

    try:
        return resolve_repository_path(path)
    except RepositoryPathError as exc:
        parser.exit(2, f"voxcode: {exc}\n")


def _index(args, parser) -> int:
    from voxcode.api import services

    stats = services.index_repository(_repo(parser, args.path), full=args.full)
    print(
        f"Indexed {stats.files_indexed} files ({stats.files_changed} changed, "
        f"{stats.files_removed} removed) into {stats.chunks_created} chunks in "
        f"{stats.duration_seconds}s · {'semantic + keyword' if stats.semantic_indexed else 'keyword only'}"
    )
    return 0


def _query_command(args, parser) -> int:
    from voxcode.agent.agent_request import AgentRequest
    from voxcode.agent.runtime import runtime_cache
    from voxcode.api import services

    repo = _repo(parser, args.repo)
    key = services.ensure_indexed(repo)
    if args.command == "search":
        retriever = runtime_cache.retriever(key)
        hits = retriever.expand(retriever.search(args.text, 8))
        for hit in hits:
            print(f"[{hit.source:8}] {hit.file_path}:{hit.start_line}  {hit.symbol_name}")
        if not hits:
            print("No matches.")
        return 0
    request = AgentRequest(session_id="cli", repository_key=key, query=args.text)
    try:
        if args.command == "ask":
            for event in services.pipeline().stream(request):
                if event["type"] == "token":
                    sys.stdout.write(event["text"])
                    sys.stdout.flush()
            print()
        else:
            edit = services.edit_agent().run(request, repo, run_tests=args.tests)
            print(f"[{edit.status}] {edit.summary}\n")
            print(edit.diff or "(no patch)")
    except Exception as exc:  # e.g. the model is unreachable: a message, not a traceback
        print(f"\nvoxcode: {exc}", file=sys.stderr)
        return 1
    return 0


def _train_ranker(args, parser) -> int:
    import json

    from voxcode.api import services
    from voxcode.learning.training import train

    stats = services.index_repository(_repo(parser, args.repo))
    repo_index = services.indexer.store.load(stats.repo_key)
    assert repo_index is not None
    report = train(repo_index, min_feedback=args.min_feedback)
    print(json.dumps({k: v for k, v in report.items() if k != "features"}, indent=2))
    return 0


def _eval(args, parser) -> int:
    if args.learning:
        from voxcode.evaluation import learning_eval

        learning_eval.main(["--repo", args.repo])
    elif args.answers:
        from voxcode.evaluation import answer_eval

        answer_eval.main(["--repo", args.repo])
    else:
        from voxcode.evaluation import retrieval_eval

        extra = ["--embedding-models", args.embedding_models] if args.embedding_models else []
        retrieval_eval.main(["--repo", args.repo, *extra])
    return 0


def _hook(args, parser) -> int:
    from voxcode.review import hooks

    if args.action == "claude-plan":
        return hooks.claude_plan_hook()
    if args.action == "install":
        print(f"Added the plan-review hook to {hooks.install_claude(args.project)}")
        print("Claude Code plans now open in VoxCode for review. Restart Claude Code to load it.")
    else:
        removed = hooks.uninstall_claude(args.project)
        print("Removed the VoxCode hook." if removed else "No VoxCode hook was installed.")
    return 0


def _review(args, parser) -> int:
    from voxcode.review.client import review_text
    from voxcode.review.feedback import title_from

    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    is_diff = args.diff or args.file.endswith((".diff", ".patch"))
    name = "stdin" if args.file == "-" else Path(args.file).name
    decision = review_text(
        text,
        "diff" if is_diff else "document",
        name if is_diff else title_from(text, name),
        "cli",
        {"file": args.file},
    )
    approved = decision.get("decision") == "approved"
    print("APPROVED" if approved else decision.get("feedback", ""))
    return 0 if approved else 1


def _reviews(args, parser) -> int:
    from voxcode.review.store import review_store

    items = review_store.list(50)
    for r in items:
        print(f"{r['id']}  {r['status']:<9} {r['kind']:<8} {r['source']:<12} {r['title']}")
    if not items:
        print("No reviews yet.")
    return 0


def _mcp(args, parser) -> int:
    try:
        from voxcode.integrations.mcp_server import run
    except ImportError:
        parser.exit(1, "voxcode: the MCP server needs the 'mcp' extra: pip install 'voxcode[mcp]'\n")
    run(args.repo, args.transport, args.host, args.port)
    return 0


def _swebench(args, parser) -> int:
    from voxcode.evaluation import swebench

    argv = [args.instances, "--output", args.output]
    if args.limit:
        argv += ["--limit", str(args.limit)]
    if args.workdir:
        argv += ["--workdir", args.workdir]
    if args.resolve:
        argv.append("--resolve")
    swebench.main(argv)
    return 0


COMMANDS = {
    "serve": lambda args, parser: _serve(args),
    "index": _index,
    "search": _query_command,
    "ask": _query_command,
    "edit": _query_command,
    "train-ranker": _train_ranker,
    "eval": _eval,
    "hook": _hook,
    "review": _review,
    "reviews": _reviews,
    "mcp": _mcp,
    "swebench": _swebench,
}


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "serve":
        from voxcode.config.settings import settings

        settings.BACKGROUND_CALL_GRAPH = False  # this process exits when the command ends
    code = COMMANDS[args.command](args, parser)
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
