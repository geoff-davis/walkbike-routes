# Review: TASK

Copy to `plans/<task>_review.md` when the first handoff is ready and replace
`TASK` in the heading. One file per
task or PR. The implementer and reviewer take turns appending; neither rewrites
the other's text. Move to `plans/archive/` when the task completes.

## Current state

Updated in place each round.

- Status: ready for reviewer | reviewing | ready for implementer | complete
- Next action:
- Review target: `<base SHA>` -> `<head SHA>`
- Open findings:
- Decisions needed from the user: none

## Loop

- Loop ID and round: `<task>-1`, round 1 of at most 3
- Implementer: `walkbike-implementer` (Claude), pane `<id>`
- Reviewer: `walkbike-reviewer` (Codex), pane `<id>`
- Authorized scope: review requests and replies only
- Expires (UTC): `<start + 60 min>`

## Handoff, round 1

- Tier: Small | Standard | Critical
- Intended behavior and acceptance criteria:
- Changed areas and affected producers and consumers:
- Pattern inventory (class-level fixes): every occurrence and how it is handled
- Verification: command | revision | result | skips | run by
- Condition inventory (Standard: new or changed conditions; Critical: all):

  | Condition | Test or probe | Caught / survived / not exercised | Note |
  | --- | --- | --- | --- |

- Settled decisions (do not re-report):
- Known limitations and out of scope:

## Review, round 1

- Target reviewed:
- Checks run by the reviewer:
- Evidence reused from the implementer:
- Verification gaps:
- Verdict: accepted | changes required

## Finding ledger

| ID | Severity | Blocking | Location | Status | Evidence, fix commit, test |
| --- | --- | --- | --- | --- | --- |

Status is one of: open, fixed, deliberately unchanged, deferred, withdrawn.
Each non-blocking finding names its recommended home. A deferred finding
names its issue number; it is not deferred until it has one.
