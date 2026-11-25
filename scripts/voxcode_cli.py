"""Command-line access to VoxCode without the server.

python scripts/voxcode_cli.py index  PATH [--full]
python scripts/voxcode_cli.py search PATH "query"
python scripts/voxcode_cli.py ask    PATH "question"      (streams the answer)
python scripts/voxcode_cli.py edit   PATH "instruction" [--tests]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agent.agent_request import AgentRequest  # noqa: E402
from backend.api import services  # noqa: E402
from backend.core.repository import resolve_repository_path  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["index", "search", "ask", "edit"])
    parser.add_argument("path")
    parser.add_argument("text", nargs="?", default="")
    parser.add_argument("--full", action="store_true", help="rebuild the index from scratch")
    parser.add_argument("--tests", action="store_true", help="run tests against proposed patches")
    args = parser.parse_args()

    repo = resolve_repository_path(args.path)
    if args.command == "index":
        print(services.index_repository(repo, full=args.full).to_dict())
        return
    if not args.text:
        parser.error(f"{args.command} needs a query")

    key = services.ensure_indexed(repo)
    request = AgentRequest(session_id="cli", repository_key=key, query=args.text)

    if args.command == "search":
        retriever = services.runtime_cache.retriever(key)
        for result in retriever.expand(retriever.search(args.text, 8)):
            print(f"[{result.source:8}] {result.file_path}:{result.start_line}  {result.symbol_name}")
    elif args.command == "ask":
        for event in services.pipeline().stream(request):
            if event["type"] == "token":
                print(event["text"], end="", flush=True)
        print()
    else:
        result = services.edit_agent().run(request, repo, run_tests=args.tests)
        print(f"[{result.status}] {result.summary}\n")
        print(result.diff or "(no patch)")


if __name__ == "__main__":
    main()
