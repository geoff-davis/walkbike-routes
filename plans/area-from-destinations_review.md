# Review: area-from-destinations

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
- Review target: `1eb88e1` -> `0b643c4`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `area-from-destinations-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-26 21:16`

## Handoff, round 1

- Tier: Standard (new config option, changes what a run downloads)
- Part two of issue #40 (part one, per-destination wording, is #43).
- Intended behavior and acceptance criteria:
  - `area.source: destinations` is accepted; `place` and `boundary_file`
    are refused with it ("does not apply"), as for the other sources; a
    study with no destinations (network flows only) is refused.
  - `layers.destination_areas(study)` gives one shape per destination, in
    `study.crs` or the UTM zone of the destinations: its limiting outline,
    else its `serves` area, else its location (or its `boundary_file` for
    an area destination) buffered by `layers.reach_m(study)`, the longer of
    the walk and bike time limits at the default 3 and 10 mph. A drawn-only
    outline (`limits: false`) counts as no area.
  - `download.area.resolve(area, fetcher, crs, shapes)` uses those shapes
    as the boundary for `source: destinations` (refusing without them), then
    projects and buffers by `buffer_m` exactly as for the other sources.
  - `layers.build` passes the shapes only for that source. Other sources
    are unchanged (`shapes` defaults to None).
  - `docs/adapting.md` documents it.
- Changed areas: `config.py`, `config_models.py`, `download/area.py`,
  `layers.py`, `docs/adapting.md`, `tests/test_osm_streets.py`.
- Verification:

  | Command | Revision | Result | Run by |
  | --- | --- | --- | --- |
  | `make check` | `0b643c4` | 1082 passed | implementer |
  | `make validation-check` | `0b643c4` | 58 passed | implementer |
  | the 2 new tests with `src/` at base | base src, head tests | 2 failed: no `reach_m`; "'destinations' is not one of demo, place, boundary_file" | implementer |
  | a private config's areas computed with the new source (no run) | `0b643c4` | every destination had its own area; the derived extent was about 20% smaller than the place plus buffer and covered all of it | implementer |

- Condition inventory:

  | Condition | Test | Result |
  | --- | --- | --- |
  | limiting outline used as is | `test_an_area_from_the_destinations_is_their_areas_and_reach` | caught |
  | no area: boundary buffered by reach, not more | same | caught |
  | reach is the longer of walk and bike | same | caught |
  | full `layers.build` with fake public services | same | caught |
  | `resolve` refuses without shapes; extent covers shapes plus buffer | same | caught |
  | `place` refused; no destinations refused | `test_an_area_from_the_destinations_takes_no_place_and_needs_destinations` | caught |
  | `serves` area used (same branch shape as the outline case) | not exercised directly | – |

- Settled decisions (do not re-report): reach uses the default speeds, not
  per-preset speeds (a straight-line upper bound for routes along streets);
  `buffer_m` is reused as the routing margin rather than a new key;
  existing configs keep their source until a later config change.
- Known limitations and out of scope: the public schools config still uses
  `source: place`; switching it is a separate change once a full run has
  been checked. Error messages in the download code still say "study area".
