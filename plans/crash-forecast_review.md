# Review: crash-forecast

## Current state

- Status: complete, accepted at `eadf908`
- Next action: none
- Review target: `ec5e685` -> `eadf908`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `crash-forecast-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-25T03:25Z`

## Handoff, round 1

- Tier: Standard (new config key, run output and map layer; a private
  input).
- Asked for by the user: show another project's intersection crash
  forecast (bike-safety-ca's `intersection_ranking.geojson`) as a toggle
  layer on the maps, without changing any ranking.
- Intended behavior and acceptance criteria:
  - The private `.local.yaml` may hold `crash_forecast: <path>` (relative
    to that file). The main config may not (its keys are unchanged).
    `Study.crash_forecast` holds the path.
  - `layers.crash_forecast(study, crs, bounds)`: reads the file; refuses one
    without `rank` or `expected_bike_crashes`, or without a CRS; keeps
    points intersecting `bounds` (the streets' extent) with a numeric
    `expected_bike_crashes`; sorts by expected crashes, then rank. Returns
    an empty frame when no file is named.
  - `__main__.build` writes `crash_forecast.parquet` and lists it in the
    manifest, or removes an earlier run's file when there is none (as for
    `outlines.parquet`).
  - `map.build_maps` passes the points to every page as `D.crash`. The page
    draws them in a `crash` pane below the fix-first markers, toggled by
    `showcrash` (unchecked, hidden when there are none), with a popup
    (rank, streets, expected and recorded crashes), a legend entry, and the
    print title. Nothing else reads the file.
- Changed areas: `config.py`, `config_models.py`, `layers.py`,
  `__main__.py`, `report/map.py`, `report/map.html`, `README.md`, tests.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `make check` | `2b179fb` | 1048 passed, 95.77% | none | implementer |
  | `make validation-check` | `2b179fb` | 58 passed | none | implementer |

  On a private real-area run (not readable by the reviewer), a copy of the
  outputs with the real forecast added: 4,199 corners within the streets,
  the map's exercise script passed in headless Chrome with the layer on,
  and a screenshot showed the layer under the fix-first markers.

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Forecast drawn on the map, most expected first; far corner and one without a forecast left out | `test_the_forecast_is_drawn_on_each_map_and_only_inside_the_streets` | caught | made-up corners on the demo town |
  | No file: no parquet (old one removed), empty layer | `test_without_a_forecast_the_layer_is_empty_and_an_old_file_is_removed` | caught | |
  | Missing column refused | `test_a_forecast_without_its_columns_or_coordinates_is_refused` | caught | |
  | Toggle, legend, popup and removal in a browser | `test_the_crash_forecast_layer_toggles_with_a_legend_and_popups` | caught | headless Chrome |
  | Private-file key list | `test_a_private_local_file_may_hold_only_local_layers` (message updated) | caught | |

- Settled decisions (do not re-report): display only, no ranking change
  (the user chose this); every corner in the streets' extent is drawn (a
  top-500 cap kept mostly corners outside the study city); read only from
  the private file, since it is derived from crash records.
- Known limitations: no matching to the fix-first places yet; the
  forecast's years are whatever the source used, and the popup says so.

## Review, round 1

- Loop: `crash-forecast-1`, round 1; Standard tier.
- Target reviewed: `ec5e685..2b179fb` (`2b179fb216a0a6d727eae6b86f287e3dac848de9`). The handoff was read from `5b857bf` as requested; implementation checks used `2b179fb`.
- Verdict: **changes required** for `2b179fb`.

### CF1 — P2, blocking: the public config accepts and silently ignores the private-only key

