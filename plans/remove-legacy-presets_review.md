# Review: remove-legacy-presets

## Current state

- Status: complete (accepted at 3ab6574)
- Next action: none; PR with Review line
- Review target: `83fc25f` -> `3ab6574` (branch `remove-legacy-presets`)
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `remove-legacy-presets-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T18:22Z`

## Handoff, round 1

- Tier: Standard (removes shipped presets; many tests re-pointed)
- Intended behavior and acceptance criteria:
  - The six legacy-family presets (bike and walk `legacy`, `legacy-hills`, `child`) are gone; bike `broach-2011*`, `furth`, `shortest` and walk `broach-2016*`, `shortest` and all car presets remain with **unchanged parameters** (only `source:` text changed).
  - No remaining preset, config, doc or test refers to a removed preset. Remaining "legacy" hits are the out-of-scope `lts_criteria = "legacy"` / `lts_best` street-stress rules and `validation/RESULTS.md` (historical figures, with a note).
  - Each re-pointed test still checks the behaviour it checked before; no expected value was changed just to pass. Changed expectations are justified in the commit message and below.
  - `docs/assumptions.md` and `src/walkbike_routes/report/assumption_sections.json` stay in sync (a test checks this).
- Tests with changed expectations or deleted (please check these most):
  - Deleted: `test_presets::test_the_child_presets_are_slower_and_more_cautious_but_ban_nothing`, `test_core_demo_routing::test_the_child_walking_preset_keeps_the_stretch_with_no_sidewalk_at_a_cost` (both only tested deleted presets).
  - `test_presets::test_the_presets_carry_their_settings` (was ...legacy_presets_match...): now checks furth and walk/shortest values read from their YAML.
  - `test_core_demo_routing`: bike tests use `furth`; the street-exclusion walking test uses a made-up `cautious` PedestrianProfile (old walk/legacy settings); the grid "calmer arterial saves nothing" test uses a made-up `flat` BicycleProfile because furth's hill speeds broke the equal-length premise.
  - `test_grade`: compares broach-2016 timing with hills on vs off.
  - `test_study::test_screens_can_be_turned_off`: previously asserted a missing `walk-legacy` screen in a run that never had one (could not fail); now checks `walk-broach-2016`.
- Verification: `make check` | 3ab6574 tree | 991 passed, 95.73% | none | implementer. `make validation-check` 58 passed. `prek run --all-files` passed. `make demo` wrote the report.
- Settled decisions (do not re-report): keep `furth` and `shortest`; remove the child presets; leave `lts_best` / `lts_criteria="legacy"` naming for a later change; `PedestrianProfile` defaults still exclude some links (no shipped preset uses them) — out of scope here.
- Known limitations: Los Altos is still named in two comments under `src/` (pre-existing).

Append "## Review, round 1" with a verdict naming `3ab6574`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `remove-legacy-presets-1`, round 1.
- Target reviewed: `83fc25f` -> `3ab6574a7f4436a5331f25256e0d45d2117def5d`.
- Verdict: **accepted** at `3ab6574`. No confirmed correctness defects or open findings.
- Snapshot: local clone with `--no-hardlinks --no-checkout`, detached at the exact target, following `AGENTS.md`. All checks and probes ran in the temporary clone; the handoff is the only implementer-tree file written.
- Reviewer checks: locked dependency sync succeeded; `git diff --check` passed; `make check` passed lint, format checks, mypy, and **991 tests**, coverage **95.73%**; `make validation-check` passed lint, format checks, mypy, and **58 tests**. Checks ran with normal local process permissions so the Chromium tests could execute.
- Independent inventory probe compared base and head YAML: exactly bike/walk `legacy`, `legacy-hills`, and `child` were deleted; no preset was added; every retained preset's parsed content is identical after removing only its `source` field. This also verifies retained titles, kinds, objectives, parameters and all car presets.
- Searched current source, configs, documentation, scripts and tests for removed preset names; no remaining removed-preset references were found in the reviewed paths. The `lts_criteria="legacy"` street-stress selector is retained as agreed. Historical records and excluded data were not inspected.
- Test review: the two deleted tests specifically covered deleted child presets; existing Broach-child tests still cover stress scaling and missing sidewalks. The made-up cautious walking and flat cycling profiles preserve the exclusion and equal-length screen premises. The grade comparison exercises the retained walking model's timing with grade enabled versus disabled. The screen-disabled assertion now names the actual configured preset. Other test substitutions preserve their assertions about config handling, routing integration, flow accounting and reports. The assumptions-heading synchronization test passed in the full suite.
- Evidence reused: implementer's hook and demo-run results. The reviewer independently reproduced both check targets; no new regression claim required a base/head failing probe.
- Verification limits: no downloaded city layers, real count/trip records, or historical validation figures were reviewed or rerun; the review used committed code/configs and synthetic tests. No further review round requested.
