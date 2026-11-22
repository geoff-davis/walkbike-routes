# Review: los-altos-all-schools

## Current state

- Status: accepted at 6a677ba
- Review target: `1ac3cf4` -> `6a677ba` (branch `los-altos-all-schools`)
- Open findings: none yet

## Loop

- Loop ID and round: `los-altos-all-schools-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T23:45Z`

## Handoff, round 1

- Tier: Small (one public study config; no code, guard or contract changes)
- Goal (the user's request): run every Los Altos School District elementary and junior high school within its attendance area, and send high-school students to the nearest of Los Altos High and Mountain View High by street.
- Acceptance criteria:
  - The config loads; the new schools have the right age groups (TK to grade 6: 4 to 11; grades 7 and 8: 12 to 13; grades 9 to 12: 14 to 17).
  - `lahs` and `mvhs` share `nearest: high`, and neither has `serves` or a limiting outline.
  - The header says plainly which entrances are reviewed and which are provisional (OpenStreetMap gates or school points), and that attendance boundaries are not distributed.
  - No attendance boundaries, private paths or local layers are committed.
- Evidence: `make check` 1004 passed, 95.70%; `make validation-check` 58 passed. The config loads with the private outlines; the private file is git-ignored and not in the commit.
- Settled decisions (user): start new schools' entrances from OpenStreetMap gates; nearest high school by street distance.
- Known limitations: six schools have no mapped gates and use the school point until the user reviews them. Do not read `*.local.yaml`.

Append "## Review, round 1" with a verdict naming `6a677ba`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `los-altos-all-schools-1`, round 1; Small tier retained.
- Target reviewed: `1ac3cf4` -> `6a677ba0e9872fa26a8bbb1bfab2254fc0d2caba`.
- Verdict: **accepted** at `6a677ba`. No confirmed correctness defects or open findings in the committed public config.
- Snapshot: independent local clone detached at the exact target. The implementer's checkout was not modified; this handoff is the only file written there. The config probe explicitly used `config.load(..., local=False)`; no developer credential files or private attendance layers were inspected.
- Public config checks passed: seven elementary schools have ages 4–11; Egan and Blach have ages 12–13; LAHS and MVHS have ages 14–17. Every destination has school purpose. Exactly LAHS/MVHS share nearest group high, with no serves or limiting outline. No local layers appear in the public-only parsed study.
- The district's [official school directory](https://www.lasdschools.org/268356_2) confirms the seven elementary and two junior-high campuses in the config. The header distinguishes the three previously reviewed campuses from provisional OpenStreetMap gates and the six school-point fallbacks, and explains that attendance boundaries are not distributed.
- Diff inventory confirms the sole committed change is `config/los_altos_schools.yaml`: no boundary geometry, private layer, credential file or private absolute path was added. Existing reviewed gates are unchanged.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` ran with `PYTEST_ADDOPTS='--ignore=tests/test_env_file.py'`: lint, formatting, mypy and **1002 tests** passed, coverage **95.46%**. The two tests that open synthetic dotenv files were excluded. Other core tests used their made-up fixtures, including synthetic local-config tests. Browser tests ran with normal local process permissions.
- Evidence reused: implementer's unfiltered core check (1004 tests) and validation check (58 tests). Validation checks were not repeated for this public-YAML-only change.
- Verification limits: the private outlines were deliberately not inspected, so correct attendance-area coverage in a private run is not independently verified. The public config alone contains no attendance limits for elementary/junior-high schools; its header instructs adding them. No live map download, school-gate survey, or real-data study was run. Gate provenance/review status is taken from the handoff and preserved comments. No further review round requested.
