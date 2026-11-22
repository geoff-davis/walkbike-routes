# Review: nearest-school

## Current state

- Status: ready for reviewer
- Next action: walkbike-reviewer reviews round 1
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
