# mypy: disable-error-code="arg-type,var-annotated,index,call-overload,assignment,misc"
# The pandas and shapely stubs reject these tests' table idioms.
"""Tests for shared output, manifest, text, and provenance infrastructure."""

from __future__ import annotations

from pathlib import Path

from walkbike_routes.outputs import (
    git_provenance,
    sha256_file,
    source_manifest,
)


def test_source_manifest_hashes_what_exists(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    first.write_text("alpha")
    sources = source_manifest((first, tmp_path / "missing.txt"), project_root=tmp_path)
    by_source = sources.set_index("source")
    assert by_source.loc["first.txt", "exists"]
    assert by_source.loc["first.txt", "sha256"] == sha256_file(first)
    assert not by_source.loc["missing.txt", "exists"]


def test_git_provenance_is_read_only() -> None:
    provenance = git_provenance(Path(__file__).parents[1])
    assert isinstance(provenance["git_commit"], str)
    assert isinstance(provenance["git_dirty"], bool)
