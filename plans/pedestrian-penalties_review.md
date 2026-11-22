# Review: pedestrian-penalties

## Current state

- Status: complete (accepted at cf09283; PP1 fixed in 3d834c1, docs only)
- Next action: none; PR with Review line
- Review target: `4432511` -> `cf09283` (branch `pedestrian-penalties`)
- Open findings: none (PP1 fixed in 3d834c1)
- Decisions needed from the user: none

## Loop

- Loop ID and round: `pedestrian-penalties-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T19:09Z`

## Handoff, round 1

- Tier: Standard (changes link-inclusion logic; removes a guard that excluded links)
- Issue: #8
- Intended behavior and acceptance criteria:
  - `PedestrianProfile` no longer has `allow_unknown_local`, `allow_unknown_collector`, `allow_unknown_major` or `allow_problematic`; `evidence_factor` never returns `None`.
  - Every walkable link is kept by `build_pedestrian_links`. Unknown and problematic evidence both cost the road class's `unknown_*_factor`, which is what `allow_problematic=True` did before.
  - Links with `ped_allowed` false, or `crossing=no` crossing links, are **still left out** (access, not evidence; checked before `evidence_factor`).
  - Shipped presets' results are unchanged: `walk/shortest` already allowed everything with all factors 1; `WalkBroachProfile.timing()` already allowed everything.
- Evidence: the four rewritten tests fail on the base source and pass on the head (source stashed, tests from head):
  - `test_presets::test_no_preset_walks_across_where_crossing_is_not_allowed`: base drops the unmarked crossing (`{w,z}` vs `{w,y,z}`).
  - `test_core_network::test_pedestrian_links_keep_unknown_and_poor_evidence_at_a_cost`: base drops the problematic link.
  - `test_core_network::test_unknown_sidewalk_collectors_cost_their_own_factor`: base drops unknown-evidence collectors.
  - `test_core_demo_routing::test_walkers_avoid_the_arterial_stretch_with_no_sidewalk_but_may_use_it`: base drops the no-sidewalk arterial stretch; head keeps it at x5 and routes still avoid it.
  - Deleted: `test_the_new_switch_does_not_move_positional_settings` (only tested the removed switch's position).
- Verification: `make check` 990 passed, 95.74%; `make validation-check` 58 passed; hooks passed.
- Settled decisions (do not re-report): penalties never bans (project rule); poor evidence costs the same factor as unknown evidence (no new judgment value added); the signal-wait-in-cost branch in `study_active._screen` stays (it serves any `pedestrian_evidence` profile).
- Known limitations: `BicycleProfile.maximum_lts` can still drop links above an LTS; no shipped preset sets it. Out of scope here.

Append "## Review, round 1" with a verdict naming `cf09283`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `pedestrian-penalties-1`, round 1; Standard tier retained.
- Target reviewed: `4432511` -> `cf09283d3b911a4c1841cc33c948430b509beff7`.
- Verdict: **accepted** at `cf09283`, with one non-blocking documentation finding (PP1). No blocking correctness defects found.
- Snapshot: independent local clone detached at the exact target, plus a separate base clone for comparison. The implementer's checkout was not modified; the handoff is the only file written there.
- Reviewer checks: locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy, and **990 tests**, coverage **95.74%**. `make validation-check` passed lint, formatting, mypy, and **58 tests**. Chromium checks ran with normal local process permissions.
- Independent base/head probe: on the made-up demo town, all four shipped walking presets produced identical sorted link attributes and cost values. The head's generic profile matched the base profile with all evidence switches enabled, both for generated link attributes and 21 supported/unknown/problematic evidence and highway-class combinations. The default profile's demo network increased from 330 to 332 links, as intended.
- Reviewed the access checks before `evidence_factor`, which still exclude `ped_allowed=False` and prohibited crossing links. The crossing test confirms that an along-street `crossing=no` tag is retained, the prohibited crossing is excluded, and an unmarked crossing is retained with a facility penalty. Rewritten network and demo tests passed; no remaining callers use the removed switches. Broach timing still uses unit evidence factors.
- Evidence reused: implementer's four fail-first test results and hook result. The reviewer independently reproduced head checks and the base/head behavior comparison.
- Verification limits: comparisons used synthetic/demo networks, not downloaded data or recorded trips. Shipped-preset equivalence was directly checked for link attributes and costs on the demo, rather than every possible network.

### PP1 — P3, non-blocking: assumptions still advertise evidence-based exclusions

- Location and recommended home: `docs/assumptions.md:206`, following the contract change in `src/walkbike_routes/network/pedestrian.py:evidence_factor`.
- Trigger: a reader consulting the sidewalk-evidence section is told a `pedestrian_evidence` preset can “leave unknown and problematic links out”. The removal of the switches makes that claim false.
- Invariant: method documentation should describe the supported profile behavior.
- Evidence: the same text is present on both base and head; the base profile exposes the exclusion switches, while the head removes them and always returns a numerical class factor. The base/head probe independently confirms the default now retains the previously excluded demo links.
- Recommended correction: say unknown and problematic evidence receive the class penalty and do not exclude otherwise walkable links; retain the separate access prohibition explanation.
- Status: open, non-blocking documentation correction, with its home identified above; not marked deferred and no follow-up issue claimed. The runtime acceptance criteria are met.
