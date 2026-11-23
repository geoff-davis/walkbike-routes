# Review: parallel-jobs

## Current state

- Status: complete
- Next action: open the PR
- Review target: `6e4438e` -> `767a543`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `parallel-jobs-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-23T19:29Z`

## Handoff, round 1

- Tier: Standard (new execution path for every run).
- Intended behavior and acceptance criteria:
  - The user asked to speed up a multi-destination study (11 schools, about
    4 hours on one core of 32).
  - `walkbike-routes run --jobs N` (default 1; below 1 exits with
    "--jobs: expected 1 or more"). `build(..., workers=N)` and
    `study.analyze(..., workers=N)`; the name `jobs` was already taken in
    `build` by the employment layer.
  - For each mode and preset, the first destination runs in the parent and
    builds the network; the remaining destinations run in a
    `multiprocessing` fork pool of `min(N, remaining)` processes that
    inherit the network through a module global (`_SHARED`), set before the
    pool starts and cleared after. `pool.map` keeps the study's order.
    Workers return `(files, rows, flow)`; files are written by the workers
    into each destination's own folder.
  - Outputs equal a run with one process.
- Changed areas: `src/walkbike_routes/__main__.py`, `src/walkbike_routes/study.py`,
  `README.md`, `tests/test_parallel_runs.py`.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `uv run pytest tests/test_parallel_runs.py` | `767a543` | 2 passed | none | implementer |
  | `make check` | `d6038cd` | 1027 passed, 95.74% | none | implementer |
  | `make validation-check` | `d6038cd` | 58 passed | none | implementer |

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Every parquet file and summary.csv equal with 1 and 2 workers | `test_parallel_workers_write_the_same_outputs` | caught | demo: school in the parent, downtown in a worker; includes a shorter limit and all-destinations lists |
  | `--jobs 0` refused | `test_jobs_below_one_is_refused` | caught | |
  | More workers than remaining destinations | not exercised separately | not exercised | pool size is capped |

- Settled decisions (do not re-report): fork (not spawn or forkserver),
  so the network is shared without pickling; parallel across destinations
  only, not inside screens.
- Known limitations:
  - Python 3.15 warns that the process is multi-threaded when it forks
    (library thread pools); a deadlock in a child is possible in principle.
    The tests and the demo do not hang. Please judge whether this needs a
    guard (for example limiting library threads before forking).
  - Linux and macOS only in practice (fork); Windows would fail at the pool.
  - Memory grows per worker; the README says so.

## Review, round 1

- Loop: `parallel-jobs-1`, round 1; Standard tier.
- Target reviewed: `6e4438e..767a543` (`767a543e036cbf2b714776f0e1a5b655c731fb15`).
- Verdict: **accepted** at `767a543`. No confirmed blocking findings.
- Reviewed the complete four-file diff in an independent detached clone with its own Git metadata. The parent builds the first destination's network, children inherit it through `_SHARED`, ordered pool results feed the existing aggregation, and the shared global clears in `finally`. The default remains serial; the CLI rejects jobs below one.
- The committed equivalence and invalid-jobs tests pass. An additional independent synthetic probe extends the demo to four unique destinations with two walking presets, two cycling presets, driving and a shorter limit. With `workers=8`, PID markers confirm the first destination runs in the parent and the other three use three distinct child processes (pool size capped to the remaining destinations). All 164 Parquet files and every CSV, including shorter summaries, match `workers=1`. Both runs finish. A separate injected worker exception propagates to the parent and leaves `_SHARED` cleared.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1021 tests, four deselected, 95.07% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- Fork assessment: the full suite reproduces the three Python 3.15 multithreaded-fork deprecation warnings described in the handoff. Neither the committed test nor the genuinely concurrent extended demo deadlocks. The warning alone does not supply a confirmed failing case for a blocking finding or justify overriding the settled fork choice. These checks do not establish safety at the real study's scale, on macOS, or under every native-library thread state; the documented fork/deadlock limitation remains. No additional thread guard is required by this verdict on the available evidence.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. Additional execution used only made-up demo inputs outside the repository; no real-study performance claim is made.
