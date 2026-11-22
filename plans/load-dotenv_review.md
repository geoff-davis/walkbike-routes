# Review: load-dotenv

## Current state

- Status: complete (accepted at 855e001)
- Next action: none; PR with Review line
- Review target: `c9e3e22` -> `855e001` (branch `load-dotenv`)
- Open findings: none
- Decisions needed from the user: none

## Loop

- Loop ID and round: `load-dotenv-1`, round 1 of at most 3
- Implementer (the reviewer replies here): `walkbike-implementer` (Claude), pane `wG:p1`
- Reviewer: `walkbike-reviewer` (Codex), pane `wG:p2`
- Authorized scope: review requests and replies only
- Expires (UTC): `2026-11-22T20:27Z`

## Handoff, round 1

- Tier: Standard (handles secrets)
- Intended behavior and acceptance criteria:
  - `walkbike-routes run` reads `NAME=value` lines from `./.env` before loading the config; `report` does not.
  - An existing environment variable is never overridden. Blank lines and `#` comments are skipped; `export NAME=value` and matching single or double quotes are accepted.
  - No value is ever logged or put in an error: the log lists names only; a malformed line raises `ConfigError` naming the line number, and the CLI exits 2.
  - Tests never read a developer's `.env`: `tests/conftest.py` replaces `cli.load_env_file` with a no-op for every test; `tests/test_env_file.py` tests the real function by direct import.
  - No new dependency; `uv.lock` unchanged.
- Evidence: new behavior (no base equivalent): `test_env_file.py` covers precedence, comments, quotes, `export`, missing file, and a bad line whose text is not in the error.
- Verification: `make check` 995 passed, 95.71%; markdownlint on README.md and docs/adapting.md; hooks passed.
- Reviewer note: do not open the main checkout's `.env` (AGENTS.md); the clone has none.
- Known limitations: no multi-line values or escape sequences inside quotes; inline `# comments` after a value are kept as part of the value.

Append "## Review, round 1" with a verdict naming `855e001`, then send one prompt to the implementer named above.

## Review, round 1

- Loop: `load-dotenv-1`, round 1; Standard tier retained.
- Target reviewed: `c9e3e22` -> `855e001d2f786416ae2465da8d549892832dc0c3`.
- Verdict: **accepted** at `855e001`. No confirmed correctness defects or open findings.
- Snapshot: independent local clone detached at the exact target. The implementer's checkout was not modified; this handoff is the only file written there. No `.env` file was opened or read during review.
- Reviewer checks: cached locked dependency sync and `git diff --check` passed. Ran `make check` with `PYTEST_ADDOPTS='--ignore=tests/test_env_file.py'` to honor the instruction not to read any `.env`, including the two tests' synthetic files: lint, formatting, mypy and **993 tests** passed, coverage **95.46%**. `make validation-check` passed lint, formatting, mypy and **58 tests**. Browser tests ran with normal local process permissions.
- Independently exercised the real loader using only synthetic in-memory text returned by mocked `Path.read_text`, with no filesystem content reads. Probes passed for shell precedence, blank/comment lines, plain values, both quote types, embedded equals in a quoted value, `export`, whitespace, empty values, missing input, and a malformed line whose value stays out of the error.
- CLI integration probes passed: an in-memory malformed input exits 2 and names its line without echoing its text; `run` invokes the loader before config parsing; `report` bypasses the loader. The success log formats only the returned validated variable names. The autouse fixture prevents existing core CLI tests from invoking the real file loader; the direct-import parser tests exercise the actual function as intended.
- Diff inspection confirms no new dependency or lockfile change. Documentation describes the current working directory and environment precedence consistently with the code.
- Evidence reused: implementer's unfiltered `make check` result (995 tests, 95.71% coverage), Markdown checks and commit-hook result.
- Verification limits: the reviewer did not execute the two file-opening tests in `tests/test_env_file.py`; their parser behavior was checked through the independent in-memory probes above. Real file contents and developer credentials were not inspected, and no network service was contacted. No further review round requested.
