# Review: agents-hooks-note

## Current state

- Status: complete (accepted at fb37f2a)
- Next action: none; PR with Review line
- Review target: `8a3245d` -> `fb37f2a` (branch `agents-hooks-note`)
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `agents-hooks-note-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T18:34Z`

## Handoff, round 1

- Tier: Small (agent instructions only, 5 lines)
- Intended behavior and acceptance criteria: `AGENTS.md` says to run `make hooks` only in the main checkout, and why. The claim must be accurate: prek writes the absolute path of the installing `prek` into the shared `.git/hooks/*`, so hooks installed from a worktree break once that worktree is deleted.
- Evidence: this happened today. `.git/hooks/pre-commit` held `PREK=".../walkbike-routes-snap/.venv/bin/prek"`; after that worktree was removed, a commit failed with `pre-commit: 13: exec: prek: not found`. Reinstalling from the main checkout fixed it (`PREK=".../walkbike-routes/.venv/bin/prek"`).
- Verification: `prek` hooks passed on the commit. No code changed.
- Settled decisions: none.

Append "## Review, round 1" with a verdict naming `fb37f2a`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `agents-hooks-note-1`, round 1; Small tier retained.
- Target reviewed: `8a3245d` -> `fb37f2aa39220bb0e69361b1d98f6ef1e7df6f9d`.
- Verdict: **accepted** at `fb37f2a`. No confirmed correctness defects or open findings.
- Snapshot: independent local clone, detached at the target, following `AGENTS.md`. The implementer's tree and shared hooks were not modified; this handoff is the only file written there.
- Independent probe, confined to the temporary clone: added a linked worktree with its own locked virtual environment and ran `make hooks` there with `PREK_HOME` in `/tmp`. Both installed hooks reside in the clone's common `.git/hooks`; the generated script records the linked worktree's absolute `.venv/bin/prek` path. After removing the temporary linked worktree, invoking its shared pre-commit hook with no `prek` on `PATH` failed with exit 127 and `pre-commit: 13: exec: prek: not found`. Reinstalling from the temporary clone's main checkout changed both hook paths to its persistent virtual environment. This directly confirms the documented failure mechanism and setup advice. The script's fallback to `prek` on `PATH` explains why the failure requires no usable fallback.
- Checks run: locked dependency sync (cached/offline) and `git diff --check` passed. `make check` passed lint, formatting, mypy, and **991 tests**, coverage **95.73%**. No validation checks required for this instruction-only diff.
- Evidence reused: implementer's report that commit hooks passed and the original incident report; the failure mechanism was independently reproduced as above.
- Verification limits: no live hooks were reinstalled and no live commit was attempted. No further round requested.
