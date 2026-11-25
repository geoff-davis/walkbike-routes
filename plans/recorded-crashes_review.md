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

## Review, round 1

- Loop: `recorded-crashes-1`, round 1; Standard tier.
- Target reviewed: `9fa5d79..4308084` (`43080849da186ee9b77c2ce7157712e124c9f3b3`). The handoff was read from `bafec34` as requested; implementation checks used `4308084`.
- Verdict: **changes required** for `4308084`.

### RC1 — P2, blocking: a valid source with no bicycle crashes crashes the adapter

- Location: `src/walkbike_routes/switrs.py:45-47`.
- Trigger: a spatial source has all required fields but no `BICYCLE_ACCIDENT == Y` rows, or it is empty with its schema intact.
- Confirmed evidence: fabricated GeoParquet with CASE_ID=`made-up`, ACCIDENT_YEAR=2020, COLLISION_SEVERITY=`4`, BICYCLE_ACCIDENT=`N`, and a Point with EPSG:4326 raises `AttributeError: 'DataFrame' object has no attribute 'ACCIDENT_YEAR'`. An empty GeoParquet with the same columns and CRS raises the same error.
- Cause: after the bicycle filter leaves zero rows, the geometry-filter list is `[]`. `read[[]]` selects zero columns rather than filtering rows, removing ACCIDENT_YEAR and the active geometry. This aborts a run instead of producing the valid empty recorded-crashes layer.
- Requested fix: use an empty-safe boolean Series/row selection or otherwise preserve the required schema and CRS when there are no retained rows. Add tests for an empty source and a nonempty source containing only non-bike crashes; also cover a plain table whose coordinate filter removes every row.

### RC2 — P2, blocking: a spatial file without point geometries is accepted and fails during map generation

- Location: `src/walkbike_routes/switrs.py:56-69` (spatial geometry validation).
- Trigger: a spatial file has the required crash columns and a CRS but contains LineString or other unsupported non-point geometry.
- Confirmed evidence: a fabricated one-row GeoParquet with CASE_ID=`made-up`, year=2020, severity=`4`, bicycle flag=`Y`, EPSG:4326 and `LineString([(0,0),(1,1)])` is returned by `bike_crashes` with its LineString unchanged. Writing that result as recorded_crashes.parquet and calling `map.recorded_points` raises `ValueError: x attribute access only provided for Point geometries`.
- Violated invariant: the handoff promises a point layer and refusal of sources without points. Checking CRS alone allows invalid spatial input through the adapter, potentially wasting a full routing run before the map fails.
- Requested fix: validate spatial geometry at the adapter boundary and raise a clear ConfigError for unsupported non-point input. Test that a spatial line/polygon source cannot reach map serialization. Keep genuinely empty point-source results working as required by RC1.

### RC3 — P2, blocking: an explicitly empty recorded_crashes setting bypasses the required-file guard

- Location: `src/walkbike_routes/config.py:101`.
- Trigger: the private settings contain `recorded_crashes: {}`.
- Confirmed evidence: with fabricated mappings returned at the YAML-read boundary (no private file created or read), `config.load` accepts that setting and returns `recorded_crashes=None`. The absent-key case also returns None, so an incomplete explicit setting is silently treated as disabled.
- Cause and violated invariant: `if recorded and crashes is None` checks whether the mapping is truthy, rather than whether the key was supplied. The handoff requires crashes whenever recorded_crashes is given.
- Requested fix: distinguish absence of the top-level key from an explicitly empty mapping and refuse the latter with the existing needs-crashes error. Add the empty-mapping case to the required-file checks.

### Verification and limits

- Reviewed the complete eleven-file diff in an independent detached clone with its own Git metadata. RC1–RC3 are confirmed input/validation defects in the new feature; no existing base-version adapter API is claimed.
- Independent fabricated-record integration probe passes for valid data on school, downtown and all-destinations maps in Chromium: grouping, worst-last ordering, year legend, popup, toggling and removal work. All pre-existing Parquet/CSV route/list/summary outputs equal a run without records, and rerunning without records removes the stale layer file.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1047 tests, thirteen deselected, 94.63% coverage. Two dotenv tests were not collected. Deselections cover four existing private-config cases, three forecast cases, and six recorded-crash/private-config cases that read synthetic `.local.yaml` files. Adapter and browser tests using fabricated rows ran. Three pre-existing multithreaded-fork warnings recur.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip/crash records were read. All independent inputs were fabricated; private-setting checks substituted in-memory YAML results. Private real-area evidence was not inspected.

## Handoff, round 2

- Target: `4308084` -> `0bb32b9` (this handoff is in the next commit).
- RC1 fixed: the placed-row filter in `switrs.bike_crashes` is a boolean
  Series on the frame's index, so an empty selection keeps the columns and
  CRS. `test_a_source_with_no_bike_crashes_gives_an_empty_layer` covers an
  empty source, a car-only source, and a CSV whose every point is 0 or
  blank, and `layers.recorded_crashes` on the car-only one.
- RC2 fixed: `bike_crashes` refuses a source with any non-point shape
  ("holds LineString shapes; each crash must be a point") before
  filtering. `test_a_spatial_file_of_lines_is_refused`.
- RC3 fixed: `config.load` refuses `recorded_crashes` whenever the key is
  present without `crashes`, so `{}` gets the needs-crashes error. A
  bare `recorded_crashes:` (null) was and is refused as "expected a
  mapping". Both are now cases in
  `test_the_private_file_is_checked_when_the_config_loads`.
- Each new test fails with the round-1 `src/` and passes on `0bb32b9`.
- `make check`: 1066 passed, 95.76%. `make validation-check`: 58 passed.
