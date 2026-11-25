# Review: recorded-crashes

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
- Review target: `9fa5d79` -> `4308084`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `recorded-crashes-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-25T16:31Z`

## Handoff, round 1

- Tier: Standard (new adapter, config keys, run output and map layer;
  private inputs).
- Stacked on PR #35 (`forecast-path`, accepted at `bbad720`); the base
  `9fa5d79` is that branch's tip.
- Asked for by the user: alongside the crash forecast, show police-recorded
  bike crashes as a toggle layer.
- Intended behavior and acceptance criteria:
  - The private `.local.yaml` may hold `recorded_crashes: {crashes: <file>,
    parties: <file>}` (`parties` optional; paths relative to that file).
    Each named file must exist when the config loads (the same check as
    `crash_forecast`, now shared in `_private_file`); `crashes` is required
    when the key is given; other sub-keys are refused. The main config
    refuses `recorded_crashes` and `crash_parties`.
  - `switrs.bike_crashes(crashes, parties)`: reads a spatial file, a
    GeoParquet, or a plain table (CSV or Parquet) with `POINT_X`/`POINT_Y`
    in degrees (0 or blank: no point, dropped). Refuses a file without
    `CASE_ID`, `ACCIDENT_YEAR`, `COLLISION_SEVERITY` or `BICYCLE_ACCIDENT`,
    or without points or a CRS; a parties file without `CASE_ID`,
    `PARTY_TYPE` or `PARTY_AGE`. Keeps `BICYCLE_ACCIDENT == "Y"`. Severity
    codes 0-4 become words. The rider's age group comes from the youngest
    bicyclist (`PARTY_TYPE` 4) with a stated age (998 is "not stated"):
    under 13 child, 13-17 teen, else adult; "age not recorded" otherwise.
    Rows are ordered worst severity last, then by year.
  - `layers.recorded_crashes` clips to the streets' extent; `__main__.build`
    writes `recorded_crashes.parquet` and lists it, or removes an earlier
    run's file (the forecast now shares this loop).
  - `map.recorded_points` groups crashes at the same point (6 dp): one row
    per place with its crashes worst first, the places with the worst
    crashes last. The page draws them in a `crashes` pane, toggled by
    `showcrashes` (unchecked, hidden when there are none): larger with
    more crashes, solid when anyone was killed or severely injured; a popup
    lists each crash; a legend entry gives the years. Nothing else reads it.
- Changed areas: `switrs.py` (new), `config.py`, `config_models.py`,
  `layers.py`, `__main__.py`, `report/map.py`, `report/map.html`,
  `README.md`, tests.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `make check` | `4308084` | 1062 passed, 95.75% | none | implementer |
  | `make validation-check` | `4308084` | 58 passed | none | implementer |

  On a private real-area copy of a run's outputs (not readable by the
  reviewer): 2,198 bike crashes within the streets, 2014-2026, every
  severity but property damage only, every age group; 127 places hold more
  than one. The map's exercise script passed in headless Chrome with the
  layer on, all 2,198 crashes in the page data, the last place drawn
  holding a fatal crash.

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Bike filter, severity words, youngest rider, 998, driver ages ignored, order | `test_bike_crashes_keep_year_severity_and_the_youngest_rider` | caught | |
  | CSV points; 0 and blank dropped | `test_a_tims_csv_gives_its_points_and_skips_crashes_without_one` | caught | |
  | Missing columns or points refused | `test_crash_files_without_their_columns_are_refused` | caught | |
  | Drawn on the map, clipped, grouped at one point, worst last | `test_recorded_crashes_are_drawn_on_each_map_and_only_inside_the_streets` | caught | made-up crashes on the demo town |
  | No files: no parquet (old one removed), empty layer | `test_without_crash_records_the_layer_is_empty_and_an_old_file_is_removed` | caught | |
  | Private-file checks (missing files, no crashes, stray key) | `test_the_private_file_is_checked_when_the_config_loads` | caught | |
  | Main config refuses both keys | `test_the_main_config_may_not_name_crash_records` | caught | |
  | Toggle, legend years, size, popup, removal in a browser | `test_the_recorded_crashes_layer_toggles_with_a_legend_and_popups` | caught | headless Chrome |

- Settled decisions (do not re-report): display only, no ranking change
  (the user chose this, as for the forecast); a California adapter, as the
  design rules allow for state-only sources; the popup shows year,
  severity and age group only (no date, time, case number or other
  personal details); same colour as the forecast, as both are crash data.
- Known limitations: no injured-party table, so "severe injury" is the
  crash's worst injury, not necessarily the rider's; a crash with a rider
  but `BICYCLE_ACCIDENT` blank is left out.
