# Review: largest-first

## Current state

- Status: complete, accepted at `1fa29c3`
- Next action: none
- Review target: `d30f928` -> `1fa29c3`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `largest-first-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-24T20:25Z`

## Handoff, round 1

- Tier: Standard (changes run order and adds a cache; outputs must not
  change).
- Asked for by the user: speed up the multi-destination run after a timed
  run showed workers idle at the end of each preset and a 22-minute
  single-process step after routing.
- Intended behavior and acceptance criteria:
  - `study._order(pairs)`: the destination with fewest origins builds each
    preset's network (routed alone in the main process); the rest run
    largest first; ties keep the study's order. Networks do not depend on
    the destination (`mode_network` reuses `like`'s links and graphs and
    attaches only the homes; `car_network` is the same for every
    destination), and results are stored by destination id and read back
    in the study's order, so outputs and `summary.csv` are unchanged.
  - `exposure_crossings._tables(edges, nodes)`: `_Network`, `Places`, the
    edge records, the crossing-way set and crossing groups are built once
    and reused while the same edges and nodes objects, of the same shape
    (lengths and columns), are passed. `crossing_events` and
    `exposed_crossings` both read them (each used to build its own). The
    cached objects are only read afterwards (`groups` is now a plain dict,
    read with `.get(..., [])`).
- Changed areas: `src/walkbike_routes/study.py`,
  `src/walkbike_routes/analysis/exposure_crossings.py`,
  `tests/test_parallel_runs.py`, `tests/test_exposure.py`.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `make check` | `1fa29c3` | 1044 passed, 95.77% | none | implementer |
  | `make validation-check` | `1fa29c3` | 58 passed | none | implementer |

  On a private real-area study (not readable by the reviewer): one
  destination's walking exposure lists were identical with and without the
  cache (`assert_frame_equal` on both tables); one call took 15 s on the
  base and 3.3 s once the tables were built. The run order change has not
  been timed on a whole run yet.

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Smallest first, then largest first, ties in study order, empty | `test_each_preset_builds_on_the_smallest_destination_then_routes_the_largest_first` | caught | |
  | Parallel and sequential outputs still equal | `test_parallel_workers_write_the_same_outputs` (existing) | caught | demo destinations tie, so order is unchanged there |
  | Cache reused for the same tables, rebuilt for a copy or a new column | `test_the_network_tables_are_built_once_for_the_same_edges_and_nodes` | caught | |
  | Lists unchanged with the cache | all existing exposure and study tests; real-area probe | caught | |

- Settled decisions (do not re-report): `street_risk`'s per-call edge
  records are left as they are (they depend on the car flow; about 3 s a
  call).
- Known limitations: the cache holds one network's tables and the edges and
  nodes they came from until the next network or the end of the process.
  An in-place change to a cell that keeps the shape is not detected; the
  study code does not change these tables after preparing them.

## Review, round 1

- Loop: `largest-first-1`, round 1; Standard tier.
- Target reviewed: `d30f928..1fa29c3` (`1fa29c3d910abbe6e0ff8d71800fa93815481026`). The handoff was read from `fe86228` as requested; implementation checks used `1fa29c3`.
- Verdict: **accepted** at `1fa29c3`. No confirmed blocking findings.
- Reviewed the complete four-file diff in an independent detached clone with its own Git metadata. The scheduling helper picks the first smallest origin set and sorts the rest descending with stable ties. Results remain keyed by destination and aggregated in the study's order. Network construction reads shared streets/nodes/signals rather than destination-specific origins; attachments are rebuilt for each destination.
- The crossing cache retains the source objects to avoid reused-identity matches, checks both objects and their lengths/column lists, and shares network-only records and geometry/road information. Route weights, signal arguments, risk settings and per-destination grouping remain outside the cached tables. The committed scheduling and cache reuse/invalidation tests pass.
- Independent end-to-end probe: three synthetic destinations have 278, 48 and 111 origins, with the 278-origin destination first in the study. For each walking, cycling and driving preset, the new serial schedule actually runs 48, 278, 111; the control restores the original 278, 48, 111 order and forces crossing-table rebuilds before each use. All 89 Parquet files and every CSV, including shorter-limit summaries and all-destination results, match. A third run with `workers=8` (capped to the remaining destinations) also matches every table; the parent builds on the 48-origin destination. These are controls in the target snapshot, not a real-area timing comparison.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1038 tests, four deselected, 95.12% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions. Three pre-existing multithreaded-fork warnings recur.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- Limits: acceptance uses the handoff's unchanged-network scope; same-shape in-place cell edits remain undetected as declared. The private-area timing/equivalence evidence was not inspected, and no whole-real-study speedup is independently claimed.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. Additional builds used the made-up demo with temporary synthetic attendance polygons outside the repository.
