# Review: fix-first-order

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
- Review target: `f92116a` -> `7b1d26e`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `fix-first-order-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-24T17:42Z`

## Handoff, round 1

- Tier: Standard (changes how the fix-first and watch lists rank and number
  places).
- Asked for by the user: fix-first places looked randomly ordered and were
  numbered with gaps. The user chose to rank streets by flow × points, like
  crossings, rather than flow × length × points.
- Intended behavior and acceptance criteria:
  - `exposure_streets`: a stretch's `exposure` is `people × risk_points`
    (`people` is its busiest link's flow, the value the tables show).
    `person_km` is still computed and kept.
  - `exposure_lists.ranked`: tiers are assigned first; places are then
    sorted fix first before watch, each by exposure descending, then
    `place_id`, and `rank` is 1..n in that order. So fix-first places are
    S1..Sk and C1..Ck without gaps, and watch places follow. Report tables
    and the map take their labels from `rank`, so they agree.
  - Report text (method sentence and the "S1, C1" key term) and
    `docs/assumptions.md` say the new rule; nothing still says "× length".
- Changed areas: `analysis/exposure_streets.py`, `analysis/exposure_lists.py`,
  `report/text.py`, `docs/assumptions.md`, `tests/test_exposure.py`.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `make check` | `7b1d26e` | 1039 passed, 95.74% | none | implementer |
  | `make validation-check` | `7b1d26e` | 58 passed | none | implementer |

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | A watch place with more exposure than a fix-first place is numbered after it; ranks 1..n without gaps | `test_fix_first_places_are_numbered_first_without_gaps` | caught | fails with the base `exposure_lists.py` |
  | A street stretch's exposure is people × points, not person-km × points | `test_streets_join_same_named_neighbours_and_skip_places_without_risk` (expects 30.0) | caught | fails with the base `exposure_streets.py` (4.2) |

- Settled decisions (do not re-report): length leaves the ranking by the
  user's choice; the fix-first rule itself is unchanged.
- Known limitations: an "exposure" or priority column is not added to the
  tables; the order now follows the shown flow and points columns within
  each tier.
