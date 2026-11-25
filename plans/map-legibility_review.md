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
