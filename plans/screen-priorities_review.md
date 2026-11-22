# Review: screen-priorities

## Current state

- Status: accepted at 219f6d2
- Review target: `6c2f7d5` -> `28f2356` (branch `screen-priorities`, stacked on `screen-block-flow`, PR #23)
- Open findings: none yet

## Loop

- Loop ID and round: `screen-priorities-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-23T00:03Z`

## Handoff, round 1

- Tier: Standard (changes which ranked places reports list)
- Goal (the user's request): prioritize the improvement places and list fewer of them. The user chose to cut each kind of change separately rather than rank kinds together.
- Acceptance criteria:
  - The report table and the map use one rule, `screens.priorities()`: for each kind of change, in rank order, places saving at least 10% of that kind's best, at most 5.
  - Kinds are cut against their own best, never against another kind's, and keep the order the run wrote them in.
  - Ranks, savings and the screen's files are unchanged: only what reports list changes. A report rebuild needs no rerun.
  - The report's method note states the rule and why kinds are not ranked against each other.
- Evidence: `test_priorities_cut_each_kind_at_a_share_of_its_best` in `tests/test_screens.py`; it fails on base, where `priorities` does not exist. On a local example's four screens the map lists go 20 to 8, 14 to 9, 20 to 10 and 13 to 7.
- Verification: `make check` 1008 passed, 95.71%.
- Settled decisions (user): cut per kind; 10% share and at most 5 were the proposal the user accepted.
- Known limitations: the screen still computes up to `screen_candidates` places per kind; only the listing is trimmed.

Append "## Review, round 1" with a verdict naming `28f2356`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `screen-priorities-1`, round 1; Standard tier retained.
- Target reviewed: `6c2f7d5` -> `28f2356d61f04b0f875031a2329ce0bb80be2c17`.
- Verdict: **changes required** at `28f2356`: SP1 is a confirmed inclusive-cutoff defect affecting both report and map listings.
- Snapshot: independent local clone detached at the exact target, with a separate base clone for identical rendering probes. The implementer's checkout was not modified; this handoff is the only file written there. No existing data/output directories, private local overlays or credential files were inspected.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy and **1002 tests**, with **4 deselected**, coverage **95.03%**. The two dotenv file-opening tests were excluded from collection; four config tests that open synthetic local overlays were deselected to honor the requested file restrictions. Chromium smoke tests ran with normal local process permissions.
- Independent synthetic probes passed for unsorted input ranks, preserving first-seen kind order, independent per-kind maxima, the five-place cap, empty input and not mutating the input screen. Source inspection confirms both renderers call the shared helper and the report method note states the rule and the reason for keeping kinds separate. No routing calculation or screen writer was changed.

### SP1 — P2, blocking: an exact 10% saving can be dropped

- Location: `src/walkbike_routes/analysis/screens.py:642`.
- Trigger: one kind has rank-ordered `weighted_generalized_minutes_saved` values `[3.0, 0.3, 0.29]` and uses the default 10% share.
- Violated criterion: places saving at least 10% of their own kind's best must qualify, subject to the five-place cap. The second place meets that boundary exactly and should remain; the third should be excluded.
- Confirmed evidence: the direct helper returns only rank 1. Python evaluates `0.1 * 3.0` as `0.30000000000000004`, so the raw comparison rejects stored `0.3`. This is not a below-threshold case or a row-cap effect. Synthetic integration probes with the actual `screen_table` and `_screen` renderers confirm the head lists only rank 1 in both outputs. The identical base probes list ranks 1/2/3; applying the intended new rule should list ranks 1/2.
- Requested fix: make the inclusive boundary robust to floating-point roundoff using a suitably small tolerance consistent with the existing numerical comparisons. Add coverage for the exact boundary and a genuinely smaller saving (for example 0.29), while preserving the separate per-kind comparison and cap.
- Status: open. No other confirmed findings.
- Evidence reused: implementer's ordinary per-kind selector test and full check result (1008 tests), plus their example listing counts. The actual example outputs were deliberately not inspected. The reviewer independently reproduced the boundary on base/head using made-up layers and mocked parquet reads; no saved study results were read.
- Verification limits: no real-data report rebuild or validation run was performed for this report-selection-only change. No claim is made that the new cutoff existed on the base; this finding concerns the new rule's inclusive boundary.

## Handoff, round 2

- Target: `28f2356` -> `219f6d2`
- SP1 fixed: the floor is `share * best * (1 - 1e-9)`. `test_priorities_keep_a_place_at_exactly_the_share` uses your [3.0, 0.3, 0.29] case and expects ranks 1 and 2; it fails at `28f2356`.
- `make check`: 1009 passed, 95.71%.

Append "## Review, round 2" with a verdict naming `219f6d2`, then send one prompt to walkbike-implementer.

## Review, round 2

- Loop: `screen-priorities-1`, round 2; Standard tier retained.
- Target reviewed: `28f2356` -> `219f6d2c0fb500a17257e805a3e40aa6de446baa`.
- Verdict: **accepted** at `219f6d2`. SP1 is verified fixed; no open findings or new confirmed defects.
- Snapshot: independent local clone detached at the exact target, with a separate base clone for identical rendering probes. The implementer's checkout was not modified; this handoff is the only file written there. No existing data/output directories, private overlays or credential files were inspected.
- SP1 verification: the identical synthetic `[3.0, 0.3, 0.29]` probe returns only rank 1 on `28f2356`, and ranks 1/2 on `219f6d2`, through the helper and both actual report/map rendering paths. The boundary is retained and the genuinely smaller value remains excluded. Parquet reading was mocked with made-up layers; no saved study results were read.
- Additional probes passed for scaled boundary cases, unsorted ranks, separate per-kind maxima and five-place caps, first-seen kind order, and unchanged input values. The small relative tolerance fixes the comparison without changing the output values or stored screens. The committed exact-boundary regression passes.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy and **1003 tests**, with **4 deselected**, coverage **95.03%**. The two dotenv file-opening tests were excluded from collection and four synthetic-local-overlay file-opening tests deselected to honor the requested restrictions. Chromium smoke tests ran with normal local process permissions.
- Evidence reused: implementer's unfiltered check result (1009 tests, 95.71%) and fail-first test result, plus the round-1 method-note and integration inspection. Validation checks were not repeated for this report-selector comparison fix.
- Verification limits: all direct probes used synthetic tables and mocked readers; existing user reports were not rebuilt or inspected. No further review round requested.
