# Review: map-legibility

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
- Review target: `ad78a4c` -> `952c45d`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `map-legibility-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-25T23:53Z`

## Handoff, round 1

- Tier: Standard (a config form and map drawing).
- Asked for by the user, after looking at real maps: forecast circles all
  looked the same size; car overlap was hard to tell from bike red and hid
  under busy bike streets; the forecast should say its time frame (a
  yearly rate) with one decimal, not three; recorded crashes should say
  their period; and a stray `_smoke.html` appeared beside real maps.
- Intended behavior and acceptance criteria:
  - Forecast circles: four classes by the drawn corners' expected crashes
    (from the 99th, 95th and 75th percentiles; radius 12, 8, 5, 2.5;
    fuller fill for larger). The legend lists each class with its range
    (one decimal, "under 0.1" below 0.05).
  - Private config: `crash_forecast` may be a mapping `{file, years:
    [first, last]}` (integers, 1900 <= first <= last <= 2100); the plain path
    still works; stray keys, no file, bad years and a missing file are
    refused at load. `Study.crash_forecast_years`; the main config refuses
    it. The run writes `first_year`/`last_year` columns (nullable) in
    `crash_forecast.parquet`; maps get `D.crashYears` (or null, including
    for older parquet without the columns).
  - Popup: expected and recorded crashes "in first-last" when known, the
    expected to one decimal, and a yearly rate: "about N a year" (>= 0.95),
    "about one every N years" (>= 0.05), else "fewer than one every 20
    years". Without years: no period and no rate.
  - Car overlap: drawn in blues (top 1/5/10%) at the trips line's width at
    that edge plus 8 px (1 + 8 when trips are hidden), beneath the trips
    line, so a band shows on both sides. Legend swatches follow.
  - Recorded-crashes popup gives the years its records cover.
  - The browser-test helpers delete their exercised copy (`_smoke.html`) in
    a `finally`.
- Changed areas: `config.py`, `config_models.py`, `layers.py`,
  `report/map.py`, `report/map.html`, `README.md`, tests.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `make check` | `73bbf98` | 1073 passed, 95.76% | none | implementer |
  | `make validation-check` | `73bbf98` | 58 passed | none | implementer |

  `952c45d` changes only `README.md` (markdownlint passed). On a private
  real-area copy with the years added, a screenshot showed four distinct
  circle sizes with legend ranges and the overlap band visible around the
  thickest walking streets.

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Years reach the map; absent years give null | `test_the_forecast_years_reach_the_map` | caught | |
  | Forecast mapping checks | `test_a_forecast_setting_is_checked_when_the_config_loads` | caught | five cases |
  | Main config refuses the years | `test_the_main_config_may_not_name_forecast_years` | caught | |
  | Classes, legend range, popup period and rate in a browser | `test_the_crash_forecast_layer_toggles_with_a_legend_and_popups` | caught | headless Chrome |
  | Overlap drawn with every layer and mode | `test_every_map_draws_each_mode_and_layer_without_a_script_error` | caught | no script error; width not asserted |
  | Recorded-crashes popup period | not exercised | not exercised | one template string |

- Settled decisions (do not re-report): classes are relative to the
  corners on each map (the user asked for visible differences; the legend
  gives absolute ranges); years come from the private file since the
  forecast file does not record them; blues for overlap (the car colour).
- Known limitations: with many tied values two classes can share a lower
  bound, so a class may be empty; the last year may be partial, which
  makes the yearly rate slightly low.

## Review, round 1

- Loop: `map-legibility-1`, round 1, Standard tier.
- Target: `ad78a4c..952c45d` (`952c45d9879b9de9d2051767f097640b34302dac`). Handoff read from `7fed6b9`.
- Verdict: **changes requested** at `952c45d`. Two confirmed acceptance failures; no hypotheses reported.

### ML1 — P2, blocking: the overlap band paints over the trips line

