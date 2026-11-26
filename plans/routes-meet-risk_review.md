# Review: routes-meet-risk

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
- Review target: `ab22950` -> `2d4f391`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `routes-meet-risk-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-26 20:51`

## Handoff, round 1

- Tier: Standard (an output value changes: the `tier` column)
- Intended behavior and acceptance criteria:
  - The top exposure tier is `"priority"` instead of `"fix first"`
    everywhere it is written or shown: `exposure_streets.parquet` and
    `exposure_crossings.parquet` `tier`, the report, the map page (badges,
    legend, tables, toggle), the rule sentences and key terms, README and
    docs. `"watch"` is unchanged.
  - The exposure section and per-run lists are headed "Where routes meet
    risk" (was "Where routes meet traffic today" and "places to fix first").
  - No text a reader sees still says "fix first" (`git grep -i
    "fix[- ]first"` outside `plans/` returns nothing).
  - Internal identifiers (`FIX_FIRST`, `fix_first_rule`, the manifest's
    `exposure.fix_first`, the page data's `fix_first` counts) and the config
    key `exposure.fix_first` are unchanged, so existing configs parse.
- Changed areas: wording in `analysis/exposure_*.py`, `config_models.py`,
  `report/map.html`, `report/map.py`, `report/tables.py`, `report/text.py`,
  README, `docs/adapting.md`, `docs/assumptions.md`, and test expectations.
- Pattern inventory: every comparison against the tier string goes through
  either `text.FIX_FIRST` or the literal, and all literals were replaced
  together (Python and `map.html`, including `TIER_COL` keys).
- Verification:

  | Command | Revision | Result | Run by |
  | --- | --- | --- | --- |
  | `make check` | `2d4f391` | 1078 passed | implementer |
  | `make validation-check` | `2d4f391` | 58 passed | implementer |
  | exposure and report tests with `src/` stashed to base | base src, head tests | 26 failed, 121 passed | implementer |

- Settled decisions (do not re-report): the names "priority" and "Where
  routes meet risk" are the user's choice; internal names and the config key
  stay to avoid breaking configs.
- Known limitations and out of scope: `docs/images/claremont-traffic-today.png`
  still shows the old labels until the screenshot is retaken; runs written
  before this change hold `"fix first"` in their parquet `tier` column and
  should be rerun (the map would draw their top tier in the watch colour).
