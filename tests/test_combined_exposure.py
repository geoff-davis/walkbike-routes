"""Every destination's routes added up: the lists, the report section and the map page."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
import yaml

from walkbike_routes import __main__ as cli
from walkbike_routes import config
from walkbike_routes.analysis.exposure import ALL_DESTINATIONS, STREET_COLUMNS
from walkbike_routes.report import map as report
from walkbike_routes.report import tables
from walkbike_routes.report import text as words

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"


def _build(folder: Path, destinations: int) -> Path:
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = raw["destinations"][:destinations]
    raw["modes"], raw["presets"] = ["walk", "bike"], {"walk": ["shortest"], "bike": ["shortest"]}
    path = folder / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    cli.build(config.load(path), folder / "out")
    return folder / "out"


@pytest.fixture(scope="module")
def out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory.mktemp("combined"), 2)


def test_the_lists_add_up_every_destination(out: Path) -> None:
    ids = ["school", "downtown"]
    for mode in ("walk", "bike"):
        together = gpd.read_parquet(
            out / ALL_DESTINATIONS / f"{mode}-shortest" / "exposure_streets.parquet"
        )
        assert list(together.columns) == STREET_COLUMNS
        assert len(together)
        for text, total in zip(together.people_by_destination, together.people, strict=True):
            people = json.loads(str(text))
            assert set(people) <= set(ids)
            assert sum(people.values()) == pytest.approx(total)
        # A destination's own list says nothing about others.
        alone = gpd.read_parquet(out / "school" / f"{mode}-shortest" / "exposure_streets.parquet")
        assert alone.people_by_destination.eq("").all()


def test_the_report_and_map_show_where_people_come_from(out: Path) -> None:
    paths = report.build_maps(out)
    text = tables.build_report(out)[0].read_text()
    assert "## All destinations together" in text
    together = text.split("## All destinations together", 1)[1].split("\n## ", 1)[0]
    assert "[Open the map](all-destinations/map.html)" in together
    # Walking before cycling, whatever order the folders sort in.
    assert together.index("### Walking: places to fix first") < together.index(
        "### Cycling: places to fix first"
    )
    assert "From each destination" in text
    assert text.index("## All destinations together") < text.index("## Demo Elementary")
    assert out / ALL_DESTINATIONS / "map.html" in paths
    page = (out / ALL_DESTINATIONS / "map.html").read_text()
    found = re.search(r"const D=(\{.*?\});const CREDITS=", page, re.S)
    assert found
    data: dict[str, Any] = json.loads(found.group(1))
    assert data["combined"] and len(data["places"]) == 2
    # Each shape carries its own name: the school is a point, Downtown an area.
    kinds = {p["name"]: "point" in p for p in data["places"]}
    assert kinds == {"Demo Elementary": True, "Downtown": False}
    swapped = report.combined_page_data(
        out, tables.read_summary(out), gpd.read_parquet(out / "links.parquet"),
        gpd.read_parquet(out / "destinations.parquet").iloc[::-1],
        {out / name for name in json.loads((out / "manifest.json").read_text())["files"]},
    )  # fmt: skip
    assert swapped is not None
    assert {p["name"]: "point" in p for p in swapped["places"]} == kinds
    assert [r["mode"] for r in data["runs"]] == ["walk", "bike"]
    assert all(r["exposure"] and r["flow"] for r in data["runs"])
    assert any(e["from"] for r in data["runs"] for e in r["exposure"])


def test_one_destination_has_no_lists_together(tmp_path: Path) -> None:
    out = _build(tmp_path, 1)
    assert not (out / ALL_DESTINATIONS).exists()
    assert "All destinations together" not in tables.build_report(out)[0].read_text()
    assert report.build_maps(out) == [out / "school" / "map.html"]


def test_a_sliver_from_a_destination_is_not_shown_as_none() -> None:
    labels = {"a": "Almond", "e": "Egan"}
    text = words.from_each(json.dumps({"a": 99.0, "e": 0.01}), labels)
    assert text == "Almond 99 (100%); Egan fewer than 1 (under 1%)"
    assert words.from_each(json.dumps({"a": 1.25, "e": 3.75}), labels) == (
        "Almond 1 (25%); Egan 4 (75%)"
    )
    assert words.from_each(json.dumps({"a": 0.0, "e": 0.0}), labels) == ""
    assert words.from_each("", labels) == "" and words.from_each(None, labels) == ""


def test_a_destination_called_all_destinations_is_not_read_as_the_lists(tmp_path: Path) -> None:
    # Its own runs sit where the lists would; the run then writes no lists (study.py).
    summary = pd.DataFrame(
        {"destination_id": ["school", ALL_DESTINATIONS], "mode": "walk", "preset": "shortest"}
    )
    run = tmp_path / ALL_DESTINATIONS / "walk-shortest"
    listed = {run / "exposure_streets.parquet", run / "exposure_crossings.parquet"}
    assert tables.combined_blocks(tmp_path, summary, {}, listed) == []
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:26910")
    assert report.combined_page_data(tmp_path, summary, empty, empty, listed) is None


def test_the_run_records_and_prints_its_rules(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = raw["destinations"][:1]
    raw["modes"], raw["presets"] = ["bike"], {"bike": ["shortest"]}
    raw["time_caps_min"] = {"bike": 15}
    raw["exposure"] = {"fix_first": {"min_points": 1, "busiest_share": 0.2}}
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    out = tmp_path / "out"
    manifest = json.loads(cli.build(config.load(path), out).read_text())
    assert manifest["exposure"]["fix_first"] == {
        "min_points": 1, "min_people": 5, "min_share": 0.05, "busiest_share": 0.2,
    }  # fmt: skip
    text = tables.build_report(out)[0].read_text()
    assert "## Risk points used" in text
    assert "**Fix first:** A place with 1 or more risk points" in text and "busiest 20%" in text
    # With a second way to be fix first, each place says which applied to it.
    first = text.split("places to fix first", 1)[1].split("\n## ", 1)[0]
    assert "| Priority |" in first
    page = report.build_maps(out)[0].read_text()
    assert "Fix first: 1 or more risk points" in page
