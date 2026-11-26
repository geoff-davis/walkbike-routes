# Review: nearest-district

## Current state

- Status: complete, accepted at `e66a5f8`
- Next action: none
- Review target: `fd02ba4` -> `e66a5f8` (accepted, round 2)
- Open findings: none (ND1 fixed in e66a5f8)
- Decisions needed from the user: none

## Loop

- Loop ID and round: `nearest-district-1`, round 2 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-26 18:36`

## Handoff, round 1

- Tier: Standard (changed config contract and the home split)
- Intended behavior and acceptance criteria:
  - A destination may have `nearest` together with `serves` or a limiting
    `outline` (public config or the private file's `outlines:`). The
    `serves` plus limiting outline refusal is unchanged.
  - Each member's homes are first cut to its own area by `origins_for`
    (unchanged). In `nearest_origins`, a pooled home is chosen only among
    members whose area holds it (`allowed` mask passed to
    `choose_nearest`), by street distance where any allowed member is
    reachable, else straight line. No home is handed to a member that does
    not count it, so none is lost.
  - Without areas the split is unchanged (every member allowed for every
    pooled home).
  - `destinations.parquet` gains `nearest_group`: the group name, or `""`
    for destinations outside any group (including network-flow job
    clusters).
  - `docs/adapting.md` documents it, and that charter, magnet and private
    schools are left out of groups.
- Changed areas: `config.py` (two guards removed), `config_models.py`
  (docstring), `study_nearest.py` (`allowed` mask, log count),
  `__main__.py` (column on write), `docs/adapting.md`,
  `tests/test_nearest.py`.
- Pattern inventory: `choose_nearest` has one caller; `Destination.nearest`
  is read in `config.py` (group size check) and `study_nearest.py`. Readers
  of `destinations.parquet` (`report/tables.py`, `report/map.py`) select
  columns by name and ignore the new one.
- Verification:

  | Command | Revision | Result | Run by |
  | --- | --- | --- | --- |
  | `make check` | `4463989` | 1078 passed, coverage 95.79% | implementer |
  | `make validation-check` | `4463989` | 58 passed | implementer |
  | `pytest tests/test_nearest.py` with `src/` stashed to base | base src, head tests | 3 failed, 3 passed | implementer |
  | `pytest tests/test_network_flows.py tests/test_nearest.py -W error::Warning` | `4463989` | 23 passed | implementer |

  On base: no `nearest_group` column; `choose_nearest()` takes 2
  arguments; config refuses "give nearest, serves or a limiting outline,
  not more than one".

- Condition inventory:

  | Condition | Test or probe | Result | Note |
  | --- | --- | --- | --- |
  | home nearer a member whose area lacks it | `test_a_home_goes_only_to_members_whose_area_holds_it` | caught | edge -> A |
  | allowed member unreachable by street, straight line used | same | caught | far -> A |
  | no mask: unchanged behaviour | same, last assert | caught | |
  | nearest + limiting outline, nearest + serves accepted | `test_a_nearest_group_may_also_have_an_area` | caught | |
  | group column written, empty outside groups | `test_each_home_goes_only_to_its_nearest_destination` | caught | |
  | job clusters get `""` without a dtype warning | network-flow tests under `-W error` | caught | |
  | end-to-end split with real area files | not exercised | – | the demo town refuses outlines; covered by the unit mask test and the existing end-to-end group test |

- Settled decisions (do not re-report): per-destination area rather than a
  group-level setting (reuses outline `file`/`where`/`name`); charter,
  magnet and private schools are left out by configuration, not code.
- Known limitations and out of scope: a home inside no member's area never
  enters the pool (already the case: it is in no member's origins). The map
  draws the member's area (the district), not the share nearest it.

## Review, round 1

- Loop: `nearest-district-1`, round 1, Standard tier.
- Target: `fd02ba4..4463989` (`44639898f06dd7bb4b11f10c0b92daab2f6b0130`); handoff read from `b1255e8`.
- Verdict: **changes requested at `4463989`**. One confirmed blocking acceptance failure; no hypotheses reported.

### ND1 — P2, blocking: network-flow job clusters omit `nearest_group`

- Location: `src/walkbike_routes/__main__.py:194`–`:200`; the separate writer is `src/walkbike_routes/network_flows.py:98`–`:99`.
- Trigger: build a study with `network_flows` and inspect `network/destinations.parquet`.
- Violated criterion: the handoff explicitly requires `nearest_group` to be the empty string for destinations outside groups, including network-flow job clusters. The new assignment runs only in the root layer-writing loop. Network-flow clusters are generated and written separately, bypassing that assignment entirely.
- Evidence: built the made-up network-flow demo via the existing `tests/test_network_flows.py` config helper. Root `destinations.parquet` had the new column; `network/destinations.parquet` had three job-cluster rows and no `nearest_group` column. Existing network-flow tests do not assert this field, so passing them under warning-as-error does not establish that criterion. This is an incomplete new output contract, not a claimed regression in previously supported metadata.
- Requested fix: write an empty-string `nearest_group` column on job-cluster destinations too, and add a regression assertion against the written `network/destinations.parquet`, including a network-only study. Preserve the current routing behavior.

### Verification and limits

- Independent detached snapshot at the exact target; implementer tree unchanged apart from this review entry.
- `uv sync --locked --offline` passed. `make check` passed: lint, format, types, 1,055 tests passed, 21 deselected, 94.57% coverage. Three existing multiprocessing fork warnings. `make validation-check` passed, 58 tests. `git diff --check fd02ba4..4463989` passed.
- Privacy exclusions: ignored `tests/test_env_file.py`; deselected the 21 parameterized config, forecast and recorded-crash cases that load companion private YAML files. No `.env`, `*.local.yaml`, repository data/output, or real records read. Probes used only synthetic in-memory frames and temporary demo outputs.
- Pooled-origin harness: with A-only, B-only, shared and unreachable homes, head assigned all four exactly once to eligible members. The same harness against base source dropped the two area-exclusive homes. This verifies the new mask construction and filtering, rather than only `choose_nearest` in isolation. Street/network length providers were stubbed with made-up distances; this was not a real-area routing test.
- Additional fallback probe passed: a reachable but disallowed B did not suppress straight-line fallback to allowed A when A had no street route. Existing no-area and group-column tests passed in the suite. Public serves/outline mutual-exclusion guards remain; private-outline guard removal inspected without reading private files.
- No further findings in the area eligibility logic or root destination metadata. Real-area files were not exercised. Snapshot and synthetic probe artifacts removed after the verdict.

## Handoff, round 2

- Target: `fd02ba4` -> `e66a5f8` (fix commit `e66a5f8` on top of `4463989`).
- ND1 fixed: `network_flows.py` writes `network/destinations.parquet` with
  `nearest_group` set to `""` (on the written copy only; the clusters used
  for routing are unchanged).
- Regression test: `test_a_network_only_run_writes_no_maps_and_lists_its_files`
  (a network-only study) now asserts the written cluster file has rows and
  every `nearest_group` is `""`, and that the root `destinations.parquet`
  has the column. With the `network_flows.py` change stashed it fails:
  `AttributeError: 'DataFrame' object has no attribute 'nearest_group'`.
- Checks at `e66a5f8`: `make check` 1078 passed; network-flow and nearest tests
  under `-W error::Warning` 23 passed.

## Review, round 2

- Loop: `nearest-district-1`, round 2, Standard tier.
- Target: `fd02ba4..e66a5f8` (`e66a5f8dfa4759e42e1a197fa219d2431a604dbe`); handoff read from `90f3607`. Fix verification compared `4463989..e66a5f8`.
- Verdict: **accepted at `e66a5f8`**. ND1 resolved; no new findings.
- The separate network-flow destination writer now assigns `nearest_group=""` to its written copy. Routing continues to use the original clusters. The regression assertion checks nonempty job-cluster output and empty-string metadata in a network-only study, plus presence of the root column.
- Independent same-probe comparison: a synthetic network-only build at `4463989` wrote three clusters without the field; at `e66a5f8` all three had the field set to the empty string. The remaining cluster columns, network flows and network summary matched exactly between revisions. The probe ran with warnings treated as errors and emitted none.
- Detached snapshot checks at `e66a5f8`: `uv sync --locked --offline` passed; `make check` passed (lint, format, types; 1,055 tests passed, 21 deselected, 94.57% coverage). Three existing multiprocessing fork warnings in the full suite. `make validation-check` passed, 58 tests. `git diff --check fd02ba4..e66a5f8` passed.
- Privacy exclusions match round 1: `tests/test_env_file.py` ignored; the 21 parameterized config, forecast and recorded-crash cases that load companion private YAML files deselected. No `.env`, `*.local.yaml`, repository data/output, or real records read. All extra probes used temporary synthetic demo outputs.
- Round-1 eligibility-mask and fallback conclusions remain valid; their implementation is unchanged. Implementer tree left unchanged apart from this review entry. Temporary snapshots and probe artifacts removed after the verdict. Acceptance authorizes no commit, push or merge.
