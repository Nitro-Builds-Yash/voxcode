"""SWE-bench style evaluation.

For each instance (a real GitHub issue + the commit it was filed against):

1. **Localization**: index the repository at `base_commit`, search with the
   issue text, and check whether the files changed by the real fix come up
   (file recall@1/5/10). Needs no LLM.
2. **Resolve** (`--resolve`): run VoxCode's edit loop on the issue and write
   predictions in the official format, so the resolve rate can be measured
   with the SWE-bench harness (which runs each repo's tests in its own Docker
   image):

       python -m swebench.harness.run_evaluation \\
           --dataset_name princeton-nlp/SWE-bench_Lite \\
           --predictions_path swebench_predictions.jsonl --run_id voxcode

Instances are read from JSONL (fields: instance_id, repo, base_commit,
problem_statement, patch). Export SWE-bench Lite with:

    python -c "from datasets import load_dataset; load_dataset('princeton-nlp/SWE-bench_Lite', split='test').to_json('lite.jsonl')"
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import time
from pathlib import Path

from voxcode.execution.diff.diff_utils import touched_paths

DEFAULT_WORKDIR = Path.home() / ".voxcode" / "swebench"
K_VALUES = (1, 5, 10)


def load_instances(path: Path, limit: int = 0) -> list[dict]:
    instances = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return instances[:limit] if limit else instances


def gold_files(instance: dict) -> list[str]:
    return [
        p for p in touched_paths(instance["patch"]) if not p.startswith(("tests/", "test/"))
    ] or touched_paths(instance["patch"])


def checkout(instance: dict, workdir: Path) -> Path:
    """Shared clone per repository + a worktree per instance at base_commit."""
    repo_name = instance["repo"].replace("/", "__")
    mirror = workdir / "repos" / repo_name
    url = instance.get("repo_url") or f"https://github.com/{instance['repo']}.git"
    if not (mirror / ".git").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", url, str(mirror)], check=True)
    target = workdir / "instances" / instance["instance_id"]
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(mirror),
                "worktree",
                "add",
                "--detach",
                "--force",
                str(target),
                instance["base_commit"],
            ],
            check=True,
            capture_output=True,
        )
    return target


def localize(repo: Path, query: str, max_k: int = 10) -> list[str]:
    from voxcode.agent.runtime import runtime_cache
    from voxcode.api import services

    key = services.ensure_indexed(repo)
    retriever = runtime_cache.retriever(key)
    ranked: list[str] = []
    for result in retriever.expand(retriever.search(query[:4000], 50), 4):
        if result.file_path not in ranked:
            ranked.append(result.file_path)
        if len(ranked) >= max_k:
            break
    return ranked


def file_recall(ranked: list[str], gold: list[str]) -> dict[str, float]:
    gold_set = set(gold)
    scores = {f"recall@{k}": float(bool(gold_set & set(ranked[:k]))) for k in K_VALUES}
    scores["all_gold@10"] = float(gold_set <= set(ranked[:10]))
    return scores


def resolve(repo: Path, instance: dict) -> dict:
    from voxcode.agent.agent_request import AgentRequest
    from voxcode.api import services

    key = services.ensure_indexed(repo)
    query = f"Fix this GitHub issue.\n\n{instance['problem_statement'][:6000]}"
    result = services.edit_agent().run(
        AgentRequest(session_id=f"swe-{instance['instance_id']}", repository_key=key, query=query), repo
    )
    return {"status": result.status, "model_patch": result.diff or "", "attempts": len(result.attempts)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("instances", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--resolve", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("swebench_results.jsonl"))
    parser.add_argument("--predictions", type=Path, default=Path("swebench_predictions.jsonl"))
    parser.add_argument("--model-name", default="voxcode")
    args = parser.parse_args(argv)

    instances = load_instances(args.instances, args.limit)
    totals: dict[str, float] = {}
    rows = []
    with contextlib.ExitStack() as stack:
        out = stack.enter_context(args.output.open("w"))
        preds = stack.enter_context(args.predictions.open("w")) if args.resolve else None
        for number, instance in enumerate(instances, start=1):
            started = time.perf_counter()
            try:
                repo = checkout(instance, args.workdir)
                gold = gold_files(instance)
                ranked = localize(repo, instance["problem_statement"])
                row = {
                    "instance_id": instance["instance_id"],
                    "gold_files": gold,
                    "ranked_files": ranked,
                    **file_recall(ranked, gold),
                }
                if args.resolve:
                    outcome = resolve(repo, instance)
                    row.update(
                        {"edit_status": outcome["status"], "patch_generated": bool(outcome["model_patch"])}
                    )
                    assert preds is not None
                    preds.write(
                        json.dumps(
                            {
                                "instance_id": instance["instance_id"],
                                "model_name_or_path": args.model_name,
                                "model_patch": outcome["model_patch"],
                            }
                        )
                        + "\n"
                    )
            except Exception as exc:  # keep going; count as a miss
                row = {
                    "instance_id": instance["instance_id"],
                    "error": str(exc),
                    **{f"recall@{k}": 0.0 for k in K_VALUES},
                    "all_gold@10": 0.0,
                }
            row["seconds"] = round(time.perf_counter() - started, 1)
            rows.append(row)
            out.write(json.dumps(row) + "\n")
            for key in [*(f"recall@{k}" for k in K_VALUES), "all_gold@10"]:
                totals[key] = totals.get(key, 0.0) + row.get(key, 0.0)
            print(
                f"[{number}/{len(instances)}] {instance['instance_id']}: recall@5={row.get('recall@5')} ({row['seconds']}s)"
            )

    n = max(len(rows), 1)
    print(f"\nFile localization over {len(rows)} instances:")
    for key, value in totals.items():
        print(f"  {key:<12} {value / n:.3f}")
    if args.resolve:
        generated = sum(1 for r in rows if r.get("patch_generated"))
        print(
            f"  patches generated: {generated}/{len(rows)} -> evaluate {args.predictions} with the SWE-bench harness"
        )


if __name__ == "__main__":
    main()
