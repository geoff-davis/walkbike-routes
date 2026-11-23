# Review: map-tooltips

## Current state

- Status: complete
- Next action: open the PR
- Review target: `6ce67474c05ca483f1f3fbc72a2fb1f901fabfe1` -> `2af134d`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `map-tooltips-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-23T17:15Z`

## Handoff, round 1

- Tier: Small (a local fix to the map page's hover notes; no guard added,
  removed or loosened).
- Intended behavior and acceptance criteria:
  - The user saw only the help cursor on the map's dotted terms: Chrome running
    natively on Wayland does not show native `title` tooltips. The report page
    already draws its own notes (`#tip`, `data-tip`) and works.
  - On hover or keyboard focus of any element with a `title` (glossary terms
    `.gl`, the control labels, panel headings and rows), the map now shows a
    page-drawn note with the title's text, kept inside the window, and hides
    it on mouse-out, focus-out, scroll or wheel.
  - The `title` moves to `data-tip` on first hover so the native tooltip does
    not also show where it works. Code that later sets `.title` again (the
    Trips and Travel time labels) is picked up on the next hover.
  - `<select>` and `<option>` are left alone: their native lists draw outside
    the page.
  - The note is hidden in print.
- Changed areas: `src/walkbike_routes/report/map.html` (CSS for `#tip`, one
  script block before the first `draw()`), `tests/test_map_page.py` (one
  test). The markup `gl()` and `tip()` produce is unchanged, so the existing
  glossary test still holds.
- Pattern inventory: every native tooltip on the map comes from a `title`
  attribute (15 in a built page, plus ones set by script); the document-level
  handler covers all of them. The report page has no `title` attributes.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `uv run pytest tests/test_map_page.py -k own_notes` | base template, new test | 1 failed (`no note`) | none | implementer |
  | `uv run pytest tests/test_map_page.py` | `2af134d` | 5 passed | none | implementer |
  | `make check` | `2af134d` | 1010 passed, 95.71% | none | implementer |

- Condition inventory: not required for Small.
- Settled decisions (do not re-report): keep native `title` in the generated
  markup and convert on hover, rather than rewriting every producer; the
  report page's own tooltip script is left as is.
- Known limitations and out of scope: touch devices get the note on tap
  focus only where the element is focusable (`.gl` has `tabindex="0"`; labels
  are focusable through their inputs).

## Review, round 1

- Loop: `map-tooltips-1`, round 1; Small tier retained.
- Target reviewed: `6ce6747..2af134d` (`2af134d9dce894932cb57e88ce0c5b6c895ed4c2`).
- Verdict: **accepted** at `2af134d`. No blocking findings.
- Reviewed the complete two-file diff in an independent detached clone with its own Git metadata. The handler uses text content, converts the nearest title to a reusable note, refreshes a later title update, and handles dynamically rendered map content through document events. The print stylesheet hides the note.
- Independent Chromium probes using the made-up demo passed for hover/native-title removal, keyboard focus and blur, title updates, scroll and wheel dismissal, and viewport bounds after scrolling each tested anchor into view. The same feature probe against the base handler reports no page-drawn note, as expected. This verifies the replacement without claiming to reproduce native Wayland tooltip behavior.
- `git diff --check` and `make check` passed: lint, format check, type check, 1004 tests, four deselected, 95.03% coverage. The browser tests ran. Two dotenv tests were not collected and four private-config tests were deselected to honor the review file restrictions.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or recorded-trip/count records were read. Touch behavior and native dropdown rendering were not independently exercised; the handoff's stated limitations remain.
