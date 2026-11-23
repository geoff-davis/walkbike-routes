# Review: school-gates

## Current state

- Status: accepted at ebbb705
- Review target: `4b787b2` -> `42082e2` (branch `school-gates`)
- Open findings: none yet

## Loop

- Loop ID and round: `school-gates-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-23T04:31Z`

## Handoff, round 1

- Tier: Small (one public study config: entrance points only)
- Goal (the user's request): give the seven newer Los Altos schools their street entrances, read from aerial imagery, in place of the district school point.
- Acceptance criteria:
  - The config loads; each of Covington, Gardner Bullis, Loyola, Oak Avenue, Santa Rita, Springer and Blach has `access_points` and one `car_access_points` entry that is one of its gates.
  - Each gate lies at the campus edge on the named street, a few tens of metres to a few hundred metres from the school point, never across town.
  - The header and comments say plainly the gates are read from imagery and need checking on the ground; approximate and unsure gates are marked.
  - Nothing else changes (ages, `nearest`, area, other schools).
- Evidence: `make check` 1009 passed, 95.71%. Positions were calibrated against an exact map pin (0.47 m per screen pixel at the zoom used).
- Settled decisions (user): use these imagery-derived gates for now.
- Known limitations: which gates are open at bell times is unknown; paths across fields from side streets were left out. Do not read `*.local.yaml`.

Append "## Review, round 1" with a verdict naming `42082e2`, then send one prompt to the implementer named above.

## Review, round 1

- Target reviewed: `4b787b2..42082e2` (`42082e2c48677467c8bd398769c807c4548cdbb1`).
- Verdict: **changes required** for `42082e2`.

### SG1 — P2: place the Covington Rosita entrance at the campus edge

`config/los_altos_schools.yaml:75` adds `[-122.105200, 37.371100]` as the drive out to Rosita Avenue. Plotting that exact coordinate on public aerial imagery places it inside the tennis courts, west of the parking lot, rather than at the named street entrance. This is an imagery-based finding, not a claim about whether a gate is open. A route using this configured access point would use an interior court location as its destination entrance. Marking it approximate does not satisfy the handoff's campus-edge criterion. Locate the actual driveway/street connection and correct the coordinate, or remove this uncertain point until it can be located; retain the confirmed Covington entrances.

Evidence: [Esri World Imagery export](https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox=-13593154.107974146%2C4490725.903388471%2C-13592554.107974146%2C4491325.903388471&bboxSR=3857&imageSR=3857&size=600%2C600&format=jpg&f=image). The export's reported extent matches the requested 600 by 600 EPSG:3857 extent; the coordinate projects to pixel approximately `(465.4, 365.6)` from the upper-left corner, inside the visible courts. This is reproducible from the public image without local study layers.

### Verification and limits

- Reviewed an independent detached snapshot of the target commit, with independent Git metadata; only the public config changes.
- Public config loads with local layers disabled. All seven schools have gates and exactly one car gate contained in their gate list. Gate distances from their school points are approximately 97–179 metres.
- Comparing parsed base and target configs after excluding the seven schools' gate lists confirms all other settings and schools are unchanged. Imagery/provisional comments are present. The other plotted points appear consistent with their stated frontages at this imagery scale.
- `git diff --check` passed. `make check` passed with 1003 tests, four deselected, 95.03% coverage; two dotenv tests were not collected and four private-config tests were deselected to honor the file restrictions.
- No repository `data/`, `output/`, `.env`, or `*.local.yaml` was read. Imagery cannot establish current gate availability or bell-time access. No replacement coordinate is asserted without verification.

## Handoff, round 2

- Target: `42082e2` -> `ebbb705`
- SG1 fixed by removal: Covington's approximate third gate is gone; it keeps the two Covington Road gates (loop entrance and exit). The Rosita Avenue side waits for a check on the ground.
- The config loads; `make lint` passes. No other change.

Append "## Review, round 2" with a verdict naming `ebbb705`, then send one prompt to walkbike-implementer.

## Review, round 2

- Loop: `school-gates-1`, round 2.
- Target reviewed: `42082e2..ebbb705` (`ebbb7052cc4e06e6fed677417c5d33f200b1a3f6`).
- Verdict: **accepted** at `ebbb705`. SG1 is resolved; no new findings.
- The same parsed-config probe on base and head confirms the misplaced Covington point exists in the base and is absent from the head. Covington retains both Covington Road entrances and its single car gate, which remains one of those entrances. Removing only that point from the base produces the head config exactly; the sole changed path is `config/los_altos_schools.yaml`.
- The public config loads with `local=False`. `git diff --check`, lint, format checks and type checks pass. `make check` passes with 1003 tests, four deselected, and 95.03% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions.
- Reviewed and checked an independent detached clone with its own Git metadata. No repository `data/`, `output/`, `.env`, or `*.local.yaml` was read. The remaining imagery-derived gates still need the ground checks documented in round 1; this fix introduces no replacement coordinate requiring an imagery review.
