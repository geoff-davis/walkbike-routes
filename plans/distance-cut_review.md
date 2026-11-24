# Review: distance-cut

## Current state

- Status: complete
- Next action: open the PR
- Review target: `45238e2` -> `b908815`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `distance-cut-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-24T06:01Z`

## Handoff, round 1

- Tier: Standard (changes the graph every walking and cycling run searches;
  the outputs must not change).
- Asked for by the user: speed up runs by not routing over the parts of
  the network that homes cannot use, without changing results.
- Intended behavior and acceptance criteria:
  - `study_active._nearby` runs the best-route and near-best searches on
    the subgraph within radius `R` of the destination's entrances
    (`routing/local.py`), starting from `R = 1.5 x` the farthest home's
    street-end distance.
  - Claim: with `per_m` the least edge cost per metre of straight line
    between the edge's street nodes (`cost_per_metre`; movement-graph nodes
    map to their street node via `local.at`), every path through a removed
    node to a node `n` at distance `d` from the nearest entrance costs at
    least `per_m * max(2R - d, d)` (`Near.bound`). The bound is
    `per_m`-Lipschitz along edges, so labels below it are identical on the
    cut and whole graphs, in Dijkstra and in the multi-label near-best
    search (labels at a node below its bound are built only from labels
    below their nodes' bounds).
  - Accepted only if every home's street ends (and attached node) cost
    less than their bound, or reach no entrance at all on the whole graph
    (`reaches`), and every eligible node's `best + gap` is below its bound.
    Otherwise the radius widens to what the failed check asks for; after
    three tries, or with no finite radius, the whole graph is searched.
  - No bound (negative edge cost, missing position, zero per-metre cost)
    or nothing cut away: the whole graph, as before.
  - The run no longer computes `node_accessibility` for every node (unused
    by any caller); `AccessibilityResult` carries `tree` instead. The
    function itself is unchanged and still exported.
  - Barriers screens and fix-first lists still use the whole network
    (left for a later slice).
- Changed areas: `src/walkbike_routes/routing/local.py` (new),
  `src/walkbike_routes/study_active.py`, `src/walkbike_routes/analysis/accessibility.py`,
  `docs/routing-methods.md`, `tests/test_local_routes.py`.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `uv run pytest tests/test_local_routes.py` | `b908815` | 11 passed | none | implementer |
  | `make check` | `b908815` | 1038 passed, 95.74% | none | implementer |
  | `make validation-check` | `b908815` | 58 passed | none | implementer |

  Also, on a private real-area study (not readable by the reviewer): one
  school with an attendance area, every preset; the cut searched 9-16% of
  the network and all 76 Parquet files plus the summary rows and
  shorter-limit summaries matched an earlier whole-network run exactly.

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Certified Dijkstra costs and paths equal whole graph | `test_certified_nodes_match_the_whole_network` (6 random grids) | caught | |
  | Certified near-best alternatives equal whole graph | same test | caught | |
  | Grids include nodes better reached from outside (non-trivial) | `test_the_grids_include_nodes_better_reached_from_outside_the_circle` | caught | |
  | Failed check asks for a radius that then passes | `test_a_failed_check_asks_for_a_radius_that_passes` | caught | |
  | Negative cost / zero rate / nothing cut: no cut | `test_no_bound_without_a_positive_cost_per_metre` | caught | |
  | Unreachable end needs whole-graph reachability | `test_an_end_that_reaches_nothing_anywhere_is_safe` | caught | |
  | Demo with an attendance area: every output equals the whole-network run, cut used for walk and bike | `test_a_run_near_the_destination_writes_what_a_whole_network_run_does` | caught | `TRIES = 0` forces the whole graph |
  | Fallback after three failed circles on real data | not exercised | not exercised | |

- Settled decisions (do not re-report): exactness over speed (a plain cut
  would change which homes count, since eligibility is by the cheapest
  route); screens and exposure left for later.
- Known limitations: destinations without an attendance area (homes all
  over the map) gain nothing; `cost_per_metre` walks every edge once per
  graph and weight (remembered in a `WeakKeyDictionary`).

## Review, round 1

- Loop: `distance-cut-1`, round 1; Standard tier.
- Target reviewed: `45238e2..b908815` (`b90881501bcc769b5f5561fd27bba0c9d3215bef`).
- Verdict: **accepted** at `b908815`. No confirmed blocking findings.
- Reviewed the complete five-file diff in an independent detached clone with its own Git metadata. The bound uses distance to the nearest entrance, the minimum nonnegative edge-cost/geometric-distance ratio, and a strict comparison. Home certification covers both street ends and the attached node before eligibility/route splitting; the alternative certificate covers the additive search gap in the objective's units. Movement states and sinks map to their street positions. Missing/invalid bounds and unsuccessful certification fall back to the whole search; screens still receive the full network. The intentional result change from the unused node table to the path tree is reflected in the internal consumer, with no remaining repository reads of the removed field.
- The committed grid/certificate, widening, unreachable-node and whole-run equivalence tests pass. An independent probe adds 12 synthetic turn graphs with two entrances: 2778 certified comparisons of costs, paths, selected entrance and near-best alternatives match whole-graph controls.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1032 tests, four deselected, 95.07% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions. The three pre-existing multithreaded-fork warnings recur in the parallel-run test.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- Limits: the private real-area evidence was not inspected or independently reproduced. The specific three-failed-circles sequence on real data remains unexercised, as declared; the iteration limit and whole-graph fallback were reviewed. Synthetic equivalence does not provide a real-study timing claim.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. All additional probes use made-up graphs outside the repository.
