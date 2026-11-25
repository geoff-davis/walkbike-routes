# Review: forecast-path

## Current state

- Status: complete, accepted at `bbad720`
- Next action: none
- Review target: `f75c95b` -> `bbad720`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `forecast-path-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-25T16:07Z`

## Handoff, round 1

- Tier: Standard (adds a guard).
- Asked for by the user: a `crash_forecast` file named in the private file
  but missing should stop the run at once with a clear message.
- Before: `config.load` resolved the path without checking it; the run
  built the street network (minutes on a real area) and then stopped in
  `layers.crash_forecast` with `pyogrio.errors.DataSourceError`.
- Intended behavior and acceptance criteria:
  - `config.load(..., local=True)` raises `ConfigError` naming the private
    file and the resolved path, "not found; fix the path or remove the
    line", when the named forecast is not a file.
  - With `local=False` the private file is not read, so nothing is checked.
  - No forecast line: unchanged (`crash_forecast=None`).
- Changed areas: `config.py` (the private-file branch of `load`),
  `tests/test_crash_forecast.py`.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `make check` | `bbad720` | 1050 passed, 95.77% | none | implementer |
  | `make validation-check` | `bbad720` | 58 passed | none | implementer |

- Condition inventory:

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |
  | Missing file refused at load; ignored with `local=False` | `test_a_missing_forecast_file_is_refused_when_the_config_loads` | caught | fails with the base `config.py` |
  | Present file still loads and draws | existing forecast tests | caught | |

- Settled decisions (do not re-report): refuse rather than warn and drop
  the layer (the user chose this).
- Known limitations: a file that exists but cannot be read as a layer is
  still found only at the forecast step.

## Review, round 1

- Loop: `forecast-path-1`, round 1; Standard tier.
- Target reviewed: `f75c95b..bbad720` (`bbad720207d6620c9c43f09a9f3ac8ab5f97cce1`). The handoff was read from `3fa3336` as requested; implementation checks used `bbad720`.
- Verdict: **accepted** at `bbad720`. No confirmed blocking findings.
- Reviewed the complete two-file diff in an independent detached clone with its own Git metadata. The guard checks the resolved path with `is_file` immediately after reading the private settings, before constructing the study/run network, and reports the private filename, resolved forecast path and requested corrective instruction. `checks.path(None, ...)` preserves the absent/null behavior; `local=False` returns before the private branch.
- Independent probes supply fabricated YAML mappings at the `_read_yaml` boundary and simulate only the companion file's existence, without creating or reading a `.local.yaml` file. Forecast paths are actual temporary filesystem cases. The identical probe on base and head confirms: missing paths and directories are accepted on `f75c95b` but refused on `bbad720` with the full expected message; an existing regular file loads, absent/null settings yield None, and `local=False` never requests the private mapping and leaves the forecast None. The existing-file check validates load-time existence only, not layer readability, as scoped in the handoff.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1041 tests, seven deselected, 94.90% coverage. Two dotenv tests were not collected; four existing private-config tests and three forecast tests that read synthetic `.local.yaml` were deselected to honor file restrictions. The new private-file test is among these exclusions; its guard branches were checked independently as described above. Three pre-existing multithreaded-fork warnings recur.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip/crash records were read. Additional probes use only fabricated in-memory settings and temporary ordinary files/directories outside the repository.
