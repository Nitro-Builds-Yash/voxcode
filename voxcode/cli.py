"""`voxcode` command line.

voxcode serve                         start the API server and web UI
voxcode index  [PATH] [--full]        index a repository (default: current folder)
voxcode search "query" [--repo PATH]  show matching code
voxcode ask    "question" [--repo PATH]       streamed answer
voxcode edit   "instruction" [--repo PATH] [--tests]   propose a validated patch
voxcode eval   [--repo PATH]          retrieval quality benchmark
"""

from __future__ import annotations

import argparse
import sys


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

    evaluate = sub.add_parser("eval", help="retrieval quality benchmark")
    evaluate.add_argument("--repo", default=".")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from voxcode.main import main as serve

        serve()
        return

    if args.command == "eval":
        from voxcode.evaluation import retrieval_eval

        retrieval_eval.main(["--repo", args.repo])
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
