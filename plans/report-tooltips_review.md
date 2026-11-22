# Review: report-tooltips

## Current state

- Status: ready for reviewer
- Next action: walkbike-reviewer reviews round 1
- Review target: `a6eb02c` -> `33e29aa` (branch `report-tooltips`)
- Open findings: none yet
- Decisions needed from the user: none

## Loop

- Loop ID and round: `report-tooltips-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T22:30Z`

## Handoff, round 1

- Tier: Standard (report and map output)
- Goal (the user's request): all jargon in the reports has hover definitions.
- Acceptance criteria:
  - Every `<th>` in the HTML report has a hover note, except "#".
  - Glossary terms in cells, paragraphs and bullets get a note at their first use in that passage, whole words only, ignoring case; the text stays escaped.
  - Route model names in cells and headers show the preset's title.
  - Map popups and side panel define glossary terms on hover; the side panel's figures, route-model names and the Route model, Trips and Travel time controls have notes; no script errors.
  - Definitions are accurate to the code (LTS, risk points factors, drop-off rule, path-size logit, distance decay).
- Evidence: on base `a6eb02c`, these new tests fail:
  - `test_report_tables::test_every_column_and_route_model_is_defined_on_hover`
  - `test_report_tables::test_jargon_is_defined_once_per_passage_and_only_as_a_whole_word`
  - `test_map_page::test_the_map_defines_jargon_on_hover`
  At head they pass. A headless-browser check of a Claremont map found the notes in popups and the panel, with no console errors.
- Verification: `make check` 998 passed, 95.73%; `make validation-check` 58 passed.
- Settled decisions: Markdown output is unchanged; its Key terms and Method section carry the definitions. Key terms entries themselves are not glossed.
- Known limitations: the map uses `title` tooltips, which touch screens do not show. A term used in both a popup's priority line and its factors line is noted in each.

Append "## Review, round 1" with a verdict naming `33e29aa`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `report-tooltips-1`, round 1; Standard tier retained.
- Target reviewed: `a6eb02c` -> `33e29aa4b6cb4ff7fe72fd9fb5a00c27f14faa76`.
- Verdict: **changes required** at `33e29aa`: TT1, TT2 and TT3 are confirmed tooltip-accuracy defects.
- Snapshot: independent local clone detached at the exact target, plus a separate base clone for identical rendering probes. The implementer's checkout was not modified; this handoff is the only file written there.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy and **998 tests**, coverage **95.73%**. `make validation-check` passed lint, formatting, mypy and **58 tests**. Chromium tests, including the new map glossary exercise, ran with normal local process permissions.
- Additional probes rendered a synthetic cycling-shortest summary, a rules table with non-default drop-off settings, and a shipped Furth method paragraph on both revisions. The base has none of the new erroneous glossary/model tooltips; the head produces the incorrect assertions detailed below. The findings concern added information, not changed routing calculations.
- Evidence reused: implementer's fail-first test results and local map inspection. No downloaded study layers, real count/trip records or existing credential files were inspected.

### TT1 — P2, blocking: cycling shortest is described as walking

- Location: `src/walkbike_routes/report/text.py:297-300`, consumed by `report/tables.py:_cell_html` and column tips.
- Trigger: render `summary_table` for a row with `mode='bike', preset='shortest'`, or a cycling busiest-streets table with a shortest column.
- Violated criterion: route-model names in cells and headers must show that preset's title.
- Confirmed evidence: a synthetic cycling summary renders `shortest` with `Route model: Shortest distance on foot, ignoring sidewalks and traffic.` The cycling preset's actual title is `Shortest distance by bike, ignoring traffic stress`. `model_tips` keys only by name and walks the walking presets first; `setdefault` therefore discards the cycling title. The identical base rendering has no model tooltip rather than an incorrect walking title.
- Requested fix: retain mode when selecting the preset title for report cells and headers instead of resolving the ambiguous bare name globally. Cover both walking and cycling shortest in the same report, including cycling column headings.
- Status: open.

### TT2 — P2, blocking: drop-off tooltip contradicts configured risk rules

- Location: `src/walkbike_routes/report/text.py:254-256`; the same static glossary is embedded by `report/map.py`.
- Trigger: use supported settings such as `RiskPoints(drop_off_share=0.2, drop_off=3)` and hover over drop-off in the risk-rules table or map factors.
- Violated criterion: definitions must accurately describe the drop-off rule used by the code and run.
- Confirmed evidence: rendering `rules_blocks(ExposureRules(points=RiskPoints(drop_off_share=0.2, drop_off=3)))` correctly prints busiest 20% and +3, but the new hover text says busiest 10% and one more. `analysis/exposure_streets.py` reads the configured share and points, so this is a tooltip error rather than a calculation issue. The base renders the correct configured row without the contradictory tooltip.
- Requested fix: make the explanation independent of particular defaults, explicitly identify defaults as such, or derive the tooltip from the run's recorded rules consistently in report and map. Add coverage for non-default values.
- Status: open.

### TT3 — P2, blocking: uncalibrated falsely implies unchanged published parameters

- Location: `src/walkbike_routes/report/text.py:264-265`.
- Trigger: hover over uncalibrated in the method description of bike/furth or a Broach child preset.
- Violated criterion: definitions must accurately describe the model assumptions and code.
- Confirmed evidence: rendering the Furth title/source paragraph attaches `the numbers come from the published study, used as they are` to uncalibrated. Its shipped YAML explicitly calls the stress multipliers hand-set judgments not estimated from observed route choice. Child presets likewise include doubled stress terms and other explicit judgments. Being uncalibrated does not establish unchanged empirical coefficients. The base shows the same source description without the new conflicting claim.
- Requested fix: define lack of calibration without asserting a particular source or unchanged coefficients; leave the provenance and modifications to each preset's own description. Add a regression using an existing hand-set or modified preset.
- Status: open.

- Verification limits: accuracy probes used synthetic summary/rule tables and committed preset descriptions; no live external studies were fetched. Browser smoke tests passed, but they do not check these semantic tooltip cases.

## Handoff, round 2

- Target: `33e29aa` -> `99efb0b`
- TT1 fixed: `text.model_tip(mode, name)` replaces the name-only lookup. The reference table's route-model cells are `Noted` cells (plain text in Markdown), and `busiest_blocks` takes the mode for its header notes. Test: `test_a_route_models_note_is_for_its_own_mode` (walk and bike `shortest` in one table, and bike busiest-streets headers).
- TT2 fixed: the drop-off note has no share or points and points to "Risk points used". Test: `test_glossary_notes_do_not_fix_what_a_study_can_set`.
- TT3 fixed: "uncalibrated" says some route models adjust the published penalties by hand. Same test.
- Both new tests fail at `33e29aa`: the first because `tables.Noted` does not exist there, the second on the old wording.
- `make check`: 1000 passed, 95.73%. `make validation-check`: 58 passed.

Append "## Review, round 2" with a verdict naming `99efb0b`, then send one prompt to walkbike-implementer.

## Review, round 2

- Loop: `report-tooltips-1`, round 2; Standard tier retained.
- Target reviewed: `33e29aa` -> `99efb0b4efa355c08ba6a25dc88a5e52e9883fca`.
- Verdict: **changes required** at `99efb0b`. TT1 and TT2 are verified fixed; TT3 remains open because the revised definition still makes an unsupported provenance claim.
- Snapshot: independent local clone detached at the exact target, with a separate `33e29aa` clone for identical probes. The implementer's checkout was not modified; this handoff is the only file written there.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. `make check` passed lint, formatting, mypy and **1000 tests**, coverage **95.73%**. `make validation-check` passed lint, formatting, mypy and **58 tests**. Chromium smoke tests ran with normal local process permissions.
- TT1, fixed: independent rendering of walking and cycling shortest in the same summary now gives each its own shipped title. A cycling busiest-streets header also carries the cycling title. The same probes at `33e29aa` incorrectly describe cycling shortest as walking. Summary-table Markdown is identical across revisions; the new Noted cells preserve its plain text.
- TT2, fixed: rendered rules with `drop_off_share=0.2` and `drop_off=3` keep their correct 20%/+3 values, and the tooltip no longer contradicts them with default numbers. The shared glossary used by report and map now directs readers to the run's rule.

### TT3 — P2, blocking, remains open: uncalibrated still asserts published provenance

- Current location: `src/walkbike_routes/report/text.py:264-266`.
- Trigger and evidence: render a paragraph containing the committed bike/furth title and source. The actual resulting tooltip now says: `The penalties come from published studies, some adjusted by hand`. The same shipped source still calls the LTS cost multipliers hand-set judgments not estimated from observed route choice. `docs/routing-methods.md` explicitly says the multipliers are not Furth's and describes them as hand-set costs on top of published street ratings. The round-2 wording removes the unchanged-numbers claim, but still assigns published-study provenance to penalties that were set by judgment.
- The first sentence also substitutes `Not checked against counts or surveys` for lack of calibration. The repository's own definition for this preset is that the multipliers were not fitted; checking a model and fitting its parameters are different operations.
- Violated criterion: glossary definitions must accurately describe the shipped models and assumptions. This is the same semantic finding from round 1, not a routing regression or a request for a new model decision.
- Requested correction: define lack of fitting without promising a source. For example: `Not fitted to observed trips in the intended population or area. Values may be literature-based or hand-set; Method explains each model.` Preserve the per-preset source descriptions. Test the definition against the existing hand-set Furth example; asserting only that the words adjusted by hand occur does not catch this contradiction.
- Status: open. TT1 and TT2 are closed as verified fixed; no other new findings.
- Evidence reused: implementer's fail-first tests and round-1 rendering/browser checks. The reviewer independently reproduced both check targets and the three semantic probes on base and head. No live study data or existing credential files were inspected.
