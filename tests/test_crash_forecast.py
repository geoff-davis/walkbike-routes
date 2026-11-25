"""Another model's crash forecast, drawn as a map layer from the private config."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import Point, box

from walkbike_routes import __main__ as cli
from walkbike_routes import config, demo, layers
from walkbike_routes.report import map as report

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"


def _forecast(path: Path) -> None:
    """Made-up corners: two in the demo town, one far outside, one with no forecast."""
    nodes = demo.build().nodes
    xs, ys = nodes.geometry.x, nodes.geometry.y
    inside = nodes.geometry.within(nodes.union_all().envelope.buffer(-100))  # off the edge
    xs, ys = xs[inside], ys[inside]
    a, b = Point(xs.iloc[0], ys.iloc[0]), Point(xs.iloc[-1], ys.iloc[-1])
    made = gpd.GeoDataFrame(
        {
            "rank": [2, 1, 3, 4],
            "streets": ["Made-up St & Other St", "Made-up Ave & Side St", "Far Rd", "Blank Ln"],
            "expected_bike_crashes": [0.4, 1.6, 9.0, None],
            "observed_bike_crashes": [0, 2, 5, 1],
        },
        geometry=[a, b, Point(a.x + 50_000, a.y), a],
        crs=nodes.crs,
    ).to_crs(4326)
    made.to_file(path, driver="GeoJSON")


def _study(folder: Path, *, forecast: bool) -> Path:
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = [d for d in raw["destinations"] if d["id"] == "school"]
    raw["modes"] = ["walk"]
    raw["presets"] = {"walk": [raw["presets"]["walk"][0]]}
    raw["time_caps_min"] = {"walk": raw["time_caps_min"]["walk"]}
    path = folder / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    if forecast:
        _forecast(folder / "forecast.geojson")
        (folder / "study.local.yaml").write_text("crash_forecast: forecast.geojson\n")
    return path


def _crash(page: str) -> list[list[Any]]:
    found = re.search(r"const D=(\{.*?\});const CREDITS=", page, re.S)
    assert found
    data: dict[str, Any] = json.loads(found.group(1))
    return list(data["crash"])


def test_the_forecast_is_drawn_on_each_map_and_only_inside_the_streets(tmp_path: Path) -> None:
    study = config.load(_study(tmp_path, forecast=True))
    assert study.crash_forecast == tmp_path / "forecast.geojson"
    out = tmp_path / "out"
    cli.build(study, out)
    assert "crash_forecast.parquet" in json.loads((out / "manifest.json").read_text())["files"]
    report.build_maps(out)
    points = _crash((out / "school" / "map.html").read_text())
    # Most expected first; the far corner and the one without a forecast are left out.
    assert [(p[2], p[3], p[4], p[5]) for p in points] == [
        (1, 1.6, 2, "Made-up Ave & Side St"),
        (2, 0.4, 0, "Made-up St & Other St"),
    ]
    assert 'id="showcrash"> Crash forecast' in (out / "school" / "map.html").read_text()


def test_without_a_forecast_the_layer_is_empty_and_an_old_file_is_removed(tmp_path: Path) -> None:
    out = tmp_path / "out"
    cli.build(config.load(_study(tmp_path, forecast=True)), out)
    (tmp_path / "study.local.yaml").unlink()
    study = config.load(tmp_path / "study.yaml")
    assert study.crash_forecast is None
    cli.build(study, out)
    assert not (out / "crash_forecast.parquet").exists()
    report.build_maps(out)
    assert _crash((out / "school" / "map.html").read_text()) == []


def test_a_forecast_without_its_columns_or_coordinates_is_refused(tmp_path: Path) -> None:
    study = replace(config.load(DEMO), crash_forecast=tmp_path / "f.geojson")
    area = box(-1e9, -1e9, 1e9, 1e9)
    gpd.GeoDataFrame({"rank": [1]}, geometry=[Point(0, 0)], crs=4326).to_file(
        tmp_path / "f.geojson", driver="GeoJSON"
    )
    with pytest.raises(config.ConfigError, match="no expected_bike_crashes column"):
        layers.crash_forecast(study, "EPSG:32610", area)
