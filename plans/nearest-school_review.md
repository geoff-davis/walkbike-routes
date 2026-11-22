# Review: nearest-school

## Current state

- Status: accepted at 6743e43
- Next action: open the PR
- Review target: `2a25ea2` -> `205d424` (branch `nearest-school`)
- Open findings: none yet
- Decisions needed from the user: none

## Loop

- Loop ID and round: `nearest-school-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T23:18Z`

## Handoff, round 1

- Tier: Standard (new config option and a change to which homes count)
- Goal (the user's request): send high-school students to the closest high school, measured by street distance, when attendance areas are unknown.
- Acceptance criteria:
  - Destinations sharing a `nearest` group split the homes: each home appears in exactly one member's routes, and together the members cover every home they would count alone.
  - Distance is the shortest walk along streets and paths (`walk/shortest`), identical for every mode and preset; homes with no street route fall back to straight-line distance.
  - A group of one, or `nearest` with `serves` or a limiting outline (config or `.local.yaml`), is a config error.
  - Destinations without `nearest` are unchanged.
  - The core stays destination-neutral.
- Evidence: `tests/test_nearest.py` (demo town). Both tests fail on base, where `nearest` is an unknown key.
- Verification: `make check` 1002 passed, 95.71%; `make validation-check` 58 passed.
- Settled decisions (user): street distance rather than per-mode travel time or straight line; one split for every mode.
- Known limitations: ties go to the first member in config order. The split adds one walk/shortest network build per group.

Append "## Review, round 1" with a verdict naming `205d424`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `nearest-school-1`, round 1; Standard tier retained.
- Target reviewed: `2a25ea2` -> `205d424`.
- Verdict: **changes required** at `205d424`: NS1 and NS2 are confirmed defects in the new nearest-group behavior.
- Snapshot: independent local clone detached at the exact target. The implementer's checkout was not modified; this handoff is the only file written there.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy and **1002 tests**, coverage **95.71%**. `make validation-check` passed lint, formatting, mypy and **58 tests**. Chromium tests ran with normal local process permissions.
- Independent probes exercised the actual nearest-group code, mode network, access-point generation and accessibility routing on small made-up metric networks. The connected and disconnected cases below do not depend on downloaded layers or real school locations. The failures violate the new feature's acceptance criteria; no claim is made that nearest-group behavior existed on the base, which does not accept the option.
- Evidence reused: implementer's base rejection/fail-first results and ordinary demo partition test. That partition test verifies disjointness and coverage but does not verify that the selected member is actually closest or exercise disconnected fallback.

### NS1 — P2, blocking: ranking omits the home-to-endpoint street distance

- Location: `src/walkbike_routes/study_nearest.py:92`.
- Trigger: a single bidirectional 1000 m street from node A at x=0 to B at x=1000, a home at x=10, and grouped point destinations at the two endpoints. Put the far destination B first in the config.
- Violated criterion: the chosen member must be nearest by shortest walking distance along streets and paths.
- Confirmed evidence: using the real `mode_network` and `_street_lengths`, both destinations receive `physical_length_m=0` for this home, so `nearest_origins` assigns it to B. Its actual street distances are 990 m to B and 10 m to A. This is not an actual tie. Accessibility routing chooses the appropriate endpoint of the home's attached edge and records the distance walked to that endpoint as `snap_distance_m`; `physical_length_m` measures only the subsequent graph path. Both subsequent paths are empty in this example. Minimizing only that graph portion discards a destination-dependent part of the walk.
- Requested fix: compare the complete shortest-walk distance, including the attached street segment from the home's landing point to the chosen endpoint (and handle the access components consistently). Add a small geometric regression like the above, with the farther destination first, asserting the selected destination rather than partition coverage alone.
- Status: open.

### NS2 — P2, blocking: all-unreachable rows fail before straight-line fallback

- Location: `src/walkbike_routes/study_nearest.py:60`.
- Trigger: a home on the main walking component and both grouped destinations on another component, with valid entrances on that component. All street distances for the home are missing.
- Violated criterion: a home with no street route to any group member must be assigned by straight-line distance rather than aborting the run.
- Confirmed evidence: on a made-up network with main chain A-B-C at x=0/1000/2000 and separate street D-E at x=10000/11000, a home at x=10 and destinations D/E cause the real `nearest_origins` call to raise `ValueError: Encountered all NA values` under the locked pandas 3.1.1. `table.idxmin(axis=1)` is evaluated on the all-NA row before `.where(by_street, ...)` can select the straight-line result. A separate direct probe with absent street-length Series reproduces the same error.
- Requested fix: run street minimization only for rows with at least one known distance, then fill the remaining choices from the straight-line table, avoiding an all-NA reduction. Cover fully disconnected and mixed reachable/unreachable homes, including partition coverage after fallback.
- Status: open.

- Verification limits: source inspection covered the config exclusions and group validation, but no existing private `.local.yaml`, real counts, recorded trips or downloaded city data were read. No additional findings are asserted.

## Handoff, round 2

- Target: `205d424` -> `6743e43`
- NS1 fixed: `route_lengths()` adds `snap_distance_m` to `physical_length_m`. Test `test_the_walk_onto_the_network_counts_toward_nearest` covers your 1,000 m street case: a home 10 m from A, with B listed first, goes to A.
- NS2 fixed: `choose_nearest()` uses street distance only for rows that have some, and straight-line distance for the rest. Test `test_homes_no_street_reaches_go_to_the_nearest_in_a_straight_line` mixes a partly reachable home and a cut-off one.
- Both tests fail at `205d424`, where the helpers do not exist.
- `make check`: 1004 passed, 95.70%. `make validation-check`: 58 passed.

Append "## Review, round 2" with a verdict naming `6743e43`, then send one prompt to walkbike-implementer.

## Review, round 2

- Loop: `nearest-school-1`, round 2; Standard tier retained.
- Target reviewed: `205d424` -> `6743e43fc6be4f01063ec9219d135ceb70f97070`.
- Verdict: **accepted** at `6743e43`. NS1 and NS2 are verified fixed; no open findings or new confirmed defects.
- Snapshot: independent local clone detached at the exact target, with a separate `205d424` clone for identical geometric probes. The implementer's checkout was not modified; this handoff is the only file written there.
- NS1 verification: reran the actual nearest-group routing on the 1000 m A-B street with a home at x=10 and far B listed first. The base reports 0 m to both and assigns B; the head reports 990 m to B and 10 m to A and assigns A. This independently verifies integration of the added snap distance, beyond the helper-level regression test.
- NS2 verification: reran the actual disconnected-component network from round 1. The base raises `ValueError: Encountered all NA values`; the head assigns the home to the straight-line-nearest member and preserves partition coverage. A mixed table probe also confirms that a home with any reachable member uses street distance even when an unreachable member is closer in a straight line, while a fully cut-off home uses fallback.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy and **1004 tests**, coverage **95.70%**. `make validation-check` passed lint, formatting, mypy and **58 tests**. Chromium tests ran with normal local process permissions.
- Fix assessment: whole-route lengths include the home-to-node walk, and street minima are computed only for rows with a known street distance, avoiding the all-missing reduction. The shared assignment remains independent of the study's requested mode and route-choice preset.
- Evidence reused: implementer's new helper tests and reported check results. The reviewer independently reproduced both check targets and both base/head network probes.
- Verification limits: network probes used made-up projected coordinates and canonical layers; no downloaded city data, private local configuration, counts or recorded trips were inspected. No further review round requested.
