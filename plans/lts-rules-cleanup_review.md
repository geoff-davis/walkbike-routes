# Review: lts-rules-cleanup

## Current state

- Status: complete (accepted at 27196cb)
- Next action: none; PR with Review line
- Review target: `95c1c5c` -> `27196cb` (branch `lts-rules-cleanup`)
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `lts-rules-cleanup-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T19:22Z`

## Handoff, round 1

- Tier: Standard (removes a profile option; renames an accepted config value)
- Issues: #9, #13
- Intended behavior and acceptance criteria:
  - `BicycleProfile` has no `maximum_lts`; `build_bicycle_links` keeps every rideable street (`bike_allowed` true) whatever its LTS. Shipped presets' results are unchanged (none set it).
  - `lts_criteria` accepts `"simplified"` (default, reads `lts_best`) and `"furth"`; `"legacy"` is refused like any unknown value. No shipped preset or config sets `"legacy"` (presets come only from the package; `bike/furth` sets `"furth"`).
  - No "legacy" wording for the stress rules and no city name remain in `src/walkbike_routes` (`grep -rni "legacy\|los altos" src`).
  - Out of scope: Los Altos in `config/`, `docs/` and `README.md` (public-data example studies), and the `lts_best` column name.
- Evidence (head tests, base source; both fail on base and pass on head):
  - `test_furth::test_a_profile_chooses_which_levels_to_read`: default is `"simplified"`; `"legacy"` refused.
  - `test_furth::test_no_profile_can_leave_out_streets_by_traffic_stress`: `maximum_lts` is refused.
  - `test_core_network::test_bicycle_links_apply_lts_threshold_direction_and_stress_cost`: the `maximum_lts=1` part was removed; it now asserts every rideable street is kept.
  - `test_core_network::test_link_builders_return_typed_empty_layers_when_everything_is_filtered`: empties the layer with `bike_allowed=False` instead of `maximum_lts=0`.
- Verification: `make check` 991 passed, 95.73%; `make validation-check` 58 passed; `make demo` ran; hooks passed.
- Settled decisions (do not re-report): the name `simplified` (matches docs/assumptions.md); no backward-compatible `"legacy"` alias (no external configs set it).

Append "## Review, round 1" with a verdict naming `27196cb`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `lts-rules-cleanup-1`, round 1; Standard tier retained.
- Target reviewed: `95c1c5c` -> `27196cbf2b11559889ccb62e2ca82cec96d408a8`.
- Verdict: **accepted** at `27196cb`. No confirmed correctness defects or open findings.
- Snapshot: independent local clone detached at the exact target, plus a separate base clone for comparison. All probes ran in temporary snapshots. The implementer's checkout and hooks were not modified; this handoff is the only file written there.
- Reviewer checks: locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy, and **991 tests**, coverage **95.73%**. `make validation-check` passed lint, formatting, mypy, and **58 tests**. Browser tests ran with normal local process permissions.
- Independent base/head probe: all six shipped cycling presets and the generic default generated identical sorted link attributes and costs on the made-up demo town. The synthetic network fixture retained every rideable physical edge and excluded forbidden edges on both revisions. The removed threshold was not used by shipped presets.
- Contract probes: `simplified` maps to `lts_best`, `furth` maps to `lts_furth`; `legacy` and an unknown selector raise `ValueError`; passing `maximum_lts` raises `TypeError`. Base/head field inspection confirmed the intended default rename and threshold removal. The changed tests preserve direction and stress-cost assertions and now exercise empty output via access prohibition.
- Source/caller inspection: no case-insensitive `legacy` or `Los Altos` matches remain under `src/walkbike_routes`. No active callers or shipped presets/configs use the removed threshold or retired selector; the retained threshold reference in tests verifies its rejection. The `lts_best` column and classification/cost calculations are unchanged.
- Evidence reused: implementer's fail-first test results, demo run and hook result. The reviewer independently reproduced core and validation checks and base/head link comparisons.
- Verification limits: comparisons used synthetic/demo networks, not downloaded layers or recorded trips; shipped-preset equivalence was directly checked for demo link attributes and costs rather than every possible input network. No further round requested.
