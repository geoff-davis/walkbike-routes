# Review: screen-block-flow

## Current state

- Status: accepted at 66ba850
- Review target: `719ce10` -> `66ba850` (branch `screen-block-flow`)
- Open findings: none yet

## Loop

- Loop ID and round: `screen-block-flow-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T23:57Z`

## Handoff, round 1

- Tier: Standard (new report logic; no routing, guard or contract change)
- Goal (the user's request): show, in each ranked improvement place's map popup, the flow on its busiest street today, so readers can relate "helps N" to the street flow.
- Acceptance criteria:
  - A street section's value is the largest `potential_flow` among its `physical_edge_ids` in the same run's `flows.parquet`.
  - A junction (`J:<node>`) takes the largest flow among streets within 1 m of its point; a place with no matching street shows nothing rather than a wrong number.
  - Zero flow today is shown as 0 (people who would switch onto a fixed street), not hidden.
  - The popup says why the two numbers differ, in plain words; "route-cost minutes" wording unchanged.
  - Reports rebuild with `report` from existing outputs, without rerouting.
- Evidence: `tests/test_map_busiest_today.py` (made-up streets); it fails on base, where `busiest_today` does not exist. A rebuild of a local example report gave values for all 35 popups, including a 0 for a street with no sidewalk today.
- Verification: `make check` 1007 passed, 95.71%.
- Known limitations: a junction whose streets end more than 1 m from its point shows no value. The value is today's flow under the run's own route model, split across near-best routes.

Append "## Review, round 1" with a verdict naming `66ba850`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `screen-block-flow-1`, round 1; Standard tier retained.
- Target reviewed: `719ce10` -> `66ba8506c0f67d8db8e4bf084e6ea5ea49420a28`.
- Verdict: **accepted** at `66ba850`. No confirmed correctness defects or open findings.
- Snapshot: independent local clone detached at the exact target. The implementer's checkout was not modified; this handoff is the only file written there. No existing data/output directories, credential files or private local overlays were inspected.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy and **1001 tests**, with **4 deselected**; coverage **95.03%**. The two dotenv file-opening tests were excluded from collection and the four config tests that open synthetic local-overlay files were deselected to honor the requested file restrictions. Chromium smoke tests ran with normal local process permissions.
- Independent synthetic-table probes passed: maximum flow across a place's listed physical edges, known zero flow, missing edge IDs, junction maximum, a point exactly 1 m from a street, and a point just beyond the cutoff. `_screen` was exercised with its parquet reader mocked to return a made-up layer; it formats zero as the string `0` and unmatched places as null. No saved study results were read.
- Executed the exact popup template expression from the committed map source in JavaScript with synthetic records: zero is displayed, a null value suppresses the added note, the explanation includes people switching onto the improved route, and weighted route-cost minutes wording remains. The template escapes the formatted value and run's flow words.
- Source integration check: `page_data` passes each run's own already-read full flows layer to `_screen`, retaining zero-flow edges; it does not pass only the positive-flow subset used to draw lines. The addition remains in report rendering and introduces no routing call or new artifact requirement for rebuilding reports.
- Evidence reused: implementer's unfiltered core checks (1007 tests) and their example rebuild report. The example outputs were deliberately not inspected. Validation checks were not repeated for this map-rendering-only change.
- Verification limits: all direct value probes used made-up tables and in-memory reader mocks; existing user reports were not rebuilt or inspected. No finding is asserted from the excluded unrelated file-loader tests. No further review round requested.
