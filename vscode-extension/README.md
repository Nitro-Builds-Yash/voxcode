# VoxCode for VS Code

Talk to your codebase. Ask questions out loud (or type them) and get answers grounded in your code, or describe a change and review a validated patch in a side-by-side diff before it is applied.

Requires a running VoxCode server: see the [main README](https://github.com/Nitro-Builds-Yash/voxcode#readme).

| Action | How |
|---|---|
| Talk | **Ctrl+Alt+V** (press again to stop) or click **Vox** in the status bar |
| Type instead | **Ctrl+Alt+T** |
| Review a patch | Say or click **accept**, **reject**, **next file** |
| Revert | Say **undo** or run *VoxCode: Undo Last Applied Patch* |
| Tests | Say **run the tests** |

Settings: `voiceCoding.backendUrl`, `apiToken`, `autoApplyDiffs`, `runTestsOnEdit`, `speakReplies`, `watchWorkspace`.
