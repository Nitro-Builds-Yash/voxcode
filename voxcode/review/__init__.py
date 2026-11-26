"""Human review of plans and patches, in the browser or by voice.

Inspired by Plannotator: an agent's plan (VoxCode's own, or Claude Code's
via a plan-mode hook) or a patch opens in a local review page. The reviewer
annotates it (comment, delete, replace, line comments on diffs), then
approves or denies; a denial goes back to the agent as structured feedback
and the revision shows up as a diff against the previous version.
"""
