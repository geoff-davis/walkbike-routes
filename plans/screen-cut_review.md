# Review: screen-cut

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
- Review target: `f92116a` -> `b08465a`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `screen-cut-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-24T17:29Z`

## Handoff, round 1

- Tier: Standard (changes the graph every barrier screen routes on; the
  outputs must not change). Follows the accepted distance-cut slice (#30).
- Asked for by the user: apply the distance cut to the barrier screens.
- Intended behavior and acceptance criteria:
  - `study_active._screen`, per treatment: the screen graph, `improved`
    and turn costs are still built on the whole network. A `local.Near`
    is cut from the physical screen graph with
    `screens.screen_cost_per_metre` (least of `generalized_cost_s` and
    `treated_cost_s` per metre of straight line, over all links including
    links available only when treated). Turn and crossing costs are
    enforced non-negative in `turns.turn_graph`, so movement edges cost at
    least their link's.
  - `improvement_screen(..., near=, reachable=)` routes on `near.graph`
    (and its movement graph) and calls `_check_near`: every origin node's
    baseline cost (Dijkstra honouring `baseline_available`) must be below
    `near.bound`, or the node must reach no entrance on the whole physical
    graph. A treatment only lowers costs (`treated_cost_s <= cost`,
    opened links, junction reductions), so treated flows and every
    candidate's counterfactual costs are then below their bounds too and
    match the whole network. Otherwise `NotExact(radius)`; the caller
    widens (or computes `reachable` once), up to `TRIES`, then screens the
    whole network.
  - Candidates (`_street_places`, `_crossing_places`) are drawn from the
    whole `graph`, so barrier sections and their `S0001` numbering are
    unchanged. Each candidate has treated flow > 0, so it holds a street on
    a cohort route, inside the circle; edges of it outside the circle are
    unknown to the cut search and change nothing.
  - First circle per treatment: `_first_circle`, from today's route costs
    at the screen's `per_m`.
- Changed areas: `src/walkbike_routes/analysis/screens.py`,
  `src/walkbike_routes/study_active.py`, `tests/test_local_routes.py`.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `uv run pytest tests/test_local_routes.py` | `b08465a` | 12 passed | none | implementer |
  | `make check` | `b08465a` | 1039 passed, 95.76% | none | implementer |
  | `make validation-check` | `b08465a` | 58 passed | none | implementer |

  On a private real-area study (not readable by the reviewer): one school,
  every preset; all 76 Parquet files including both screens, and the
  summary rows, matched an earlier whole-network run exactly. A first
  version that drew candidates from the cut graph renumbered sections
  (`section_id` only); that is why candidates now come from the whole graph.

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Demo with a 700 m attendance area: all outputs, both screens non-empty, equal to the whole-network run; screens log the circle | `test_a_run_near_the_destination_writes_what_a_whole_network_run_does[1.5]` | caught | |
  | Screen's first circle too small: `NotExact`, widened, still equal | same test `[0.4]` (first circle forced to 150 m) | caught | |
  | Section numbering with sections outside the circle | real-area probe only | caught there | the demo has too few sections outside to show it |

- Settled decisions (do not re-report): exactness over speed; screen graphs
  and turn costs stay whole-network (a link's cost can depend on its
  neighbours).
- Known limitations: building each treatment's whole screen graph and turn
  costs is now most of a screen's time.

## Review, round 1

- Loop: `screen-cut-1`, round 1; Standard tier.
- Target reviewed: `f92116a..b08465a` (`b08465ad0b81b3fa4eb8e606e80a39583e8aa258`). The handoff was read from `ad4b67c` as requested; implementation checks used `b08465a`.
- Verdict: **changes required** for `b08465a`.

### SC1 — P2, blocking: certified cuts can change tied treated routes and select a different barrier

- Location: `src/walkbike_routes/analysis/screens.py:621-634` (the new use of `near.graph` for screen searches), interacting with `routing/local.py`'s subgraph copy and `treated_flows`' NetworkX Dijkstra traversal.
- Trigger: two equal-cost routes to the destination, with predecessor insertion order different from the source-node iteration order. This can occur in the plain screen graphs used by presets without turn movements, including Furth.
- Defect: `local.near` copies the induced subgraph. Rebuilding its edges groups them by source node and can reorder a node's predecessors. `treated_flows` searches the reversed graph using NetworkX's insertion-order tie choice. Thus a cut can select a different equally cheap route, even though all route nodes remain inside the circle and `_check_near` certifies the costs. Candidate generation uses that treated flow, so retaining the whole graph for section numbering does not preserve the screen output.
- Confirmed reproduction, run on base and head: insert directed edges in this order: `ha: H->A`, `hb: H->B`, `bd: B->D`, `ad: A->D`, then an isolated `far` node. Give every edge baseline cost 200, treated cost 100, physical time/length 100, `baseline_available=True`, and mark all four routable IDs improved. The `ha/ad` physical edges are named East Street; `hb/bd` are named West Street. One origin at H has weight 10 and access time 0; screen to D with 10 candidates and no time cap. Coordinates are H=(0,2), A=(-1,1), B=(1,1), D=(0,0), far=(100,0). Use `screen_cost_per_metre` and a radius-10 `local.near`.
- Evidence: the baseline cost at H is 400, below its bound (approximately 1272.79), and the cut is accepted without `NotExact`. On base `f92116a` and head without a cut, treated flow uses `bd/hb` and the screen returns **S0002, West Street**, saving 33.333333333333336 weighted route-cost minutes. On head with the certified cut, treated flow uses `ad/ha` and the screen instead returns **S0001, East Street**, with the same savings. The predecessor order at D changes from B,A to A,B. This is a different intervention/location, not just renumbering.
- Violated invariant: the handoff requires screen outputs to equal the whole-network run. A cost certificate does not by itself preserve insertion-order path ties, which control candidate selection and can also affect physical-time cohorts in the counterfactual engine.
- Requested fix: preserve the original search traversal/tie order when filtering the screen graph (an order-preserving filtered view is one possible approach), and ensure both treated-flow and counterfactual searches retain their whole-graph path choices. Add this tied-route screen as a regression, asserting the entire screen table equals the whole-network result.

### Verification and limits

- Reviewed the complete three-file diff in an independent detached clone with its own Git metadata; used a separate detached base clone for the identical synthetic input.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1033 tests, four deselected, 95.10% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions. Three pre-existing multithreaded-fork warnings recur.
- `make validation-check` passes: lint, formatting, types and 58 tests. The committed screen widening/equivalence tests pass but do not catch SC1's tie-order case.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. The private real-area evidence was not inspected. The independent reproduction uses only a made-up graph and the existing synthetic screen-edge fixture.

## Handoff, round 2

- Target: `b08465a` -> `ad4b67c`.
- SC1 fixed: `local.near` now builds the cut with `_ordered_subgraph`,
  keeping every node's successors and predecessors in the whole graph's
  order (shared edge-data copies, so succ and pred agree). This covers the
  route searches as well as the screens.
- Regression: `test_the_cut_keeps_each_nodes_link_order_so_ties_break_the_same_way`
  uses the round-1 diamond insertion order (H-A, H-B, B-D, A-D, plus a far
  node): it checks every node's succ and pred order and that the
  reversed-graph `nx.multi_source_dijkstra` (the call `treated_flows` and
  the counterfactual engine make) picks D-B-H on the cut as on the whole
  graph. With the old `subgraph().copy()`, D's predecessors read
  `[A, B]` instead of `[B, A]` and the path is D-A-H (shown by a probe
  on the old code). It does not run the whole screen table; please say if
  that is still required.
- `make check`: 1040 passed, 95.76%. `make validation-check`: 58 passed.
