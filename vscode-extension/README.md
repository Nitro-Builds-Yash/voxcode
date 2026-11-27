# VoxCode for VS Code

Talk to your codebase. Ask questions out loud (or type them) and get answers grounded in your code, or describe a change and review a tested patch before anything is written.

Requires a running VoxCode server (`voxcode serve`): see the [main README](https://github.com/Nitro-Builds-Yash/voxcode#readme).

## Talk to it

| Action | How |
|---|---|
| Talk | <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>V</kbd> (press again to stop), or click **Vox** in the status bar |
| Hands-free | <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>H</kbd>: VoxCode listens continuously (optional wake word); start talking to interrupt a spoken reply |
| Type instead | <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd> |
| Chat | `@vox` in the Chat view. `@vox /edit` writes, tests and reviews a patch; `@vox /agent` lets the model search and read files first. 👍 / 👎 teach VoxCode's search which code was right. |

## Review changes

A patch opens as a side-by-side diff. Say or click:

| Say | Command |
|---|---|
| **accept** | *VoxCode: Accept Pending Patch* (applied as set by `applyTarget`) |
| **reject** | *VoxCode: Reject Pending Patch* |
| **next file** | *VoxCode: Show Next File in Patch* |
| **undo** | *VoxCode: Undo Last Applied Patch* |
| **run the tests** | *VoxCode: Run Tests* |

You can also accept with *Accept Patch on a New Branch* or *Accept Patch as Pull Request*.

To annotate a plan before any code is written, use *VoxCode: Plan a Change and Review It in the Browser*: comment on, delete or replace lines of the plan, approve it, then review the patch. Past reviews are under *VoxCode: Open Review Archive*.

## Settings

| Setting | Default | |
|---|---|---|
| `voiceCoding.backendUrl` | `http://localhost:8000` | URL of the VoxCode server |
| `voiceCoding.apiToken` | – | Value of `API_TOKEN` if the server requires one |
| `voiceCoding.speakReplies` | off | Read answers aloud, sentence by sentence as they stream in (needs a TTS engine on the server; you're told once if there isn't one) |
| `voiceCoding.reviewPlans` | off | Show the plan for a change and wait for approval before the patch is written |
| `voiceCoding.runTestsOnEdit` | off | Run the tests against each proposed patch (in a temporary copy) before showing it |
| `voiceCoding.applyTarget` | `working_tree` | What **accept** does: `working_tree`, `branch` or `pull_request` |
| `voiceCoding.autoApplyDiffs` | off | Apply patches that passed validation and review without asking |
| `voiceCoding.watchWorkspace` | on | Re-index automatically when files change |
| `voiceCoding.showTimings` | on | Show per-stage latency and token usage after each reply |

Other commands: *Re-index Workspace*, *Rebuild Index From Scratch*, *Review Pending Patch*, *Open Settings*.
