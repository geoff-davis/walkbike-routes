# Review: routes-meet-risk

## Current state

- Status: ready for reviewer
- Next action: reviewer verifies round 2
- Review target: `ab22950` -> `ea5f168`
- Open findings: RMR1 (fixed in ea5f168, to verify)
- Decisions needed from the user: none

## Loop

- Loop ID and round: `routes-meet-risk-1`, round 2 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-26 20:51`

## Handoff, round 1

- Tier: Standard (an output value changes: the `tier` column)
- Intended behavior and acceptance criteria:
  - The top exposure tier is `"priority"` instead of `"fix first"`
    everywhere it is written or shown: `exposure_streets.parquet` and
    `exposure_crossings.parquet` `tier`, the report, the map page (badges,
    legend, tables, toggle), the rule sentences and key terms, README and
    docs. `"watch"` is unchanged.
  - The exposure section and per-run lists are headed "Where routes meet
    risk" (was "Where routes meet traffic today" and "places to fix first").
  - No text a reader sees still says "fix first" (`git grep -i
    "fix[- ]first"` outside `plans/` returns nothing).
  - Internal identifiers (`FIX_FIRST`, `fix_first_rule`, the manifest's
    `exposure.fix_first`, the page data's `fix_first` counts) and the config
    key `exposure.fix_first` are unchanged, so existing configs parse.
- Changed areas: wording in `analysis/exposure_*.py`, `config_models.py`,
  `report/map.html`, `report/map.py`, `report/tables.py`, `report/text.py`,
  README, `docs/adapting.md`, `docs/assumptions.md`, and test expectations.
- Pattern inventory: every comparison against the tier string goes through
  either `text.FIX_FIRST` or the literal, and all literals were replaced
  together (Python and `map.html`, including `TIER_COL` keys).
- Verification:

  | Command | Revision | Result | Run by |
  | --- | --- | --- | --- |
  | `make check` | `2d4f391` | 1078 passed | implementer |
  | `make validation-check` | `2d4f391` | 58 passed | implementer |
  | exposure and report tests with `src/` stashed to base | base src, head tests | 26 failed, 121 passed | implementer |

- Settled decisions (do not re-report): the names "priority" and "Where
  routes meet risk" are the user's choice; internal names and the config key
  stay to avoid breaking configs.
- Known limitations and out of scope: `docs/images/claremont-traffic-today.png`
  still shows the old labels until the screenshot is retaken; runs written
  before this change hold `"fix first"` in their parquet `tier` column and
  should be rerun (the map would draw their top tier in the watch colour).

## Review, round 1

- Loop: `routes-meet-risk-1`, round 1, Standard tier.
- Target: `ab22950..2d4f391` (`2d4f39126ee6d0fd730468cd3a2900e313aece56`); handoff read from `fc928ee`.
- Verdict: **changes requested at `2d4f391`**. One small confirmed acceptance failure; no functional defects or hypotheses reported.

### RMR1 — P3, blocking for the explicit wording criterion: wrapped old label remains in the docs

- Location: `docs/assumptions.md:1016`–`:1017`.
- Trigger: read the `busiest_share` explanation. The quoted example still starts `Fix` at the end of one line and `first: among the busiest ...` on the next. Markdown renders this as the old `Fix first:` label, while the code now emits `Priority:`.
- Violated criterion: no reader-facing text may still say the old label, including rule sentences and docs. This is outside the expressly acknowledged screenshot exception.
- Evidence: a whitespace-aware scan using case-insensitive `fix[-\s]+first` found exactly one occurrence in the target's source/tests/Markdown docs/README: `Fix\nfirst` here. The handoff's line-oriented grep misses it. The same scan on base docs found four occurrences; this one was left unchanged. This is incomplete rename coverage rather than a newly introduced regression.
- Requested fix: change the example's label to `Priority:` and repeat the text scan with whitespace normalized or multiline matching. No behavioral test is needed for this prose correction.

### Verification and limits

- Independent detached snapshot at the target; implementer tree unchanged apart from this review entry.
- `uv sync --locked --offline` and `make check` passed: lint, format, types; 1,055 tests passed, 21 deselected, 94.59% coverage. Three existing multiprocessing fork warnings. `make validation-check` passed, 58 tests. `git diff --check ab22950..2d4f391` passed.
- Base/head synthetic demo builds: all 12 exposure tables matched exactly after normalizing only the intended tier/reason wording. Numerical values, ranks, schemas and geometry were unchanged. Base tiers were `fix first`/`watch`; head tiers were `priority`/`watch`. The head report and generated maps contained no old phrase and used the new headings. The manifest's internal `exposure.fix_first` key remained present. Inspected Python and JavaScript tier comparisons consistently use the new value.
- Privacy exclusions: `tests/test_env_file.py` ignored; the 21 parameterized config, forecast and recorded-crash cases that load companion private YAML files deselected. No `.env`, `*.local.yaml`, repository data/output, or real records read. All extra builds used made-up demo inputs in temporary folders.
- The acknowledged old screenshot and old-output rerun limitation were not re-reported. Review snapshots and synthetic outputs removed after the verdict.

## Handoff, round 2

- Target: `ab22950` -> `ea5f168` (fix commit `ea5f168` on top of `2d4f391`).
- RMR1 fixed: the `busiest_share` example in `docs/assumptions.md` now reads
  "Priority: among the busiest 10% …"; the paragraph is reflowed.
- Whitespace-aware scan: `git ls-files -z src tests docs README.md '*.md' |
  xargs -0 perl -0ne 'while(/fix[-\s]+first/gi){print "$ARGV\n"}'` outside
  `plans/` prints nothing at `ea5f168`.
- Prose only; `markdownlint-cli2` passes on the file.
