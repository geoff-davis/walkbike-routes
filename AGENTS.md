# Notes for coding agents

## Before every commit

1. Run `ruff format` on every touched Python file and restage it, then
   `make check` (lint, types, tests). If the change touches `validation/`,
   also run `make validation-check`.
2. Get an independent review of the staged change from the Codex CLI, run
   read-only on a copy of the staged files:

   ```
   git diff --cached > "$SCRATCH/staged.diff"
   git checkout-index --all --prefix="$SCRATCH/tree/"   # the staged files, not HEAD
   codex exec --sandbox read-only --skip-git-repo-check -C "$SCRATCH" \
       -o "$SCRATCH/review.md" - < "$SCRATCH/prompt.txt"
   ```

   `$SCRATCH` is a temporary folder outside the repository. The prompt asks
   for correctness bugs in the diff, with a concrete failing case for each.
3. Test each finding before acting on it. The reviewer cannot run the code.
   Fix what is confirmed, or say in the commit report what was left and why.
4. Then commit.

## What may be sent for review

Staged files and the staged diff only. Never anything under `data/` or
`output/`, never count or recorded-trip records (`validation/README.md`), and
never `.env`.

## Data

- No downloaded data, city layers or results are committed. They live in the
  git-ignored `data/` and `output/` folders.
- `validation/` is an experiment kept apart from the package: nothing under
  `src/` or `tests/` may import it.
- Tests use the made-up demo town (`walkbike_routes.demo`) or small made-up
  tables.
- Local layers a city supplies (sidewalks, signals, address points, school
  boundaries, reviewed gates) are read from paths named in a study config and
  stay out of the repository.

## Design rules

- **The core is destination-neutral.** Nothing under `src/walkbike_routes`
  names a particular city, school or state. California-only sources are
  optional adapters.
- **Route choice comes from named presets** in `presets/`, each with its
  citation. A coefficient taken from a paper is checked against the paper, and
  a test reproduces a small worked example from it.
- **Physical time and generalized cost are kept apart.** Time limits use
  physical time. Route choice uses generalized cost. Reports say "generalized
  minutes" for the latter.
- **Outputs say what they are:** potential flow, not counts, forecasts or risk.
