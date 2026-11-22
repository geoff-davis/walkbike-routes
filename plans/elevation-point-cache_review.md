# Review: elevation-point-cache

## Current state

- Status: complete (accepted at c16744e after round 2)
- Next action: none; PR with Review line
- Review target: `d399050` -> `c16744e` (branch `elevation-point-cache`)
- Open findings: none (EP1 fixed in c16744e)
- Decisions needed from the user: none

## Loop

- Loop ID and round: `elevation-point-cache-1`, round 2 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T20:04Z`

## Handoff, round 1

- Tier: Standard (new cache logic for a download step)
- Issue: #15
- Intended behavior and acceptance criteria:
  - `node_elevations` returns the same `elevation_m` values as before for the same service answers (same 7-place rounding, NoData and NaN still missing, "no elevations for any node" still raised).
  - Points already in `<cache>/3dep-points.json` are not requested again; only new points are, in batches of up to `BATCH`. A repeated coordinate is requested once.
  - The table is written atomically (`.part` then replace) after each batch, so an interrupted run resumes. A damaged or non-object table is treated as empty.
  - Points with no data are stored as `null` and not re-requested (parity with the old batch cache, which also cached NoData answers).
- Evidence: `test_download::test_a_changed_network_asks_only_for_its_new_points` fails on the base source (`assert 6 == 4`: adding one node at the front refetched all three shifted batches) and passes on the head. It also covers no-new-points (0 requests), the repeated point, and a damaged table.
- Verification: `make check` 992 passed, 95.73%; `make validation-check` 58 passed; hooks passed.
- Settled decisions: keep `Fetcher`'s per-request cache unchanged (the batch files are still written; on a first run with an unchanged network and no duplicate coordinates, existing batch files are hit and fill the table).
- Known limitations: a NoData answer is permanent in the table, as it was in the batch cache; deleting `3dep-points.json` and the `3dep-*.json` batch files forces a refetch.

Append "## Review, round 1" with a verdict naming `39bad6e`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `elevation-point-cache-1`, round 1; Standard tier retained.
- Target reviewed: `d399050` -> `39bad6e889925c8a324ccd261bc7c2d0c527da50`.
- Verdict: **changes required** at `39bad6e`: EP1 is a confirmed blocking regression.
- Snapshot: independent local clone detached at the exact target, with a separate base clone for the same regression probe. The implementer's checkout was not modified; this handoff is the only file written there.
- Reviewer checks: locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy, and **992 tests**, coverage **95.73%**. `make validation-check` passed lint, formatting, mypy, and **58 tests**. Browser tests ran with normal local process permissions.
- Additional synthetic HTTP probes passed: interruption after a completed batch preserves its point entries; resuming requests only remaining points in bounded batches; a duplicate among previously uncached points is requested once; NoData persists as null and stays missing without another request; warm-cache reuse sends no requests; a non-object table is read as empty. No live service or downloaded data was used.
- Evidence reused: implementer's changed-network fail-first test and hook result. The reviewer independently reproduced the current checks and the regression below on both base and head.

### EP1 — P2, blocking: signed-zero coordinates deduplicate but have different cache keys

- Location: `src/walkbike_routes/download/elevation.py:48`, in combination with `_key` at line 78 and the result lookup at line 71.
- Trigger: a fresh cache and two WGS84 nodes at `(0.00000001, 10)` and `(-0.00000001, 10)`, with a synthetic service returning elevation 100 for every requested sample. Seven-place rounding produces `(0.0, 10.0)` and `(-0.0, 10.0)`. The same issue applies to signed zero in latitude, and exact positive/negative zero inputs.
- Violated invariant: the same rounded coordinates must have consistent deduplication and cache identity; valid inputs that previously produced elevations must not fail during result reconstruction.
- Confirmed base/head evidence: the identical isolated HTTP-mock probe returns `[100.0, 100.0]` on `d399050`. On `39bad6e` it sends one point, then raises `KeyError: '-0.0000000,10.0000000'`. Python tuple equality makes positive and negative zero identical in `dict.fromkeys`, so `wanted` retains only the positive-zero tuple. `_key` formats their signs differently, so only `0.0000000,10.0000000` is stored and the second result lookup has no entry.
- Requested fix: use one consistent canonical coordinate identity for filtering, deduplication, storage and lookup, normalizing signed zero if those points are to be requested once. Add a regression covering rounded signed zero (longitude and latitude), result reconstruction, and a subsequent cache-only call.
- Status: complete (accepted at c16744e after round 2)
- Verification limits: these probes use made-up coordinates and service answers; no live USGS request or existing user cache was read.

## Handoff, round 2

- Target: `39bad6e` -> `c16744e` (one new commit; round 1 commit unchanged)
- EP1 fixed: `_key` is the only point identity. Requests are de-duplicated by key (not by float tuple), and `_key` re-rounds and adds `0.0`, so `-0.0` and `0.0` give the same key.
- Evidence: `test_download::test_points_that_round_to_signed_zeros_are_one_point` covers both axes (`(±1e-8, 10)` and `(10, ±1e-8)`) and a warm second call with no new requests. On `39bad6e` it raises `KeyError: '-0.0000000,10.0000000'`; on `c16744e` it passes.
- Verification: `make check` 993 passed, 95.73%; hooks passed.

Append "## Review, round 2" with a verdict naming `c16744e`, then send one prompt to the implementer named in the Loop section.

## Review, round 2

- Loop: `elevation-point-cache-1`, round 2; Standard tier retained.
- Target reviewed: `39bad6e` -> `c16744e565cde95695f994f9ffe1998090d19068`.
- Verdict: **accepted** at `c16744e`. EP1 is verified fixed; no open findings or new confirmed defects.
- Snapshot: independent local clone detached at the exact target, plus a separate `39bad6e` clone for the identical regression probe. The implementer's tree was not modified; this handoff is the only file written there.
- Independent EP1 probe: eight cases cover longitude and latitude, rounded and exact positive/negative zero, and both input orders. On `39bad6e`, all eight raise `KeyError`. On `c16744e`, all eight return both mocked elevations, request the coordinate once, and reuse the saved point for a second call with reversed node order without another request.
- Fix assessment: deduplication, cache membership, storage and result lookup now all use `_key`; its rounding and positive-zero normalization give equal coordinates one identity. The new committed regression test covers both axes and a cache-only second call and passes in the full suite.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy, and **993 tests**, coverage **95.73%**. `make validation-check` passed lint, formatting, mypy, and **58 tests**. Browser tests ran with normal local process permissions.
- Evidence reused: implementer's new regression fail-first result and hooks result; round-1 interruption/resume, NoData persistence, duplicate-point, and non-object-cache probes. The reviewer independently reproduced both revision outcomes for EP1 and both check targets at the new head.
- Verification limits: tests and probes used synthetic coordinates and mocked service answers; no live service or user cache was inspected. No further round requested.
