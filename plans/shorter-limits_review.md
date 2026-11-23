# Review: shorter-limits

## Current state

- Status: complete
- Next action: open the PR
- Review target: `2ba76a1` -> `6820c60`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `shorter-limits-1`, round 2 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-23T17:31Z`

## Handoff, round 1

- Tier: Standard (new run outputs, a new config setting, map behaviour).
- Intended behavior and acceptance criteria:
  - The user asked for a map toggle that limits trips to 10 or 15 minutes.
  - New optional study setting `shorter_limits_min` (list of minutes,
    increasing, each below both the walk and bike `time_caps_min`).
  - For each shorter limit below a walk or bike run's cap, the run splits the
    trips again from the same single-best routes and near-best alternatives:
    `within_time_limit` recomputed at the shorter limit (same rule as
    `routing/routes.py`), decay weights reapplied, `split_routes` run again
    (it drops alternatives over the origin's limit, so the next near-best
    route can take a dropped one's place). It writes `route_shares`, `flows`
    and a one-row `summary.csv` in `<run>/limit-<minutes>/`.
  - The main preset's exposure lists are written there too, and the
    all-destinations lists in `all-destinations/<run>/limit-<minutes>/`.
  - **Exactness:** at a shorter limit the outputs equal a run made at that
    limit: route shares, flows, both exposure lists, the summary figures, and
    the all-destinations lists. The full-limit outputs are unchanged.
  - Screens (barriers) are not recomputed at a shorter limit; the map says so.
  - Map: a "Time limit" select beside the route model, hidden when the mode
    has no shorter limit (driving). Each limit's view is its own entry in
    `D.runs` (id `<mode>-<preset>@<minutes>`, `limit`, `agree` key) with its
    flows, travel-time squares (same routes; the page hides squares past the
    view's cap), exposure lists and summary. Switching keeps the route model.
    Model agreement and the "under each route model" table compare runs at
    the same limit; the street popup lists runs at the current limit. A note
    in the panel says trips are cut and that barriers are at the full limit.
  - The address-bar state restores a limit view.
- Changed areas: `config.py`, `config_models.py` (setting and validation);
  `study_active.py` (split again per limit); `study.py` (exposure per limit,
  `together` keyed by mode and limit); `study_context.py` (`limit_folder`);
  `report/map.py` (`Run.limit`, `limit_folders`, `_limit_views`, combined
  page views); `report/map.html` (switch); `docs/adapting.md`; both Los Altos
  configs (`[10]` for downtown with a 15-minute cap, `[10, 15]` for schools
  with a 20-minute cap).
- Not changed: the report page (`report/tables.py`) reads only top-level run
  files; a `limit-*` folder is ignored there. The manifest lists the new
  files, and the map reads only listed ones.
- Pattern inventory: consumers of `together` (`_combined` only); consumers of
  `D.agreement` and of `D.runs` filtered by mode in the page (agreement,
  edge popup, the per-model table, run select) all now also match the limit.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `uv run pytest tests/test_shorter_limits.py` | `2ba76a1` | 5 passed | none | implementer |
  | `uv run pytest tests/test_map_page.py -k shorter_time` | base `map.html`, new test | 1 failed (`Cannot read properties of null`) | none | implementer |
  | `uv run pytest tests/test_map_page.py` | `2ba76a1` | 6 passed | none | implementer |
  | `make check` | `2ba76a1` | 1019 passed, 95.75% | none | implementer |
  | `make validation-check` | `2ba76a1` | 58 passed | none | implementer |

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Shorter-limit shares, flows, lists equal a run at that limit | `test_the_shorter_limit_matches_a_run_at_that_limit` (2 destinations × walk, bike) | caught | demo town, 5 of 20 minutes |
  | Summary figures equal | same test | caught | within, median, cap, expected, routes per home |
  | All-destinations lists equal | `test_the_lists_for_every_destination_match_at_the_shorter_limit` | caught | |
  | Full limit unchanged | `test_the_full_limit_is_unchanged_by_a_shorter_one` | caught | |
  | Limit not below a cap, not increasing, not a list | `test_shorter_limits_must_increase_and_stay_under_both_active_limits` | caught | |
  | Switch, same preset, flows cut, note, no barriers, back, hidden for car | `test_the_map_switches_to_a_shorter_time_limit` (every page) | caught | |
  | Every mode × limit × layer draws without a script error | `test_every_map_draws_each_mode_and_layer_without_a_script_error` | caught | fixture now has a 5-minute view |
  | Address-bar restore of a limit view | not exercised by a test | not exercised | code path: `hr` sets mode, limit, run |

- Settled decisions (do not re-report):
  - Computed during the run, not at report time: the run saves only the
    chosen routes (up to 3 of 12 candidates), so rebuilding from saved
    shares would not match a run at the shorter limit. Existing runs need a
    rerun to get the switch.
  - Screens stay at the full limit (they are most of a run's time).
  - The shorter limit must be below both walk and bike limits.
- Known limitations and out of scope: the report page shows only the full
  limit; the travel-time squares are sent once per view (some page size).

## Review, round 1

- Loop: `shorter-limits-1`, round 1; Standard tier.
- Target reviewed: `9b6150e..2ba76a1` (`2ba76a1a25d0980b720d4d50cc0c6ceb59435ce1`).
- Verdict: **changes required** for `2ba76a1`.

### SL1 — P2, blocking: the shorter view still draws full-limit car overlap

- Location: `src/walkbike_routes/report/map.html:197-208`, with the new limit views assembled in `src/walkbike_routes/report/map.py:400-415`.
- Trigger: enable Car overlap on a destination map and select a shorter time limit (or switch limits with the overlay already enabled).
- Defect: the new view changes the flow run but the overlay remains the destination-wide `D.overlap`, calculated from the full-limit walking and cycling flows. The draw loop and visibility test never check the selected limit. It therefore marks streets using trips the selected view excludes. Barriers have an explicit full-limit exception and are hidden in shorter views; overlap has neither that treatment nor a full-limit label.
- Confirmed evidence, using only the made-up demo: with the normal 20-minute run and `shorter_limits_min=(5.0,)`, the school map retains 17 overlap tiers while recomputing overlap from its five-minute walking/biking flows gives 10; downtown retains 15 instead of 14. The identical standalone five-minute control build on both base `9b6150e` and head `2ba76a1` produces 10 and 14 respectively. This isolates the mismatch to the new shorter-view integration. A Chromium probe at the valid 0.1-minute shorter limit confirms both active modes have zero flow yet the school map draws all 17 full-limit overlap polylines (downtown draws 15).
- Violated invariant: a time-limit view must not silently display an active-trip overlay from a different limit. This defeats the map-toggle goal, rather than the settled decision about barriers.
- Requested fix: supply overlap computed from the shorter active flows, or suppress the overlap overlay, toggle and legend on shorter views. Add a browser regression that actually enables `showoverlap` and checks that excluded trips cannot leave an unqualified full-limit overlay behind.

### Verification and limits

- Reviewed the complete committed diff in an independent detached clone with its own Git metadata; used a separate detached base clone for the same control probe.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1013 tests, four deselected, 95.07% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions. The committed shorter-limit equivalence and browser tests ran.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- Independent Chromium URL-restoration probes pass for `walk-broach-2016@5` on the school, downtown and all-destinations pages. Multiple shorter limits and a zero-trip limit load without script errors.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. Probes and builds used only synthetic demo inputs outside the repository. No other blocking findings.

## Handoff, round 2

- Target: `2ba76a1` -> `6820c60` (whole slice `9b6150e` -> `6820c60`).
- SL1 fixed:
  - `study.py`: with car and walking or cycling, the run also writes
    `<destination>/limit-<minutes>/overlap.parquet` from the main walking and
    cycling flows cut at that limit and the same car flows.
  - `report/map.py`: `D.overlap` is now keyed by limit (`""` for the full
    limit, else the minutes as `f"{m:g}"`, which matches the page's
    `String(limit)`); the combined page sends `{}`.
  - `map.html`: `overlapOf(r)` picks the view's overlap for the overlay, its
    toggle and its legend.
  - Tests: `test_the_car_overlap_matches_at_the_shorter_limit` (school and
    downtown, equal to a run at 5 minutes); the browser test
    `test_the_map_switches_to_a_shorter_time_limit` now turns on
    `showoverlap` at 5 minutes and checks the lines drawn equal the 5-minute
    tiers and differ from the full ones. On the round-1 source these fail
    (missing `limit-5/overlap.parquet`; `overlap drawn 17, at 5 min 2`).
  - `tests/test_report.py`: an old-run check now expects `{}`.
- Also in this commit, asked by the user: "Fix first and watch" starts
  unchecked. A report link naming a place (`#lat,lon,zoom,run,tag`) turns it
  on before the first draw, so the place's popup still opens. Test:
  `test_fix_first_starts_off_unless_a_link_names_one_of_its_places` (fails on
  the round-1 source: `on`).
