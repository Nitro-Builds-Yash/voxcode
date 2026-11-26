# Reviewing plans and patches

VoxCode opens plans and patches in a local review page (`http://127.0.0.1:8000/review/<id>`)
before anything changes your code. The flow is inspired by
[Plannotator](https://github.com/backnotprop/plannotator).

## Annotate

| In a plan or document | In a patch |
|---|---|
| Select text → **Comment**, **Delete** or **Replace** | Click a line number → comment on that line |
| Overall feedback box | Overall feedback box |
| **v1 … vN** tabs and **Show changes** after a revision | File tree with added/removed counts |

Keyboard: <kbd>Ctrl</kbd>+<kbd>Enter</kbd> approves, <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Enter</kbd> denies, and holding <kbd>V</kbd> records voice.

**Voice.** Hold <kbd>V</kbd> (or click 🎙) and talk; the speech is transcribed by VoxCode's local Whisper.
With text selected, what you say becomes a comment on it; otherwise it's added to the overall feedback.
Saying *"approve"* / *"looks good"* approves; *"deny, use Redis instead"* denies with that note.

When you deny, the agent receives numbered, quoted feedback:

```markdown
# Review of the plan: changes requested

1. Replace "Store counters in a global dict." with "Store counters in Redis so limits hold across workers.". A global dict breaks with several uvicorn workers.
2. On "token bucket": Make the limit configurable per route in settings.

## Overall
Also exempt /health and /metrics.

Revise the plan to address every point above, then present it again.
```

## Claude Code

```bash
voxcode hook install claude            # ~/.claude/settings.json (add --project for ./.claude/settings.json)
voxcode hook uninstall claude
```

This adds a `PermissionRequest` hook with the matcher `ExitPlanMode`. When Claude finishes a plan, the hook does the following:

1. It uses the running VoxCode server, or starts one on `REVIEW_PORT` (19432).
2. It opens the plan in your browser. Over SSH it prints the URL instead, so forward the port.
3. It waits for your decision:
   - **Approve:** Claude leaves plan mode and starts implementing.
   - **Deny:** your feedback becomes the denial message, and Claude revises.

Claude's next plan in the same session becomes **v2 of the same review**, with the changes highlighted.

## VoxCode's own edits

`POST /reviews/plan` (or **VoxCode: Plan a Change and Review It in the Browser** in VS Code) runs the full loop:

```
plan ─ deny ─> re-plan with your feedback ─> review again
  └─ approve ─> write + test the patch ─> code review ─ deny ─> regenerate with your notes
                                                   └─ approve ─> apply (and teach the learned ranker)
```

## Anything else

```bash
voxcode review PLAN.md                 # any markdown: prints APPROVED, or the feedback, and exits 0 / 1
git diff | voxcode review - --diff     # review a diff from stdin
voxcode reviews                        # list past reviews; the archive page is /review
```

## Sharing

**Share** copies a link like `/review/share#v1.<data>`. The content and annotations are compressed into the
link fragment, which browsers never send to a server. Anyone with the link can read it: it is compressed,
**not encrypted**.

## Obsidian

Set `OBSIDIAN_VAULT=/path/to/vault` and every approved plan is saved to `VoxCode/Plans/<date>-<title>.md`
(change the folder with `OBSIDIAN_FOLDER`), with YAML frontmatter (title, date, source, revisions, project, tags).

## API

| Endpoint | |
|---|---|
| `POST /reviews` | `{kind: plan\|diff\|document, content, title?, source?, meta?}` → `{id, url, revision, revised}`. When `meta.claude_session` matches an open review, the content becomes a new revision of it. |
| `GET /reviews`, `GET /reviews/{id}` | Archive / one review (with `changes` against the previous revision) |
| `POST /reviews/{id}/annotations`, `DELETE …/{annotation_id}` | `{kind: comment\|delete\|replace\|insert\|line, quote, comment, replacement, file, line}` |
| `POST /reviews/{id}/decision` | `{decision: approve\|deny, comment}` → `{decision, feedback}` |
| `POST /reviews/{id}/revisions` | `{content}`: a new version, back to pending |
| `GET /reviews/{id}/wait?revision=&timeout=` | Long-poll for a decision (used by the hook) |
| `POST /reviews/plan` | `{query, repository_path, run_tests}`: start VoxCode's plan → patch review loop |

The pages (`/review…`) are public shells like `/`; the `/reviews` API requires `API_TOKEN` when one is set.
