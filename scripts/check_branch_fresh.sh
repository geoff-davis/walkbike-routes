#!/usr/bin/env bash
# Pre-push guard: fail when origin/main has commits that are not in the
# history of the branch being pushed — i.e. the branch was cut from (or has
# drifted behind) a stale main, including when main moves mid-session.
# See AGENTS.md "Before starting work".
#
# Limitation: this checks HEAD, not the refs actually being pushed (which
# arrive on stdin). Pushing a non-checked-out branch validates the wrong
# ref; acceptable for the single-developer workflow this guards.
set -u

# Fail open when offline: a fetch failure must not block pushing.
if ! git fetch --quiet origin main 2>/dev/null; then
    echo "check-branch-fresh: could not fetch origin/main (offline?); skipping check" >&2
    exit 0
fi

# Compare against the commit just fetched (FETCH_HEAD), not refs/remotes/origin/main:
# a single-branch clone, or one with a narrowed fetch refspec, has no such
# ref or leaves it stale even though the fetch succeeded.
if ! main=$(git rev-parse --verify --quiet 'FETCH_HEAD^{commit}'); then
    echo "check-branch-fresh: fetched origin main but cannot resolve it; refusing" >&2
    exit 1
fi
if ! behind=$(git rev-list --count "HEAD..${main}"); then
    echo "check-branch-fresh: cannot compare HEAD with origin main; refusing" >&2
    exit 1
fi
if [ "${behind}" -gt 0 ]; then
    echo "check-branch-fresh: origin main has ${behind} commit(s) missing from this branch:" >&2
    git log --oneline "HEAD..${main}" | head -10 >&2
    echo "" >&2
    echo "The branch is based on a stale main. Rebase before pushing:" >&2
    echo "    git fetch origin main && git rebase FETCH_HEAD" >&2
    echo "Or, if being behind is intentional, skip once with:" >&2
    echo "    SKIP=check-branch-fresh git push" >&2
    exit 1
fi
exit 0
