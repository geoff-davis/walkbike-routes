# Review: district-outlines

## Current state

- Status: complete, accepted at `a911b7c`
- Next action: none
- Review target: `d90f390` -> `a911b7c` (accepted, round 1)
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `district-outlines-1`, round 1 of at most 1
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-26 20:06`

## Handoff, round 1

- Tier: Small (one public config file; no code, no guard changed)
- Intended behavior and acceptance criteria:
  - The two `nearest: high` destinations in `config/los_altos_schools.yaml`
    each get a limiting `outline` from the Census TIGER/Line 2025 secondary
    school districts file (public domain), filtered to one district by
    `NAME`, so the feature merged in #39 applies: each home in the district
    goes to the nearer high school by street.
  - The file path is relative to the config (`../data/raw/tiger/...`), in
    the git-ignored data folder. The header comment says how to download it.
  - No data file is committed.
- Changed areas: `config/los_altos_schools.yaml` only.
- Verification:

  | Command | Revision | Result | Run by |
  | --- | --- | --- | --- |
  | `make check` | `a911b7c` | 1078 passed | implementer |
  | `config.parse` of the public config | `a911b7c` | both members have `nearest` and an outline | implementer |
  | full run with the same outline (given privately) on the #39 code | `e66a5f8` | district homes split between the two members, all by street | implementer |

- Settled decisions (do not re-report): the district file is downloaded by
  hand (no automatic fetch); the study area itself is unchanged (issue #40).
- Known limitations and out of scope: a run without the downloaded file
  stops at the input-file check; the parse step does not check existence,
  matching every other outline.

## Review, round 1

- Loop: `district-outlines-1`, round 1, Small tier.
- Target: `d90f390..a911b7c` (`a911b7c2d733272931175b0222b14e260716eb0e`); handoff read from `81b6c13`.
- Verdict: **accepted at `a911b7c`**. No findings.
- Both `nearest: high` members (`lahs`, `mvhs`) parse with limiting outlines, the same district NAME filter, credit and config-relative shapefile path. A synthetic two-polygon probe of the actual outline selector kept only the matching district polygon. Removing the two added outline entries makes the parsed YAML exactly equal to the base; the study area and other settings are unchanged. Only the public config changed; no data file was committed.
- Public-source verification: the [Census TIGER/Line 2025 secondary-district directory](https://www2.census.gov/geo/tiger/TIGER2025/SCSD/) lists `tl_2025_06_scsd.zip`; the [Census 2025 California secondary-district table](https://tigerweb.geo.census.gov/tigerwebmain/Files/acs25/tigerweb_acs25_sdsec_ca.html) lists the exact configured NAME, `Mountain View-Los Altos Union School District` (GEOID 0626310). Download instructions point to that archive and the expected extraction folder.
- Independent detached snapshot checks: `uv sync --locked --offline` and `make check` passed (lint, format, types; 1,055 tests passed, 21 deselected, 94.59% coverage). Three existing multiprocessing fork warnings. `git diff --check d90f390..a911b7c` passed. Validation checks not repeated for this config-only change.
- Privacy exclusions: `tests/test_env_file.py` ignored; the 21 parameterized config, forecast and recorded-crash cases that load companion private YAML files deselected. No `.env`, `*.local.yaml`, repository data/output, or real records read. Actual boundary files and the implementer's private run were not inspected; selector behavior was exercised with made-up polygons, and source naming/archive availability verified from public Census HTML.
- Implementer tree left unchanged apart from this review entry. Review snapshot removed after the verdict. Acceptance authorizes no commit, push or merge.
