"""Police-recorded bike crashes: read from SWITRS-layout files, drawn on the maps only."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import Point

from walkbike_routes import __main__ as cli
from walkbike_routes import config, demo, switrs
from walkbike_routes.report import map as report

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"


def _crashes(points: list[Point], crs: Any) -> gpd.GeoDataFrame:
    """Made-up crash rows in the SWITRS layout: four bike crashes and one car-only."""
    return gpd.GeoDataFrame(
        {
            "CASE_ID": ["1", "2", "3", "4", "5"],
            "ACCIDENT_YEAR": ["2019", "2021", "2023", "2020", "2022"],
            "COLLISION_SEVERITY": ["4", "2", "1", "3", "4"],
            "BICYCLE_ACCIDENT": ["Y", "Y", "Y", "Y", None],
        },
        geometry=points,
        crs=crs,
    )


def _parties() -> pd.DataFrame:
    """Riders (type 4) and drivers (type 1); crash 2 has two riders, crash 4 an unknown age."""
    return pd.DataFrame(
        {
            "CASE_ID": ["1", "1", "2", "2", "2", "4"],
            "PARTY_TYPE": ["4", "1", "4", "4", "1", "4"],
            "PARTY_AGE": ["34", "9", "15", "11", "50", "998"],
        }
    )


def test_bike_crashes_keep_year_severity_and_the_youngest_rider(tmp_path: Path) -> None:
    _crashes([Point(i, i) for i in range(5)], 4326).to_parquet(tmp_path / "c.parquet")
    _parties().to_parquet(tmp_path / "p.parquet")
    read = switrs.bike_crashes(tmp_path / "c.parquet", tmp_path / "p.parquet")
    # The car-only crash is left out, and the worst come last, to be drawn on top.
    assert read[["year", "severity", "rider"]].values.tolist() == [
        [2019, "complaint of pain", "adult"],  # the driver's age 9 is not the rider's
        [2020, "other visible injury", "age not recorded"],  # 998: not stated
        [2021, "severe injury", "child (under 13)"],  # riders of 15 and 11: the youngest
        [2023, "killed", "age not recorded"],  # no parties row
    ]
    assert switrs.bike_crashes(tmp_path / "c.parquet").rider.eq("age not recorded").all()


def test_a_tims_csv_gives_its_points_and_skips_crashes_without_one(tmp_path: Path) -> None:
    table = pd.DataFrame(_crashes([Point(0, 0)] * 5, 4326).drop(columns="geometry"))
    table["POINT_X"] = ["-122.1", "-122.2", "0", "", "-122.3"]
    table["POINT_Y"] = ["37.3", "37.4", "0", "", "37.5"]
    table.to_csv(tmp_path / "c.csv", index=False)
    read = switrs.bike_crashes(tmp_path / "c.csv")
    assert read.crs == "EPSG:4326"
    assert sorted(zip(read.geometry.x, read.geometry.y, strict=True)) == [
        (-122.2, 37.4),
        (-122.1, 37.3),
    ]


def test_crash_files_without_their_columns_are_refused(tmp_path: Path) -> None:
    crashes = _crashes([Point(0, 0)] * 5, 4326)
    crashes.drop(columns="BICYCLE_ACCIDENT").to_parquet(tmp_path / "c.parquet")
    with pytest.raises(config.ConfigError, match="no BICYCLE_ACCIDENT column"):
        switrs.bike_crashes(tmp_path / "c.parquet")
    pd.DataFrame(crashes.drop(columns="geometry")).to_csv(tmp_path / "c.csv", index=False)
    with pytest.raises(config.ConfigError, match=r"no points \(POINT_X and POINT_Y\)"):
        switrs.bike_crashes(tmp_path / "c.csv")
    crashes.to_parquet(tmp_path / "ok.parquet")
    _parties().drop(columns="PARTY_AGE").to_parquet(tmp_path / "p.parquet")
    with pytest.raises(config.ConfigError, match="no PARTY_AGE column"):
        switrs.bike_crashes(tmp_path / "ok.parquet", tmp_path / "p.parquet")


def _study(folder: Path, private: str | None) -> Path:
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = [d for d in raw["destinations"] if d["id"] == "school"]
    raw["modes"] = ["walk"]
    raw["presets"] = {"walk": [raw["presets"]["walk"][0]]}
    raw["time_caps_min"] = {"walk": raw["time_caps_min"]["walk"]}
    path = folder / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    nodes = demo.build().nodes
    inside = nodes[nodes.geometry.within(nodes.union_all().envelope.buffer(-100))]
    xs, ys = inside.geometry.x, inside.geometry.y
    a, b = Point(xs.iloc[0], ys.iloc[0]), Point(xs.iloc[-1], ys.iloc[-1])
    far = Point(a.x + 50_000, a.y)
    _crashes([a, b, a, far, b], nodes.crs).to_crs(4326).to_parquet(folder / "crashes.parquet")
    _parties().to_csv(folder / "parties.csv", index=False)
    if private is not None:
        (folder / "study.local.yaml").write_text(private)
    return path


FILES = "recorded_crashes:\n  crashes: crashes.parquet\n  parties: parties.csv\n"


def _drawn(page: str) -> list[list[Any]]:
    found = re.search(r"const D=(\{.*?\});const CREDITS=", page, re.S)
    assert found
    data: dict[str, Any] = json.loads(found.group(1))
    return list(data["crashes"])


def test_recorded_crashes_are_drawn_on_each_map_and_only_inside_the_streets(
    tmp_path: Path,
) -> None:
    study = config.load(_study(tmp_path, FILES))
    assert study.recorded_crashes == tmp_path / "crashes.parquet"
    assert study.crash_parties == tmp_path / "parties.csv"
    out = tmp_path / "out"
    cli.build(study, out)
    assert "recorded_crashes.parquet" in json.loads((out / "manifest.json").read_text())["files"]
    report.build_maps(out)
    for page in [out / "school" / "map.html"]:  # one destination: no combined map
        text = page.read_text()
        # The far crash (2020) and the car-only one are left out. The two at
        # one point share a dot, worst first, and the worst place comes last.
        assert [row[2] for row in _drawn(text)] == [
            [[2021, "severe injury", "child (under 13)"]],
            [[2023, "killed", "age not recorded"], [2019, "complaint of pain", "adult"]],
        ]
        assert 'id="showcrashes"> Recorded crashes' in text


def test_without_crash_records_the_layer_is_empty_and_an_old_file_is_removed(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    cli.build(config.load(_study(tmp_path, FILES)), out)
    (tmp_path / "study.local.yaml").unlink()
    study = config.load(tmp_path / "study.yaml")
    assert study.recorded_crashes is None
    cli.build(study, out)
    assert not (out / "recorded_crashes.parquet").exists()
    report.build_maps(out)
    assert _drawn((out / "school" / "map.html").read_text()) == []


@pytest.mark.parametrize(
    ("private", "message"),
    [
        ("recorded_crashes:\n  crashes: gone.parquet\n", r"recorded_crashes\.crashes: .*gone"),
        ("recorded_crashes:\n  crashes: crashes.parquet\n  parties: gone.csv\n", "parties: .*gone"),
        ("recorded_crashes:\n  parties: parties.csv\n", "recorded_crashes needs crashes"),
        ("recorded_crashes:\n  crashes: crashes.parquet\n  victims: v.csv\n", "'victims'"),
    ],
)
def test_the_private_file_is_checked_when_the_config_loads(
    tmp_path: Path, private: str, message: str
) -> None:
    path = _study(tmp_path, private)
    with pytest.raises(config.ConfigError, match=message):
        config.load(path, local=True)
    assert config.load(path, local=False).recorded_crashes is None


@pytest.mark.parametrize("key", ["recorded_crashes", "crash_parties"])
def test_the_main_config_may_not_name_crash_records(key: str) -> None:
    raw = yaml.safe_load(DEMO.read_text())
    with pytest.raises(config.ConfigError, match=f"unknown setting '{key}'"):
        config.parse({**raw, key: "crashes.parquet"}, base=DEMO.parent)
