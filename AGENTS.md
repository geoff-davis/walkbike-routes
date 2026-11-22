# Notes for coding agents

`CLAUDE.md` imports this file; edit here only.

## Setup and before starting work

```bash
uv sync --locked
make hooks            # once per clone: commit and push hooks (prek)
git fetch origin
git log --oneline main..origin/main   # any output means local main is stale
```

Branch from `origin/main`; the hooks refuse commits to `main`, and the
`check-branch-fresh` pre-push hook refuses a branch missing commits from
`origin/main`. Work reaches `main` through pull requests.

## Commands

| Task | Command |
| --- | --- |
| Format the files you touched | `make format FILES="path ..."` |
| Format the whole tree (maintenance only, its own commit) | `make format-all` |
| Lint and format check | `make lint` |
| Type check | `make typecheck` |
| Tests | `make test` |
| Everything (definition of done) | `make check` |
| The validation folder | `make validation-check` |
| Known vulnerabilities in locked dependencies | `make audit` |
| One test | `uv run pytest tests/test_x.py -k name` |

`make help` lists every target. Hooks and CI call the same commands; use them
instead of hand-written equivalents.

## Before every commit

1. `make format FILES="..."` on every touched Python file and restage it, then
   `make check`. If the change touches `validation/` or anything it imports
   from `src/`, also run `make validation-check`. Do not commit while the
   hooks fail and do not use `--no-verify`.
2. Unless every staged file is on the exempt list below, get an independent
   review of the staged change from the Codex CLI, run read-only on a copy of
   the staged files:

   ```bash
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

Exempt from the Codex review, decided by path (`git diff --cached
--name-only`), not by whether the change feels behavioral: `docs/**`, `*.md`
other than `AGENTS.md`, and `uv.lock` alone. Agent instructions, config,
hooks and CI are reviewed.

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

## Gotchas

- `validation/` is not in `make check`; it went stale unnoticed (an import
  through a module that did not export it, a test expecting a snap limit the
  core had dropped). CI now runs its type check and tests.
- The hook revs for `pre-commit-hooks` and `markdownlint-cli2` are not
  updated by Dependabot; run `uv run prek autoupdate` now and then.
