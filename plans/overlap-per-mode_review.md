# Review: overlap-per-mode

## Current state

- Status: complete, accepted at `5e2237a`
- Next action: none
- Review target: `00178f0` -> `5e2237a` (accepted, round 2)
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `overlap-per-mode-1`, round 2 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-26 02:33` (renewed for round 1; the first expiry had the wrong date)

## Handoff, round 1

- Tier: Standard (changed logic in what the map draws)
- Intended behavior and acceptance criteria:
  - The map's car-overlap band on the walking view is ranked from the walk
    percentile alone, on the cycling view from the bike percentile alone,
    each re-ranked among edges carrying both cars and that mode
    (`analysis/overlap.py:mode_tiers`). The driving view shows the combined
    `overlap_tier`, unchanged.
  - Same tier cut-offs (top 1%, 5%, 10%) and the same index form,
    sqrt(car percentile x mode percentile), as the combined index.
  - Page data `overlap` is keyed by mode, then limit key (`""` or minutes);
    `overlapOf(r)` reads `D.overlap[r.mode][limitKey(r)]`.
  - The key heading names the mode: "Car and walking overlap" or "Car and
    cycling overlap" ("walk/bike" on the driving view).
  - Works on existing outputs: tiers come from the `car_percentile`,
    `walk_percentile` and `bike_percentile` columns that `overlap.parquet`
    already stores, so a `report` rebuild is enough. A mode without its
    percentile column gets no tiers.
  - `overlap()` output is unchanged in columns and values (tiering moved into
    `_tiers`, shared with `mode_tiers`; `overlap_tier` stays a string dtype).
- Changed areas and affected producers and consumers:
  `src/walkbike_routes/analysis/overlap.py` (`_tiers`, `mode_tiers`),
  `src/walkbike_routes/report/map.py` (`page_data` overlap assembly;
  combined page still sends `{}`), `src/walkbike_routes/report/map.html`
  (`overlapOf`, key heading, title text), README and `docs/assumptions.md`.
- Pattern inventory: `overlap_tier` is read only in `report/map.py` (now via
  `mode_tiers`) and tests; `D.overlap` is read only through `overlapOf` and
  in `tests/test_map_page.py`.
- Verification:

  | Command | Revision | Result | Run by |
  | --- | --- | --- | --- |
  | `make check` | `5e2237a` | 1076 passed, coverage 95.76% | implementer |
  | `make validation-check` | `5e2237a` | 58 passed | implementer |
  | new and changed tests with `src/` stashed back to base | base src, head tests | fail as below | implementer |

  On base source: `test_cars_and_overlap.py` fails to import `mode_tiers`;
  `test_the_map_switches_to_a_shorter_time_limit` fails with
  `overlap drawn 10, at 5 min 0`; `test_the_overlap_band_is_painted_beneath_the_trips`
  fails with `walk: 17 bands,` (the page drew the combined bands, not the
  walking ones).

- Condition inventory:

  | Condition | Test or probe | Result | Note |
  | --- | --- | --- | --- |
  | walking view ranks from walk only | `test_each_mode_view_ranks_the_overlap_from_its_own_trips` | caught | AB only |
  | cycling view ranks from bike only | same | caught | DC, not AB |
  | driving view gets combined tiers | same | caught | |
  | layer without the mode's column | same (`walk_only`) | caught | no tiers |
  | page draws the mode's own bands at each mode | `OVERLAP` in `test_map_page.py` | caught | count per mode |
  | key heading names the mode | same | caught | |
  | walking and cycling data differ in the demo | same | caught | |
  | shorter limit uses the mode's cut data | `LIMIT` | caught | |

- Settled decisions (do not re-report): the user chose per-mode overlap on
  the walking and cycling views; driving keeps the combined index.
  `overlap.parquet` is not given extra tier columns; the map derives them.
- Known limitations and out of scope: the per-mode percentile ranks are
  among all edges with that mode's flow (as stored), not re-ranked; only the
  index is re-ranked among shared edges, as in the combined case.

## Review, round 1

- Loop: `overlap-per-mode-1`, round 1, Standard tier.
- Target: `00178f0..5e2237a`; handoff read from `96c5d60`.
- Verdict: **not reviewed; loop expired**, no acceptance at `5e2237a`.
- The handoff records expiry `2026-11-25 02:33` UTC. The current-time tool reported `2026-11-26 01:32:49 UTC`, already past that deadline when this request was opened.
- AGENTS.md requires the reviewer to stop at expiry. Only the committed handoff, agent instructions and diff statistics were inspected; no implementation checks or behavioral review were performed. No snapshot was created and no private files were read.
- Next action: none

## Handoff, round 2

- Same target, `00178f0` -> `5e2237a`, unchanged. Round 1 was not reviewed
  because the expiry I recorded had the wrong date (2026-11-25, not 26); the
  loop is renewed to `2026-11-26 02:33` UTC. The round 1 handoff above
  stands as written. Submission 2 of 3.

## Review, round 2

- Loop: `overlap-per-mode-1`, round 2, Standard tier; reviewed under the renewed `2026-11-26 02:33 UTC` expiry. Round 1 performed no behavioral review.
- Target: `00178f0..5e2237a` (`5e2237a0c136bf62f87e997053eb6477076e4446`); handoff read from `2580e52`.
- Verdict: **accepted at `5e2237a`**. No blocking or non-blocking findings.
- Verified walking/cycling tier computation uses the respective stored percentile, ranks only shared edges, keeps the existing thresholds and yields no tiers when that mode's column is absent. Driving retains the combined tier. The nested mode/limit data reaches the browser, and legend headings name walking/cycling as specified. Combined-destination maps retain their empty overlap layer.
- Compatibility probe: 84 synthetic base/head overlap frames matched exactly, including all columns, values and dtypes (empty, singleton, larger, tied, zero and negative-flow inputs, walk-only, bike-only and both). Driving tiers matched the stored combined tiers; missing-mode and zero-flow checks passed.
- Existing-output probe: generated synthetic demo outputs using `00178f0`, then rebuilt maps only using `5e2237a`. The same browser probes for shorter-limit switching and per-mode overlap failed with the base report and passed with the head report. These exercise band counts, distinct walking/cycling data, mode-specific headings, shared-canvas draw order and visible/hidden trips widths.
- Independent snapshot checks: `uv sync --locked --offline` passed; `make check` passed (lint, format, types; 1,053 tests passed, 21 deselected; 94.42% coverage). Three existing multiprocessing fork warnings. `make validation-check` passed, 58 tests. `git diff --check 00178f0..5e2237a` passed.
- Privacy exclusions: ignored `tests/test_env_file.py`; deselected the 21 parameterized config, forecast and recorded-crash cases identified in earlier reviews that load companion private YAML files. No `.env`, `*.local.yaml`, repository `data/` or `output/`, or real records read. All probes used made-up data in temporary folders.
- Implementer tree left unchanged apart from this review entry. Temporary snapshots and synthetic probe artifacts removed after the verdict. Acceptance authorizes no commit, push or merge.
