# Review: report-links

## Current state

- Status: complete
- Next action: open the PR
- Review target: `54ce8fb053d808a6bb2d0adb8cc5ecf746927e5c` -> `8990e13`
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `report-links-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-23T18:52Z`

## Handoff, round 1

- Tier: Small (links added to report and map text; no guard changed).
- Intended behavior and acceptance criteria (asked by the user: a link to
  the GitHub repository in the reports, and a link to the route-model
  details):
  - Under the report title (HTML and Markdown): links to the repository and
    to `docs/routing-methods.md` on GitHub, opened in a new tab in HTML.
  - Each route model in the Method section's "Route-choice presets" gets a
    "How it works" link to its own section of the methods page
    (`text.model_doc`, from `text.MODEL_SECTIONS`; a mode's `*` entry
    covers names not listed; an unknown mode gets the page itself).
  - The map panel ends with "Route model: <preset>, how it works ·
    About walkbike-routes" (`run.doc`, `D.repo`), on destination and
    all-destinations pages.
- Changed areas: `report/text.py` (`REPO`, `METHODS_DOC`, `MODEL_SECTIONS`,
  `model_doc`; `ROUTE_COST_DOC` now built from `METHODS_DOC`, same URL);
  `report/tables.py` (new `Away` block: text then external links, both
  renderers; `ABOUT`); `report/map.py`, `report/map.html`; tests.
- Verification:

  | Command | Revision | Result | Skips | Run by |
  | --- | --- | --- | --- | --- |
  | `uv run pytest tests/test_report.py -k "links or route_model"` | `8990e13` | 4 passed | none | implementer |
  | `make check` | `8990e13` | 1025 passed, 95.76% | none | implementer |

  New tests: `test_every_shipped_route_model_links_to_a_section_that_exists`
  (slugs every heading of `docs/routing-methods.md` as GitHub does and
  checks each shipped preset's anchor is among them),
  `test_the_report_links_the_repository_and_each_route_models_section`,
  `test_the_map_links_the_route_model_and_the_repository`.
- Settled decisions (do not re-report): links point at `main` on GitHub, so
  a report shows the current docs rather than the docs of the version that
  made it; the links line sits after the contents bar.
- Known limitations: a renamed heading in the methods page breaks only the
  anchor, not the page link; the anchor test catches it.

## Review, round 1

- Loop: `report-links-1`, round 1; Small tier retained.
- Target reviewed: `54ce8fb..8990e13` (`8990e1306c644830c390a07711974a07038a37ad`).
- Verdict: **accepted** at `8990e13`. No blocking findings.
- Reviewed the full five-file committed diff in an independent detached clone with its own Git metadata. The shared repository/methods constants preserve the route-cost URL; every shipped model maps to a section present in the committed methods document. The car wildcard and unknown-mode page fallback also pass independent probes. HTML and Markdown report link tests pass, and external HTML links use `target="_blank"` with `rel="noopener"`.
- Independent Chromium probes pass on school, downtown and all-destinations maps across their modes and full/shorter limits: the final panel paragraph contains the current model's documentation URL and repository URL with the required new-tab attributes.
- `git diff --check` and permitted `make check` pass: lint, formatting, types, 1019 tests, four deselected, 95.09% coverage. Two dotenv tests were not collected and four private-config tests were deselected to honor file restrictions. Browser tests ran.
- `make validation-check` passes: lint, formatting, types and 58 tests.
- No repository `data/`, `output/`, `.env`, `*.local.yaml`, or actual count/trip records were read. Additional maps used only the made-up demo outside the repository. Link targets and anchors were checked against committed docs; live GitHub availability was not tested. The settled choice to link current `main` documentation remains unchanged.
