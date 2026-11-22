"""Reusable hashing, artifact inventory, source inventory, and Git provenance."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pandas as pd


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for one file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(paths: Iterable[Path], *, project_root: Path | None = None) -> pd.DataFrame:
    """Record path, existence, size, and hash for material source artifacts."""
    rows: list[dict[str, object]] = []
    for path in sorted(set(paths)):
        source = str(path)
        if project_root is not None:
            try:
                source = str(path.relative_to(project_root))
            except ValueError:
                source = str(path)
        exists = path.is_file()
        rows.append(
            {
                "source": source,
                "exists": exists,
                "bytes": path.stat().st_size if exists else pd.NA,
                "sha256": sha256_file(path) if exists else pd.NA,
            }
        )
    return pd.DataFrame(rows)


def _git_value(project_root: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def git_provenance(project_root: Path) -> dict[str, str | bool | None]:
    """Return commit/branch/dirty state without mutating the repository."""
    status = _git_value(project_root, "status", "--porcelain")
    return {
        "git_commit": _git_value(project_root, "rev-parse", "HEAD"),
        "git_branch": _git_value(project_root, "branch", "--show-current"),
        "git_dirty": bool(status) if status is not None else None,
    }
