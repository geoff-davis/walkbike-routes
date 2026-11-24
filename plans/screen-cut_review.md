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
