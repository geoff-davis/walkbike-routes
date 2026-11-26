# Review: district-outlines

## Current state

- Status: ready for reviewer
- Next action: reviewer reviews round 1
- Review target: `d90f390` -> `a911b7c`
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
