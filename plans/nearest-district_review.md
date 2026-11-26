# Review: nearest-district

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
- Review target: `fd02ba4` -> `4463989`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `nearest-district-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-26 18:36`

## Handoff, round 1

- Tier: Standard (changed config contract and the home split)
- Intended behavior and acceptance criteria:
  - A destination may have `nearest` together with `serves` or a limiting
    `outline` (public config or the private file's `outlines:`). The
    `serves` plus limiting outline refusal is unchanged.
  - Each member's homes are first cut to its own area by `origins_for`
    (unchanged). In `nearest_origins`, a pooled home is chosen only among
    members whose area holds it (`allowed` mask passed to
    `choose_nearest`), by street distance where any allowed member is
    reachable, else straight line. No home is handed to a member that does
    not count it, so none is lost.
  - Without areas the split is unchanged (every member allowed for every
    pooled home).
  - `destinations.parquet` gains `nearest_group`: the group name, or `""`
    for destinations outside any group (including network-flow job
    clusters).
  - `docs/adapting.md` documents it, and that charter, magnet and private
    schools are left out of groups.
- Changed areas: `config.py` (two guards removed), `config_models.py`
  (docstring), `study_nearest.py` (`allowed` mask, log count),
  `__main__.py` (column on write), `docs/adapting.md`,
  `tests/test_nearest.py`.
- Pattern inventory: `choose_nearest` has one caller; `Destination.nearest`
  is read in `config.py` (group size check) and `study_nearest.py`. Readers
  of `destinations.parquet` (`report/tables.py`, `report/map.py`) select
  columns by name and ignore the new one.
- Verification:

  | Command | Revision | Result | Run by |
  | --- | --- | --- | --- |
  | `make check` | `4463989` | 1078 passed, coverage 95.79% | implementer |
  | `make validation-check` | `4463989` | 58 passed | implementer |
  | `pytest tests/test_nearest.py` with `src/` stashed to base | base src, head tests | 3 failed, 3 passed | implementer |
  | `pytest tests/test_network_flows.py tests/test_nearest.py -W error::Warning` | `4463989` | 23 passed | implementer |

  On base: no `nearest_group` column; `choose_nearest()` takes 2
  arguments; config refuses "give nearest, serves or a limiting outline,
  not more than one".

- Condition inventory:

  | Condition | Test or probe | Result | Note |
  | --- | --- | --- | --- |
  | home nearer a member whose area lacks it | `test_a_home_goes_only_to_members_whose_area_holds_it` | caught | edge -> A |
  | allowed member unreachable by street, straight line used | same | caught | far -> A |
  | no mask: unchanged behaviour | same, last assert | caught | |
  | nearest + limiting outline, nearest + serves accepted | `test_a_nearest_group_may_also_have_an_area` | caught | |
  | group column written, empty outside groups | `test_each_home_goes_only_to_its_nearest_destination` | caught | |
  | job clusters get `""` without a dtype warning | network-flow tests under `-W error` | caught | |
  | end-to-end split with real area files | not exercised | – | the demo town refuses outlines; covered by the unit mask test and the existing end-to-end group test |

- Settled decisions (do not re-report): per-destination area rather than a
  group-level setting (reuses outline `file`/`where`/`name`); charter,
  magnet and private schools are left out by configuration, not code.
- Known limitations and out of scope: a home inside no member's area never
  enters the pool (already the case: it is in no member's origins). The map
  draws the member's area (the district), not the share nearest it.
