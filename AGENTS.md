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
2. Commit. Unless the slice is exempt (below), it then goes to the reviewer
   before it is pushed.

## Review: Claude implements, Codex reviews

The user authorizes direct review requests and replies through Herdr; no
manual relay is needed. This covers message delivery only. A review verdict
does not authorize a commit, push, merge, tag or publish.

Live agent names, in the walkbike-routes Herdr workspace:
`walkbike-implementer` (Claude) and `walkbike-reviewer` (Codex). Before every
send, verify `HERDR_ENV=1` and check `herdr agent list` that the peer is the
Codex in this workspace with a walkbike-routes working directory. Names are
cleared when an agent exits; rename with `herdr agent rename <pane> <name>`.
Never send to another workspace's reviewer, and if identity is ambiguous,
stop and ask.

**When.** Once per PR-sized slice: after the local commit, before the push.
Several small commits may share one review. Exempt, decided by path
(`git diff --name-only <base>..<head>`), not by whether the change feels
behavioral: `docs/**`, `*.md` other than `AGENTS.md`, `uv.lock` alone, and a
Dependabot PR with green CI. Agent instructions, config, hooks and CI are
reviewed. Every PR description has a `Review:` line:
`plans/<task>_review.md, accepted at <head SHA>` or `exempt: <which rule>`.

### What the reviewer may read

Committed files at the review target only, taken with `git archive`, which
leaves out everything git ignores. Never anything under `data/` or `output/`,
`.env`, `*.local.yaml`, or count or recorded-trip records
(`validation/README.md`). Reading a file sends it to the reviewer's model
provider, so this applies to the main checkout too, not just the snapshot.

### When implementing

- Commit the slice with normal hooks; that commit is the review target. Do
  not amend it during review; answer findings with new commits.
- Write the handoff to `plans/<task>_review.md`
  ([template](plans/review-handoff.md)) before prompting: base and head SHAs,
  tier, acceptance criteria, evidence (each fix with its regression test shown
  failing on the base), settled decisions, known limitations.
- `herdr agent prompt walkbike-reviewer "<absolute handoff path, target, loop
  and round>"`, without `--wait`, and only when the reviewer is idle or done.
- Poll the handoff file in the background about once a minute until it holds
  a verdict naming this loop, this round and the exact target, or until the
  loop's UTC expiry. An older verdict does not count. Agent states are hints:
  `idle` or `done` may predate the request. If the reviewer is blocked or
  settles with no matching verdict, inspect it with `herdr agent read`; do
  not resend. At expiry, stop and tell the user.
- Test each finding before acting on it. Fix blocking findings; record the
  rest as non-blocking with a home (an issue number for deferred ones).

### When reviewing

- Read-only. Snapshot the target outside the repository:
  `mkdir -p /tmp/walkbike-review/<task>-r<round> && git archive <head SHA> |
  tar -x -C /tmp/walkbike-review/<task>-r<round>`, run checks there
  (`uv sync --locked`, `make check`), and delete it after the verdict. Never
  stash, reset or edit the implementer's tree; the handoff file is the only
  thing you write to.
- Run the same probe on base and head before calling something a regression.
- Each finding: stable ID, severity, blocking or not, location, trigger,
  violated invariant, evidence. Separate confirmed defects from hypotheses.
- A finding blocks only if it defeats the acceptance criteria or introduces a
  regression.
- Append the round and a verdict naming the exact target to the handoff file,
  then send one prompt to `walkbike-implementer`.

### Tiers and limits

The implementer declares the tier; the reviewer may raise it with a reason.
Small: a local fix that adds, removes or loosens no guard, or docs; one round.
Standard: new or changed logic or contracts; one round plus one to verify
fixes. Critical: data-loss guards, destructive operations, security; up to
three. Three submissions per loop and 60 minutes, both recorded in the
handoff. No acknowledgment messages. A blocked approval dialog goes to the
user. Stop and tell the user on acceptance, the third round, expiry, a stall,
or ambiguous identity.

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
- Review records in `plans/` are committed to a public repository: they
  follow the same rules as any other commit.
- The hook revs for `pre-commit-hooks` and `markdownlint-cli2` are not
  updated by Dependabot; run `uv run prek autoupdate` now and then.
