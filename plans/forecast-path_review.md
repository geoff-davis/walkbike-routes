# Review: forecast-path

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
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
