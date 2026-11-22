"""Local layers on a made-up grid, checked by hand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from walkbike_routes import config, local_layers
from walkbike_routes.network import prepare

CRS = 26910


def _streets() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    # Main runs east-west from A to C through B; Side runs south from B to D.
    links = gpd.GeoDataFrame(
        {
            "link_id": ["main-1", "main-2", "side"],
            "u": ["A", "B", "B"],
            "v": ["B", "C", "D"],
            "name": ["Main", "Main", "Side"],
            "highway": ["secondary", "secondary", "residential"],
            "lanes": [None, None, None],
            "speed_mph": [None, "30 mph", None],
            "bike_facility": ["none", "none", "none"],
            "sidewalk": ["none", "unknown", "unknown"],
        },
        geometry=[
            LineString([(0, 0), (100, 0)]),
            LineString([(100, 0), (200, 0)]),
            LineString([(100, 0), (100, -30)]),
        ],
        crs=CRS,
    )
    nodes = gpd.GeoDataFrame(
        {"node_id": ["A", "B", "C", "D"], "control": ["none"] * 4},
        geometry=[Point(0, 0), Point(100, 0), Point(200, 0), Point(100, -30)],
        crs=CRS,
    )
    return links, nodes


def _write(tmp_path: Path, name: str, rows: list[dict[str, Any]], geometry: list[Any]) -> Path:
    path = tmp_path / name
    frame = gpd.GeoDataFrame(rows or None, geometry=geometry, crs=CRS)
    if path.suffix == ".parquet":
        frame.to_parquet(path)
    else:
        frame.to_file(path)
    return path


def _layer(kind: str, file: Path, **extra: Any) -> config.LocalLayer:
    raw = {"local_layers": [{"kind": kind, "file": str(file), **extra}]}
    return _parse(raw).local_layers[0]


def _parse(extra: dict[str, Any]) -> config.Study:
    raw = {
        "study": "t", "area": {"source": "place", "place": "Somewhere, Oregon"},
        "destinations": [{"id": "d", "name": "D", "kind": "point", "location": [-122.1, 37.4]}],
        "modes": ["walk"], "presets": {"walk": ["legacy"]}, "time_caps_min": {"walk": 10},
        "demand": {"weight": "residents"}, **extra,
    }  # fmt: skip
    return config.parse(raw)


def test_a_sidewalk_marks_the_street_it_runs_along_not_the_cross_street(tmp_path: Path) -> None:
    links, nodes = _streets()
    # 8 m south of Main, west of Side.  Side's midpoint (100, -15) is 7 m from
    # the sidewalk's end but runs across it, so it gets nothing.
    path = _write(tmp_path, "sw.parquet", [{}], [LineString([(5, -8), (100, -8)])])
    new, _, counts = local_layers.apply(links, nodes, [_layer("sidewalks", path)])
    assert new.sidewalk.tolist() == ["local", "unknown", "unknown"]
    assert new.sidewalk_source.tolist() == ["sw.parquet", "OpenStreetMap", "OpenStreetMap"]
    assert counts == {"sw.parquet": 1}
    status = prepare.physical_edges(new).set_index("physical_edge_id").pedestrian_status
    # OSM said "no sidewalk" on Main; the city's layer overrules it.
    assert status.to_dict() == {"main-1": "supported", "main-2": "unknown", "side": "unknown"}
    assert links.sidewalk.iloc[0] == "none"  # the input is left alone


def test_the_nearest_parallel_feature_gives_the_value(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = _write(
        tmp_path, "speed.parquet",
        [{"SPEED": 40}, {"SPEED": 25}, {"SPEED": 0}, {"SPEED": 35}],
        [LineString([(0, 12), (100, 12)]), LineString([(0, 3), (100, 3)]),
         LineString([(100, 2), (200, 2)]), LineString([(95, -5), (95, -25)])],
    )  # fmt: skip
    new, _, _ = local_layers.apply(links, nodes, [_layer("speeds", path, column="SPEED")])
    # Main west: 25 (3 m) beats 40 (12 m); Main east: 0 means none, OSM's 30 stays.
    assert new.speed_mph.tolist() == [25.0, "30 mph", 35.0]
    assert new.speed_source.tolist() == ["speed.parquet", "OpenStreetMap", "speed.parquet"]
    lts = prepare.physical_edges(new).set_index("physical_edge_id").speed_mph
    assert lts.to_dict() == {"main-1": 25.0, "main-2": 30.0, "side": 35.0}


def test_bikeway_classes_map_to_facilities_and_a_filter_drops_rows(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = _write(
        tmp_path, "bikeways.gpkg",
        [{"CLASS": "Class II", "STATUS": "built"}, {"CLASS": "Class IV", "STATUS": "planned"},
         {"CLASS": "Class I", "STATUS": "built"}],
        [LineString([(0, 2), (100, 2)]), LineString([(100, 1), (200, 1)]),
         LineString([(102, 0), (102, -30)])],
    )  # fmt: skip
    layer = _layer(
        "bikeways", path, column="CLASS", where={"STATUS": ["built"]}, name="County bikeways",
        values={"Class I": "path", "Class II": "lane", "Class IV": "track"},
    )  # fmt: skip
    new, _, counts = local_layers.apply(links, nodes, [layer])
    assert new.bike_facility.tolist() == ["lane", "none", "path"]  # the planned track is filtered
    assert counts == {"County bikeways": 2}
    unmapped = _layer("bikeways", path, column="CLASS", values={"Class I": "path"})
    with pytest.raises(config.ConfigError, match="'Class II', which values does not map"):
        local_layers.apply(links, nodes, [unmapped])


def test_a_signal_goes_to_the_nearest_intersection_only(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = _write(
        tmp_path, "poles.parquet",
        [{"TYPE": "Traffic Light"}, {"TYPE": "Traffic Light"}, {"TYPE": "Street Light"}],
        [Point(110, 5), Point(190, 0), Point(100, 1)],
    )  # fmt: skip
    layer = _layer("signals", path, where={"TYPE": "Traffic Light"})
    _, new, counts = local_layers.apply(links, nodes, [layer])
    # B is a three-way junction 11 m away; C (8 m from the second pole) is a dead end.
    assert new.control.tolist() == ["none", "signal", "none", "none"]
    assert new.control_source.tolist()[1] == "poles.parquet" and counts == {"poles.parquet": 1}
    far = _write(tmp_path, "far.parquet", [{}], [Point(100, 40)])
    _, untouched, _ = local_layers.apply(links, nodes, [_layer("signals", far)])
    assert untouched.control.eq("none").all()


def test_an_empty_layer_changes_nothing(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = _write(tmp_path, "none.parquet", [], [])
    new, _, counts = local_layers.apply(links, nodes, [_layer("sidewalks", path)])
    assert new.sidewalk.tolist() == links.sidewalk.tolist() and counts == {"none.parquet": 0}


@pytest.mark.parametrize(
    ("setting", "message"),
    [
        ({"kind": "trees", "file": "x"}, "'trees' is not one of"),
        ({"kind": "speeds", "file": "x"}, "speeds needs the column"),
        ({"kind": "sidewalks", "file": "x", "column": "W"}, "sidewalks takes no column"),
        ({"kind": "bikeways", "file": "x", "column": "C"}, "are needed for bikeways"),
        ({"kind": "signals", "file": "x", "values": {"a": "lane"}}, "apply only to bikeways"),
        ({"kind": "bikeways", "file": "x", "column": "C", "values": {"a": "sharrow"}}, "'sharrow'"),
        ({"kind": "signals", "file": "x", "match_m": 0}, "match_m: expected a positive"),
        ({"kind": "signals"}, "missing setting 'file'"),
        ({"kind": "signals", "file": "x", "colour": "red"}, "unknown setting 'colour'"),
    ],
)
def test_bad_local_layer_settings_are_errors(setting: dict[str, Any], message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        _parse({"local_layers": [setting]})


def test_the_demo_town_takes_no_local_layers() -> None:
    raw = {"local_layers": [{"kind": "signals", "file": "p.gpkg"}]}
    with pytest.raises(config.ConfigError, match="the demo town takes no local_layers"):
        config.parse({**_demo(), **raw})


def _demo() -> dict[str, Any]:
    return {
        "study": "t", "area": {"source": "demo"},
        "destinations": [{"id": "d", "name": "D", "kind": "point"}],
        "modes": ["walk"], "presets": {"walk": ["legacy"]}, "time_caps_min": {"walk": 10},
        "demand": {"weight": "residents"},
    }  # fmt: skip


def test_settings_default_the_match_distance_by_kind(tmp_path: Path) -> None:
    study = _parse({"local_layers": [{"kind": "signals", "file": "p.gpkg"},
                                     {"kind": "sidewalks", "file": "s.gpkg", "match_m": 10}]})  # fmt: skip
    assert [layer.match_m for layer in study.local_layers] == [22.0, 10.0]
    assert study.local_layers[0].file == Path("p.gpkg").resolve()


def test_unreadable_layers_are_config_errors(tmp_path: Path) -> None:
    links, nodes = _streets()
    with pytest.raises(config.ConfigError, match="no such file"):
        local_layers.apply(links, nodes, [_layer("sidewalks", tmp_path / "gone.gpkg")])
    bare = tmp_path / "bare.parquet"
    gpd.GeoDataFrame(geometry=[LineString([(0, 0), (1, 0)])]).to_parquet(bare)
    with pytest.raises(config.ConfigError, match="has no coordinate system"):
        local_layers.apply(links, nodes, [_layer("sidewalks", bare)])
    points = _write(tmp_path, "pts.parquet", [{}], [Point(0, 0)])
    with pytest.raises(config.ConfigError, match="sidewalks in .* must be LineStrings, not Points"):
        local_layers.apply(links, nodes, [_layer("sidewalks", points)])
    with pytest.raises(config.ConfigError, match="has no column 'SPEED'"):
        local_layers.apply(links, nodes, [_layer("speeds", points, column="SPEED")])
    junk = tmp_path / "junk.gpkg"
    junk.write_text("not a geopackage")
    with pytest.raises(config.ConfigError, match="could not read"):
        local_layers.apply(links, nodes, [_layer("sidewalks", junk)])


def test_a_bent_street_matches_a_layer_on_the_same_line(tmp_path: Path) -> None:
    links, nodes = _streets()
    bent = LineString([(0, 0), (100, 0), (100, 150)])  # end-to-end bearing 56 degrees
    links = links.set_geometry([bent, *links.geometry.iloc[1:]], crs=CRS)
    path = _write(tmp_path, "sw.parquet", [{}], [bent])
    new, _, _ = local_layers.apply(links, nodes, [_layer("sidewalks", path)])
    assert new.sidewalk.iloc[0] == "local"


def test_each_part_of_a_multipart_feature_counts(tmp_path: Path) -> None:
    from shapely.geometry import MultiLineString, MultiPoint

    links, nodes = _streets()
    far_and_side = MultiLineString([[(1000, 1000), (1100, 1000)], [(101, 0), (101, -30)]])
    path = _write(tmp_path, "sw.parquet", [{}], [far_and_side])
    new, _, _ = local_layers.apply(links, nodes, [_layer("sidewalks", path)])
    assert new.sidewalk.tolist() == ["none", "unknown", "local"]
    # Two junctions, B and a new one at C; one MultiPoint signal near each.
    extra = gpd.GeoDataFrame({"link_id": ["c-north", "c-south"], "u": ["C", "C"], "v": ["E", "F"]},
                             geometry=[LineString([(200, 0), (200, 50)]),
                                       LineString([(200, 0), (200, -50)])], crs=CRS)  # fmt: skip
    grid = gpd.GeoDataFrame(pd.concat([links, extra], ignore_index=True), crs=CRS)
    poles = _write(tmp_path, "poles.parquet", [{}], [MultiPoint([(100, 1), (200, 2)])])
    _, marked, _ = local_layers.apply(grid, nodes, [_layer("signals", poles)])
    assert marked.control.tolist() == ["none", "signal", "signal", "none"]


def test_streets_with_a_gappy_index_keep_their_own_matches(tmp_path: Path) -> None:
    links, nodes = _streets()
    links.index = [1, 2, 5]
    path = _write(tmp_path, "sw.parquet", [{}], [LineString([(105, -8), (200, -8)])])
    new, _, _ = local_layers.apply(links, nodes, [_layer("sidewalks", path)])
    assert new.sidewalk.tolist() == ["none", "local", "unknown"]


def test_a_blank_nullable_speed_keeps_the_osm_value(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = tmp_path / "speed.parquet"
    gpd.GeoDataFrame(
        {"SPEED": pd.array([25, None], dtype="Int64")},
        geometry=[LineString([(0, 3), (100, 3)]), LineString([(100, 3), (200, 3)])], crs=CRS,
    ).to_parquet(path)  # fmt: skip
    new, _, _ = local_layers.apply(links, nodes, [_layer("speeds", path, column="SPEED")])
    assert new.speed_mph.tolist()[:2] == [25.0, "30 mph"]


def test_any_geometry_column_name_numeric_filters_and_default_distances(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = tmp_path / "sw.parquet"
    frame = gpd.GeoDataFrame(
        {"STATUS": [1.0, None]},
        geometry=[LineString([(5, -8), (100, -8)]), LineString([(105, -8), (200, -8)])], crs=CRS,
    ).rename_geometry("geom")  # fmt: skip
    frame.to_parquet(path)
    new, _, _ = local_layers.apply(links, nodes, [_layer("sidewalks", path, where={"STATUS": 1})])
    assert new.sidewalk.tolist() == ["local", "unknown", "unknown"]
    poles = _write(tmp_path, "poles.parquet", [{}], [Point(110, 5)])
    bare = config.LocalLayer(kind="signals", file=poles)  # no match_m: the kind's 22 m
    _, marked, _ = local_layers.apply(links, nodes, [bare])
    assert marked.control.tolist() == ["none", "signal", "none", "none"]
