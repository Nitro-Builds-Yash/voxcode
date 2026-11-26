"""Agent integrations: Claude Code plan mode.

`voxcode hook install claude` adds a PermissionRequest hook on ExitPlanMode
to ~/.claude/settings.json (or ./.claude/settings.json with --project). When
Claude finishes a plan, the hook opens it in the VoxCode review page and
blocks until you approve (Claude proceeds) or deny (your annotations go back
to Claude as structured feedback; its next plan shows as a revision).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

HOOK_COMMAND = "voxcode hook claude-plan"
HOOK_TIMEOUT = 345_600  # 4 days: reviews wait for a human


def claude_settings_path(project: bool = False) -> Path:
    return (Path.cwd() if project else Path.home()) / ".claude" / "settings.json"


def install_claude(project: bool = False, command: str = HOOK_COMMAND) -> Path:
    path = claude_settings_path(project)
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text() or "{}")
        shutil.copy2(path, path.with_suffix(f".json.bak-{int(time.time())}"))
    hooks = data.setdefault("hooks", {}).setdefault("PermissionRequest", [])
    hooks[:] = [
        h
        for h in hooks
        if not any(c.get("command", "").startswith("voxcode hook") for c in h.get("hooks", []))
    ]
    hooks.append(
        {
            "matcher": "ExitPlanMode",
            "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT}],
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def uninstall_claude(project: bool = False) -> bool:
    path = claude_settings_path(project)
    if not path.exists():
        return False
    data = json.loads(path.read_text() or "{}")
    hooks = data.get("hooks", {}).get("PermissionRequest", [])
    kept = [
        h
        for h in hooks
        if not any(c.get("command", "").startswith("voxcode hook") for c in h.get("hooks", []))
    ]
    if len(kept) == len(hooks):
        return False
    data["hooks"]["PermissionRequest"] = kept
    if not kept:
        del data["hooks"]["PermissionRequest"]
    path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def permission_output(behavior: str, message: str = "") -> dict:
    decision: dict = {"behavior": behavior}
    if message:
        decision["message"] = message
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


def claude_plan_hook(stdin=None, stdout=None, review=None) -> int:
    """Read the hook payload, review the plan, print Claude Code's decision JSON."""
    from voxcode.review.client import review_text
    from voxcode.review.feedback import title_from

    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    payload = json.loads(stdin.read() or "{}")
    plan = (payload.get("tool_input") or {}).get("plan", "")
    if not plan.strip():
        return 0  # nothing to review: let Claude Code ask as usual
    meta = {
        "claude_session": payload.get("session_id", ""),
        "cwd": payload.get("cwd", ""),
        "repository_path": payload.get("cwd", ""),
    }
    decision = (review or review_text)(
        plan, "plan", title_from(plan, "Claude Code plan"), "claude-code", meta
    )
    if decision.get("decision") == "approved":
        out = permission_output("allow")
    else:
        out = permission_output(
            "deny", decision.get("feedback") or "The reviewer denied the plan. Revise it."
        )
    stdout.write(json.dumps(out))
    stdout.flush()
    return 0