- Verification: `make check` at `6820c60`: 1022 passed, 95.75%;
  `make validation-check`: 58 passed.

## Review, round 2

- Loop: `shorter-limits-1`, round 2; Standard tier.
- Target reviewed: `2ba76a1..6820c60` (`6820c601d88a7b348c57270c7ee3a2fd7be2a6af`).
- Verdict: **accepted** at `6820c60`. SL1 is resolved; no new blocking findings.
- Reviewed the full six-file fix/default-change diff in an independent detached clone with its own Git metadata. Shorter overlap files are computed from the main active flows at that limit and the unchanged car flows, included in the manifest, read into limit-keyed map data, and selected consistently for drawing, toggle visibility and the legend. The committed equivalence checks pass for both destinations.
- Ran identical independent Chromium probes on base and head using synthetic maps with 0.1-, 5- and 10-minute views. The base fails because its five-minute overlap is unchanged and exposure starts enabled. The head passes on the school, downtown and combined pages: five-minute overlap changes, the zero-active-flow view has no overlap polylines/toggle/legend, and switching back restores the full overlap.
- Verified the user-requested default change: Fix first and watch starts unchecked on all three pages. Separate report-link probes on all three pages confirm that a named place enables the layer and actually opens its popup.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1016 tests, four deselected, 95.08% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions. Browser tests ran.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. All additional builds/probes used the made-up demo outside the repository. The round-1 documented limitations remain.
