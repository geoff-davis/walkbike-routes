# Review: fix-first-order

## Current state

- Status: complete, accepted at `e9a5ef3`
- Next action: none
- Review target: `f92116a` -> `e9a5ef3`
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

## Review, round 1

- Loop: `fix-first-order-1`, round 1; Standard tier.
- Target reviewed: `f92116a..7b1d26e` (`7b1d26e84d6eb60acc16f626720e923c5fe4df23`). The handoff was read from `28a3f95` as requested; implementation checks used `7b1d26e`.
- Verdict: **changes required** for `7b1d26e`.

### FO1 — P2, blocking: tier-first ranks make the top watch places disappear from maps and lose report links

- Location: the new rank assignment in `src/walkbike_routes/analysis/exposure_lists.py:94-97`; affected consumers are `report/map.py:802` and `report/tables.py:565`.
- Trigger: a street/crossing list has at least ten fix-first places plus watch places. The new ordering necessarily gives every watch place a rank above the map's `EXPOSURE_ROWS=10` cutoff.
- Defect: the map still selects `is_first | rank <= EXPOSURE_ROWS`, and report link generation uses the same absolute-rank check. After putting fix-first places first, this silently removes every watch place from such maps and strips the watch rows' map links. `README.md` still promises every fix-first place and the top watch places; the report's `WATCH_NOTE` still says the maps show more of its highest-ranked watch places. The requested ranking/numbering change should not eliminate that existing watch-map behavior.
- Confirmed by the identical synthetic ranking -> saved list -> map/report probe on base and head: create ten places `f00..f09` with people=6 and risk_points=3 (exposure=18), and one place `watch` with people=100 and risk_points=2 (exposure=200). Use counted=200 and the default FixFirst rule. On `f92116a`, `watch` is rank 1, mapped as S1 and its report cell is a Linked value pointing to the map. On `7b1d26e`, fix-first ranks correctly become 1..10 and `watch` becomes rank 11, but the map includes zero watch places and its report cell is just the unlinked text S11. These are fabricated rows with simple line geometries; the loss also applies to crossings through the shared consumer logic.
- Requested fix: choose the bounded watch subset independently of the absolute fix-first-first rank, while retaining the canonical ranks/tags. Update both the map selection and the report's determination of whether a place can be linked (prefer a shared selection rule). Add a regression with at least ten fix-first places and a watch place that verifies it remains mapped and linked under its new S11/C11 label.

### Verification and limits

- Reviewed the complete five-file diff in an independent detached clone with its own Git metadata; used a separate detached base snapshot for the identical regression probe.
- The intended people × points formula, retained person-km, tier-first ordering and contiguous numbering pass the committed tests. Searches of report/docs found no remaining claim that street ranking multiplies by length. The fix-first tier rule itself is unchanged.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1033 tests, four deselected, 95.08% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions. Three pre-existing multithreaded-fork warnings recur.
- `make validation-check` passes: lint, formatting, types and 58 tests. Existing tests do not cover FO1's map/report cutoff interaction.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. Independent probes used only fabricated exposure rows outside the repository. No other blocking findings.

## Handoff, round 2

- Target: `7b1d26e` -> `e9a5ef3` (this handoff is in the next commit).
- FO1 fixed: `ranked` saves `tier_rank`, a place's position within its
  tier (1, 2 ... for fix first, and again for watch), in both column lists.
  `text.on_map(row)` is the one rule for the map (`map._exposure`) and the
  report's links (`tables.exposure_table`): every fix-first place, and a
  watch place whose `tier_rank` is at most `MAP_WATCH_ROWS` (10). Lists
  saved before this change have no `tier_rank`; there the rank decides, as
  before (the existing `test_a_place_links_to_the_map_and_to_a_street_map`
  covers that path). README now says "the ten highest-ranked watch places".
- Regression: `test_watch_places_after_ten_fix_first_places_are_still_mapped_and_linked`
  is the round-1 probe: ten fix-first places and one high-flow watch place,
  through `ranked`, a saved Parquet list, `map._exposure` and
  `exposure_table`. The watch place is rank 11, `tier_rank` 1, drawn as S11
  and linked. With the round-1 `map.py` and `tables.py` it fails (the map
  draws only S1..S10).
- `make check`: 1040 passed, 95.74%. `make validation-check`: 58 passed.

## Review, round 2

- Loop: `fix-first-order-1`, round 2; Standard tier.
- Target reviewed: `7b1d26e..e9a5ef3` (`e9a5ef3d8e746ca349de1be44af144a39cf762fa`). The round-2 handoff was read from `3ed4689` as requested; implementation checks used `e9a5ef3`.
- Verdict: **accepted** at `e9a5ef3`. FO1 is resolved; no new blocking findings.
- Reviewed the complete fix diff in an independent detached clone with its own Git metadata. Canonical ranks remain tier-first and contiguous; the saved tier-local rank gives watch places their own budget. Both map inclusion and report-link generation now use the shared `text.on_map` rule, with the previous rank-based rule retained for older saved lists without `tier_rank`.
- The committed end-to-end FO1 regression passes. An independent synthetic probe with ten fix-first and twelve watch places passes for both streets and crossings: the map and report links agree on S1..S20 / C1..C20, retaining exactly ten watch places under their canonical tags and excluding the last two. The identical probe fails on `7b1d26e`, which retains only S1..S10. Independent checks also pass for legacy missing/NaN tier-rank fallback and a high-rank fix-first place remaining mapped.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1034 tests, four deselected, 95.08% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions. Three pre-existing multithreaded-fork warnings recur.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. Additional probes used only fabricated exposure rows outside the repository.