- Location: `src/walkbike_routes/report/map.html:224` and `:231` (shared canvas renderer at `:161`).
- Trigger: enable trips and car overlap on a walking or cycling street present in both layers.
- Violated criterion: overlap must lie beneath the trips line, leaving a blue band on either side. Trips paths are added first and overlap paths afterward to the same canvas renderer; the wider, 90%-opaque blue stroke therefore covers the colored trips center.
- Evidence: a headless Chromium probe on the synthetic demo inspected Leaflet's canvas draw list. For the same bike edge, the trips stroke was at index 184 and overlap at index 307, with a shared renderer; widths were 1.7324 and 9.7324 px respectively. The width change is correct, but paint order is reversed. The same probe on `ad78a4c` also showed overlap painted later (width 8); this is an unmet new acceptance criterion rather than a newly introduced ordering regression.
- Requested fix: paint the wider blue overlap beneath trips, preserving the specified widths and other layer ordering. Add a browser assertion for actual draw order as well as width, including trips hidden.

### ML2 — P2, blocking: small forecast values produce invalid legend ranges

- Location: `src/walkbike_routes/report/map.html:177` and `:332`.
- Trigger: forecast class boundaries below 0.05; for example, synthetic expected counts 0.04, 0.03, 0.02 and 0.01.
- Violated criterion: the legend must explain each class's absolute range. The formatter returns the qualified phrase `under 0.1`, which the range builder treats as a numeric endpoint, producing contradictory or meaningless ranges.
- Evidence: Chromium rendered `top 1%: under 0.1 or more`, `top 5%: under 0.1 to under 0.1`, and `the rest: under under 0.1` for those four distinct synthetic values. The same probe on the base showed its earlier size-only legend. This is a defect in the newly added ranges, independent of the acknowledged tied-value limitation.
- Requested fix: handle qualified small-value bounds explicitly rather than composing them with `under`, `to`, or `or more`. Preserve the settled one-decimal display and relative classes; when display precision collapses bounds, use truthful wording. Add a browser case for sub-0.05 boundaries.

### Verification and limits

- Reviewed committed files in an independent local clone, detached at the target; implementation tree left unchanged.
- `uv sync --locked --offline`: passed. `make check`: passed (lint, format, types; 1,050 tests passed, 21 deselected, 94.41% coverage). Three existing multiprocessing fork warnings.
- Privacy exclusions: ignored `tests/test_env_file.py` and deselected tests that load companion `*.local.yaml` files: four config cases; forecast layer/no-layer/missing-file/year propagation cases and five forecast-mapping parameters; recorded-crash layer/no-layer cases and its parameterized private-file checks. No private files or real records were read.
- `make validation-check`: passed, 58 tests. `git diff --check ad78a4c..952c45d`: passed.
- Additional synthetic probes: plain-path and mapping forecast forms; missing file, missing mapping file key, stray key, reversed/scalar/boolean/out-of-range/wrong-length/non-integer years rejected; map year metadata present, null and legacy-column cases passed. Browser helper removed its exercised copy at the target (the same probe left it at the base).
- Private real-area screenshot evidence was not independently inspected. Both findings above reproduce using only made-up demo data.

## Handoff, round 2

- Target: `952c45d` -> `6c6ad94` (this handoff is in the next commit).
- ML1 fixed: in `draw()` the overlap loop now runs before the trips loop
  (both on the shared canvas `cv`, which paints in insertion order);
  agreement and the other layers keep their order. Widths are unchanged.
  `test_the_overlap_band_is_painted_beneath_the_trips` walks
  `cv._drawFirst` and asserts every band precedes every trips line, the
  band's weight is the trips width + 8, and 9 with trips hidden.
- ML2 fixed: each legend size gives its own corners' range,
  `floor1(min)` to `ceil1(max)`, as "a to b", "up to b" (low end 0.0) or a
  single value when they meet; a size with no corners is omitted. No
  "under" or "or more" in the legend. The popup's one-decimal value is
  unchanged. `test_small_forecast_values_get_truthful_legend_ranges`
  (values 0.01-0.04 and 0.2) and the updated forecast browser test.
- Both new tests fail with the round-1 `map.html` and pass on `6c6ad94`.
- `make check`: 1075 passed, 95.76%.
