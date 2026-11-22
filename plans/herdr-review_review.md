# Review: herdr-review

## Current state

- Status: complete (accepted at b52761d)
- Next action: none; PR with Review line
- Review target: `c31cb7d` -> `b52761d` (branch `herdr-review`)
- Open findings: none (HR1 fixed and verified)
- Decisions needed from the user: none

## Loop

- Loop ID and round: `herdr-review-1`, round 2 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T17:32Z`

## Handoff, round 1

- Tier: Standard (agent instructions are shared policy)
- Intended behavior and acceptance criteria:
  - `AGENTS.md` replaces the one-shot `codex exec` review with the Herdr protocol between `walkbike-implementer` and `walkbike-reviewer` in this workspace.
  - The reviewer reads committed files only, via a `git archive` snapshot; never `data/`, `output/`, `.env`, `*.local.yaml`, or count and recorded-trip records. The repository is public.
  - The commands in the new section work as written (snapshot command, `herdr agent prompt`, rename).
  - Several Claude panes can share the one reviewer: unique implementer names, the reviewer replies to the implementer named in the handoff, one review at a time.
  - No rule from the old section is lost without reason (format before commit, `make check`, `make validation-check`, exempt paths, test each finding before acting).
- Changed areas: `AGENTS.md` (review section, gotchas), new `plans/review-handoff.md`.
- Verification: `uv run --locked prek run --all-files` | 9913e74, cdf6904 | all passed | none | implementer. No code changed, so `make check` was not rerun.
- Settled decisions (do not re-report): review once per PR-sized slice after the local commit (the shared default), not per staged change; records in `plans/` are committed.
- Known limitations: whether `git archive | tar` works under your sandbox is untested; please say if it does not.

To review: `git diff c31cb7d cdf6904` from the `herdr-review` worktree (read-only), or snapshot as the new section says. Append "## Review, round 1" to this file with a verdict naming `cdf6904`, then send one prompt to `walkbike-implementer`.

## Review, round 1

- Loop: `herdr-review-1`, round 1.
- Target reviewed: `c31cb7d26d027f77afacff019b017c90e14a2139` -> `cdf69040bc416c79eb1153eb237480bde848659b`.
- Verdict: **changes required** at `cdf6904`.
- Checks: the documented `git archive | tar` snapshot command succeeded under the sandbox. `git diff --check` passed. Installed Herdr help confirms the documented `agent prompt <TARGET> <TEXT>` and `agent rename <TARGET> <NAME>` syntax; identity was not mutated. `HERDR_ENV=1` and the live roster identify both named peers in workspace `wG`, with the implementer at `wG:p1`.
- Snapshot verification: `uv sync --locked` succeeded with network access after sandbox DNS failed. `make check`: lint passed, formatting passed (134 files), mypy passed (131 files); pytest had **992 passed, 1 failed**, coverage 95.74%. The sole failure is detailed below. The same focused probe also failed on an archived base.
- Evidence reused: implementer's all-files prek result. No package code changed. No validation checks required by this diff.
- Verification gaps: no rename or second review request was sent as a probe; CLI syntax was checked read-only. No runtime concurrency exercise was performed.

### HR1 — P2, blocking: the prescribed archive cannot pass the required checks

- Location: `AGENTS.md:107-110`.
- Trigger: follow the new reviewer instructions literally: extract `git archive cdf6904` into a fresh folder outside the repository, sync dependencies, then run `make check` there.
- Violated acceptance criterion: the commands in the new review section must work as written. The new procedure requires `make check` in a snapshot that lacks the Git metadata required by an existing test.
- Confirmed evidence: `tests/test_core_outputs.py::test_git_provenance_is_read_only` calls `git_provenance(Path(__file__).parents[1])` and requires a string `git_commit`. `git archive` omits `.git`, so `git rev-parse HEAD` fails and the value is `None`. The head's full `make check` fails at this assertion. A separate archive of `c31cb7d` with the same focused pytest probe fails identically. This is a defect in the newly prescribed review workflow, not a claim that package behavior regressed.
- Requested fix: make the documented isolated review setup compatible with the Git-dependent check (for example, provide isolated snapshot Git metadata without connecting it to the implementer's working tree), or revise the check/procedure so a clean archive can complete the required verification. Re-run the documented procedure to confirm.
- Status: open. No other confirmed blocking findings.

## Handoff, round 2

- Target: `cdf6904` -> `b52761d` (one commit, `AGENTS.md` only).
- HR1 fix: the reviewer snapshot is now a local clone checked out at the target (`git clone --quiet --no-hardlinks --no-checkout <repo> "$D"`, then `git -C "$D" checkout --quiet --detach <sha>`), which has its own `.git` and only committed files.
- Verification (implementer):
  - Fail-first: your round-1 evidence, `git archive` snapshot of `cdf6904` fails `test_git_provenance_is_read_only`.
  - Clone snapshot of `cdf6904`, documented commands: `make check` passed, 993 passed, coverage 95.75%; no `data/`, `output/` or `.env` in the snapshot.
  - Clone snapshot of `b52761d`, documented commands: `tests/test_core_outputs.py` 2 passed.
  - `prek run` on the commit: passed.

## Finding ledger

| ID | Severity | Blocking | Location | Status | Evidence, fix commit, test |
| --- | --- | --- | --- | --- | --- |
| HR1 | P2 | yes | `AGENTS.md` review snapshot | fixed | b52761d; clone snapshot passes `make check` |

## Review, round 2

- Loop: `herdr-review-1`, round 2.
- Target reviewed: `cdf69040bc416c79eb1153eb237480bde848659b` -> `b52761debb2b4da9f34e434523116fe1cf20be23`.
- Verdict: **accepted** at `b52761d`. HR1 is verified fixed; no open findings or new confirmed defects.
- Reproduced the revised local clone and detached-checkout commands in `/tmp/walkbike-review/herdr-review-r2`. The clone reports the exact target SHA, has independent Git metadata with no object alternates, and excludes `data/`, `output/`, `.env` and the uncommitted handoff. The implementer's tree was not changed.
- Reviewer verification: `git diff --check` passed. Locked dependency sync passed using the cached packages (`--offline`). `make check` in the clone passed lint, format checks (134 files), and mypy (131 files). Its pytest run had 990 passed and three failures confined to the Chromium tests in `tests/test_map_page.py`; coverage was 95.75%. Re-running that entire file outside the restricted sandbox passed all three tests (3 passed in 6.74s), confirming the sandbox-dependent failures. The previously failing Git provenance test passed in the full run. All 993 tests therefore passed across the full run and focused rerun; a single unrestricted full `make check` was not repeated.
- Evidence reused: implementer's documented clone setup at `cdf6904` passed full `make check` with 993 tests; the target's focused provenance checks and commit hooks passed. The only target changes are the snapshot instructions in `AGENTS.md`.
- HR1 resolution: replacing the archive with a local clone gives the existing provenance test the Git metadata it needs without using or mutating the implementer's checkout metadata. The archive failure from round 1 and the successful clone probe confirm the fix.
- Verification gaps: peer rename and concurrent review delivery remain unexercised, as in round 1; the HR1 fix does not change them. No further review round is requested.
