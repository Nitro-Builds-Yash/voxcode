import json
import subprocess

from tests.conftest import ScriptedProvider
from voxcode.evaluation import answer_eval, swebench


def test_swebench_localization_on_a_local_repo(isolated_storage, sample_repo, tmp_path, monkeypatch):
    from voxcode.api import services

    monkeypatch.setattr(services.registry, "get_embedder", lambda: None)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sample_repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=sample_repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@x", "commit", "-qm", "init"],
        cwd=sample_repo,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sample_repo, capture_output=True, text=True
    ).stdout.strip()

    instance = {
        "instance_id": "demo__auth-1",
        "repo": "demo/auth",
        "repo_url": str(sample_repo),
        "base_commit": base,
        "problem_statement": "verify_password compares the password hash incorrectly",
        "patch": "--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1 @@\n-x\n+y\n",
    }
    instances = tmp_path / "instances.jsonl"
    instances.write_text(json.dumps(instance) + "\n")
    output = tmp_path / "results.jsonl"
    swebench.main([str(instances), "--workdir", str(tmp_path / "work"), "--output", str(output)])
    row = json.loads(output.read_text().splitlines()[0])
    assert row["gold_files"] == ["app/auth.py"] and row["recall@1"] == 1.0
    assert swebench.file_recall(["x.py", "a.py"], ["a.py"]) == {
        "recall@1": 0.0,
        "recall@5": 1.0,
        "recall@10": 1.0,
        "all_gold@10": 1.0,
    }


def test_answer_judge_parsing():
    provider = ScriptedProvider(['Verdict: {"score": 4, "reason": "minor gap"}'])
    assert answer_eval.judge(provider, "q", "ref", "ans") == (4.0, "minor gap")
    assert answer_eval.judge(ScriptedProvider(["no json"]), "q", "r", "a")[0] == 0.0