- Location: `src/walkbike_routes/config_models.py:214`, interacting with the dataclass-derived public key list in `config.py:153`.
- Trigger: put `crash_forecast: invented-forecast.geojson` in the main study YAML, or pass the equivalent mapping to `config.parse`.
- Defect: adding the field to `Study` automatically permits it in `checks.keys(raw, Study, ...)`. `parse` excludes `source` and `local_source` but not `crash_forecast`, and its Study constructor never assigns the supplied forecast. The setting is accepted without an error and discarded, leaving `Study.crash_forecast=None`. The requested private-file-only contract and unchanged public key list are therefore not enforced; a misplaced setting quietly produces no layer.
- Confirmed by the identical raw-demo-config probe on base and head (no forecast file opened): `config.parse(raw | {"crash_forecast": "invented-forecast.geojson"})` raises `ConfigError: study config: unknown setting 'crash_forecast'` on `ec5e685`, but returns successfully with `crash_forecast=None` on `2b179fb`.
- Requested fix: explicitly exclude the new internal/private-only field from the public parser's allowed keys, retaining assignment through the private-load path. Add a regression asserting that a public config/mapping with this key is refused, including when local loading is disabled. This test can use an ordinary public YAML or raw mapping without opening a `.local.yaml` file.

### Verification and limits

- Reviewed the complete ten-file diff in an independent detached clone with its own Git metadata; used a separate detached base clone for the identical parser probe.
- Independent fabricated-forecast probe passes on school, downtown and all-destinations pages in Chromium: toggle, legend, popup and removal work; retained points have the expected ordering. All pre-existing Parquet and CSV route/list/summary outputs match a run without the forecast. Rerunning without it removes the old forecast file and manifest entry. This uses direct replacement of the Study field and does not read private YAML.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1040 tests, six deselected, 94.94% coverage. Two dotenv tests were not collected; four existing private-config tests and two new forecast tests that read synthetic `.local.yaml` were deselected to honor file restrictions. Three pre-existing multithreaded-fork warnings recur. The new browser test and missing-column test ran.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip/crash records were read. Additional builds used only the made-up demo and fabricated forecast points outside the repository. Private real-area evidence was not inspected. No other blocking findings.

## Handoff, round 2

- Target: `2b179fb` -> `eadf908` (this handoff is in the next commit).
- CF1 fixed: `config.parse` passes `skip={"source", "local_source",
  "crash_forecast"}` to `checks.keys`, so the main config refuses the key
  as an unknown setting, as on the base. The private path still sets it
  through `replace` in `config.load`.
- Regression: `test_the_main_config_may_not_name_a_crash_forecast` refuses
  it through `config.parse` on a raw mapping, and through `config.load`
  with `local=True` and `local=False`, on a public YAML in a temporary
  folder with no private file. It fails with the round-1 `config.py`.
- `make check`: 1049 passed, 95.77%. `make validation-check`: 58 passed.

## Review, round 2

- Loop: `crash-forecast-1`, round 2; Standard tier.
- Target reviewed: `2b179fb..eadf908` (`eadf9083d1f9b2f2f86503473f6a3b5e8f45d78e`). The round-2 handoff was read from `ecf8a4b` as requested; implementation checks used `eadf908`.
- Verdict: **accepted** at `eadf908`. CF1 is resolved; no new blocking findings.
- Reviewed the complete parser/test fix in an independent detached clone with its own Git metadata. `crash_forecast` is now explicitly excluded from the dataclass-derived public key list. The private loader's existing post-parse assignment through `replace` is unchanged.
- Independently ran the new regression against base and head. It fails on `2b179fb` because no ConfigError is raised. At `eadf908`, all three paths refuse the public key: raw `config.parse`, and public-file `config.load` with local loading enabled or disabled. The test uses ordinary temporary YAML with no private file, so no `.local.yaml` contents are read.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1041 tests, six deselected, 94.94% coverage. Two dotenv tests were not collected; four existing private-config tests and two forecast tests that read synthetic `.local.yaml` were deselected to honor file restrictions. Three pre-existing multithreaded-fork warnings recur. The new public-key regression and browser tests ran.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip/crash records were read. Private-input loading was reviewed in source rather than exercised against a private file; round-1 fabricated-forecast display/equivalence evidence remains applicable to the unchanged layer code.
