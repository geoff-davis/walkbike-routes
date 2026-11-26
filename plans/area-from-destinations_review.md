# Review: area-from-destinations

## Current state

- Status: ready for reviewer
- Next action: reviewer verifies round 2
- Review target: `1eb88e1` -> `0e832b4`
- Open findings: AFD1 (fixed in 0e832b4, to verify)
- Decisions needed from the user: none

## Loop

- Loop ID and round: `area-from-destinations-1`, round 2 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-26 21:16`

## Handoff, round 1

- Tier: Standard (new config option, changes what a run downloads)
- Part two of issue #40 (part one, per-destination wording, is #43).
- Intended behavior and acceptance criteria:
  - `area.source: destinations` is accepted; `place` and `boundary_file`
    are refused with it ("does not apply"), as for the other sources; a
    study with no destinations (network flows only) is refused.
  - `layers.destination_areas(study)` gives one shape per destination, in
    `study.crs` or the UTM zone of the destinations: its limiting outline,
    else its `serves` area, else its location (or its `boundary_file` for
    an area destination) buffered by `layers.reach_m(study)`, the longer of
    the walk and bike time limits at the default 3 and 10 mph. A drawn-only
    outline (`limits: false`) counts as no area.
  - `download.area.resolve(area, fetcher, crs, shapes)` uses those shapes
    as the boundary for `source: destinations` (refusing without them), then
    projects and buffers by `buffer_m` exactly as for the other sources.
  - `layers.build` passes the shapes only for that source. Other sources
    are unchanged (`shapes` defaults to None).
  - `docs/adapting.md` documents it.
- Changed areas: `config.py`, `config_models.py`, `download/area.py`,
  `layers.py`, `docs/adapting.md`, `tests/test_osm_streets.py`.
- Verification:

  | Command | Revision | Result | Run by |
  | --- | --- | --- | --- |
  | `make check` | `0b643c4` | 1082 passed | implementer |
  | `make validation-check` | `0b643c4` | 58 passed | implementer |
  | the 2 new tests with `src/` at base | base src, head tests | 2 failed: no `reach_m`; "'destinations' is not one of demo, place, boundary_file" | implementer |
  | a private config's areas computed with the new source (no run) | `0b643c4` | every destination had its own area; the derived extent was about 20% smaller than the place plus buffer and covered all of it | implementer |

- Condition inventory:

  | Condition | Test | Result |
  | --- | --- | --- |
  | limiting outline used as is | `test_an_area_from_the_destinations_is_their_areas_and_reach` | caught |
  | no area: boundary buffered by reach, not more | same | caught |
  | reach is the longer of walk and bike | same | caught |
  | full `layers.build` with fake public services | same | caught |
  | `resolve` refuses without shapes; extent covers shapes plus buffer | same | caught |
  | `place` refused; no destinations refused | `test_an_area_from_the_destinations_takes_no_place_and_needs_destinations` | caught |
  | `serves` area used (same branch shape as the outline case) | not exercised directly | – |

- Settled decisions (do not re-report): reach uses the default speeds, not
  per-preset speeds (a straight-line upper bound for routes along streets);
  `buffer_m` is reused as the routing margin rather than a new key;
  existing configs keep their source until a later config change.
- Known limitations and out of scope: the public schools config still uses
  `source: place`; switching it is a separate change once a full run has
  been checked. Error messages in the download code still say "study area".

## Review, round 1

- Loop: `area-from-destinations-1`, round 1, Standard tier.
- Target: `1eb88e1..0b643c4` (`0b643c4f8649b14793cf1e80d7f0f67252a9a487`); handoff read from corrected commit `5c8f0b8`.
- Verdict: **changes requested at `0b643c4`**. One confirmed blocking acceptance failure; no hypotheses reported.

### AFD1 — P2, blocking: fully bounded car-only studies fail while computing unused reach

- Location: `src/walkbike_routes/layers.py:113` (and `reach_m` at `:97`).
- Trigger: an accepted `area.source: destinations` config with `modes: [car]`, `presets: {car: [central]}`, `time_caps_min: {}`, a point destination with `serves`, and a projected CRS. Every destination already has its own area, so no fallback reach is needed.
- Violated criterion: `destination_areas` should use each destination's limiting outline or serves polygon as its shape. Instead it computes walk/bike reach unconditionally before inspecting destinations. With no walking or cycling cap, `max()` receives no values and aborts even this fully bounded case.
- Evidence: a synthetic config with one served destination passed `config.parse`. With the polygon reader stubbed to return a made-up square, `layers.destination_areas(study)` raised `ValueError: max() iterable argument is empty` before reading the square. No actual file or download was involved. This is a failure of the new source's accepted configuration, not a claimed regression of existing sources.
- Requested fix: defer reach computation until a destination actually needs a fallback buffer, so bounded car-only studies work. If a fallback is required but there are no walk/bike limits, handle that configuration explicitly with an actionable config error or documented supported behavior rather than an uncaught `ValueError`. Add a regression case for a car-only destination with its own area.

### Verification and limits

- Independent detached snapshot at the exact target; implementation tree unchanged apart from this review entry.
- `uv sync --locked --offline` and `make check` passed: lint, format, types; 1,059 tests passed, 21 deselected, 94.61% coverage. Three existing multiprocessing fork warnings. `make validation-check` passed, 58 tests. `git diff --check 1eb88e1..0b643c4` passed.
- Additional synthetic branch probes passed for serves polygons, limiting outlines, drawn-only outlines falling back to reach, plain points, walking-only reach and automatic UTM selection. The suite's new fake-service build/resolve tests passed. Default-speed and buffer-margin decisions were respected.
- Privacy exclusions: `tests/test_env_file.py` ignored; the 21 parameterized config, forecast and recorded-crash cases that load companion private YAML files deselected. No `.env`, `*.local.yaml`, repository data/output, or real records read. Private extent evidence was not inspected; probes used made-up geometries and fake services only.
- No further findings. Review snapshot removed after the verdict.

## Handoff, round 2

- Target: `1eb88e1` -> `0e832b4` (fix commit `0e832b4` on top of `5c8f0b8`).
- AFD1 fixed: `reach_m` returns None when the study has neither a walking
  nor a cycling time limit, and `destination_areas` calls it only for a
  destination with no area of its own. Such a destination in a car-only
  study gets a ConfigError naming it ("has no area of its own, and with no
  walking or cycling time limit nothing bounds it; …").
- Regression test: `test_a_car_only_area_from_the_destinations_needs_each_to_have_an_area`
  (car-only, empty `time_caps_min`): with an outline and a `serves` area
  the shapes are built, the `serves` one equal to its file; without the
  second area, the ConfigError. On the round-1 source it fails with
  `ValueError: max() iterable argument is empty`. It also covers the
  `serves` branch that round 1 left unexercised.
- Checks at `0e832b4`: `make check` 1083 passed.
