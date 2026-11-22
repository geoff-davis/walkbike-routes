# Review: route-cost-minutes

## Current state

- Status: accepted at a220c06
- Next action: open the PR
- Review target: `c9e3e22` -> `092fcee` (branch `route-cost-minutes`)
- Open findings: none yet
- Decisions needed from the user: none

## Loop

- Loop ID and round: `route-cost-minutes-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T21:07Z`

## Handoff, round 1

- Tier: Standard (report output and an AGENTS.md rule)
- Acceptance criteria:
  - No "generalized minutes" remains in README, AGENTS.md, docs or src; "route-cost minutes" is used and called generalized cost where introduced.
  - `docs/routing-methods.md#route-cost-minutes` exists and the README, assumptions, report and map links point to it.
  - Key terms include "Route-cost minutes" with a "More" link only when the run has `screen.parquet`; HTML escapes the URL and opens it in a new tab; Markdown renders `[More](url)`.
  - `Terms.links` defaults to empty, so other callers are unchanged.
- Evidence: `test_report_tables::test_main_runs_list_where_routes_meet_traffic` asserts the new term and link (fails on base: term missing).
- Verification: `make check` 993 passed, 95.73%; markdownlint passed; hooks passed.
- Settled decisions: the user chose "route-cost minutes" and asked for a definition plus tooltip or link; the link targets the public GitHub docs.

Append "## Review, round 1" with a verdict naming `092fcee`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `route-cost-minutes-1`, round 1; Standard tier retained.
- Target reviewed: `c9e3e22` -> `092fceec81a43e71db8119779c6fb6c6a3960a42`.
- Verdict: **changes required** at `092fcee`, solely for the remaining terminology occurrence in RCM1. No runtime correctness regression confirmed.
- Snapshot: independent local clone detached at the exact target. The implementer's checkout was not modified; this handoff is the only file written there.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy, and **993 tests**, coverage **95.73%**. `make validation-check` passed lint, formatting, mypy, and **58 tests**. Browser tests ran with normal local process permissions.
- Independent renderer probes passed: the new HTML anchor escapes ampersands and quotes in the URL, opens a new tab and includes `rel="noopener"`; Markdown renders the More link; ordinary Terms callers retain the empty-links default and existing rendering. A synthetic manifest/report probe confirmed that the term and link appear with a screen and are absent without one. The local documentation heading and the README, assumptions, report and map link targets agree.
- Evidence reused: implementer's Markdown checks, hook result and updated report assertion. The reviewer independently reproduced core/validation checks and the renderer/gating probes.

### RCM1 — P3, blocking on the explicit acceptance criterion: one old term remains

- Location: `src/walkbike_routes/analysis/screens.py:30` (module docstring, continuing on line 31).
- Trigger: search across whitespace using `rg -n -U -i 'generalized\s+minutes' README.md AGENTS.md docs src`. The head still says `Savings are generalized` followed by `minutes (time plus the preset's stress and crossing costs), not clock minutes.`
- Violated criterion: the handoff explicitly requires no “generalized minutes” in README, AGENTS.md, docs or src. The changed function docstring in this same module was updated, but the module-level explanation was missed because the phrase crosses a newline.
- Confirmed evidence: the same multiline search over committed base/head text finds six occurrences at `c9e3e22` and one at `092fcee`, namely this module docstring. This is an incomplete terminology migration, not a new runtime defect; severity is low, but the stated all-occurrences criterion remains unmet.
- Requested fix: rename the module-level wording to route-cost minutes, preserving its distinction from clock minutes and identifying it as generalized cost. Re-run a multiline search over the acceptance paths to confirm zero remaining old-term matches.
- Status: open. No other confirmed findings.
- Verification limits: renderer probes used synthetic content and manifest stubs. The public GitHub target was checked against the committed local path and heading, not fetched live before publication.

## Handoff, round 2

- Target: `092fcee` -> `a220c06`
- RCM1 fixed: the screens.py module docstring now says route-cost minutes (generalized cost).
- Check: `grep -rPzo "generali[sz]ed\s+minutes"` over `*.py`, `*.md`, `*.html` and `*.yaml` outside `.venv` finds only this review record, which quotes the old term.
- `make check`: 993 passed, all checks clean.

Append "## Review, round 2" with a verdict naming `a220c06`, then send one prompt to walkbike-implementer.

## Review, round 2

- Loop: `route-cost-minutes-1`, round 2; Standard tier retained.
- Target reviewed: `092fcee` -> `a220c06e100d9884d52223592abf6777f875307a`.
- Verdict: **accepted** at `a220c06`. RCM1 is verified fixed; no open findings or new confirmed defects.
- Snapshot: independent local clone detached at the exact target. The implementer's checkout was not modified; this handoff is the only file written there.
- Independent RCM1 probe: scanned committed README, AGENTS.md, docs and src text using case-insensitive `generali[sz]ed\s+minutes`, including matches across newlines. The round-1 target has the single reported match in `analysis/screens.py:30`; `a220c06` has zero matches. The replacement identifies route-cost minutes as generalized cost and preserves the distinction from clock minutes.
- Scope check: the only changed file is `analysis/screens.py`; comparing parsed syntax trees after excluding the module docstring confirms executable code is identical. Report rendering, links and term gating are unchanged from the reviewed round-1 implementation.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy, and **993 tests**, coverage **95.73%**. Browser tests ran with normal local process permissions.
- Evidence reused: round-1 renderer/gating probes and validation checks (58 tests), plus the implementer's round-2 check result. Validation checks were not repeated for this module-docstring-only fix.
- Verification limits: public links were assessed against the committed local path and heading in round 1 rather than fetched before publication. No further review round requested.
