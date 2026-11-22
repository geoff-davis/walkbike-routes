"""Hand corrections on a made-up grid."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import LineString, Point

from walkbike_routes import config, overrides
from walkbike_routes.network import prepare

CRS = 32614  # UTM 14N; the grid sits near 40 N, 99 W


def _xy_to_lonlat(x: float, y: float) -> list[float]:
    moved = gpd.GeoSeries([Point(x, y)], crs=CRS).to_crs(4326)
    return [float(moved.x.iloc[0]), float(moved.y.iloc[0])]


def _streets() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    # Way 1 runs A-B-C east; way 2 is a footway B-D south; E is a lone node 60 m east of D.
    x0, y0 = 500_000.0, 4_430_000.0
    at = {k: (x0 + dx, y0 + dy) for k, (dx, dy) in
          {"A": (0, 0), "B": (100, 0), "C": (200, 0), "D": (100, -80), "E": (160, -80)}.items()}  # fmt: skip
    links = gpd.GeoDataFrame(
        {
            "link_id": ["w1-0", "w1-1", "w2-0"],
            "u": ["A", "B", "B"],
            "v": ["B", "C", "D"],
            "osm_way_id": [1, 1, 2],
            "name": ["Main", "Main", None],
            "highway": ["residential", "residential", "footway"],
            "lanes": [None, None, None],
            "speed_mph": [None, None, None],
            "bike_facility": ["none", "none", "none"],
            "sidewalk": ["unknown", "unknown", "unknown"],
            "oneway": ["no", "no", "no"],
            "bike_allowed": [True, True, False],
            "ped_allowed": [True, True, True],
            "car_allowed": [True, True, False],
        },
        geometry=[LineString([at["A"], at["B"]]), LineString([at["B"], at["C"]]),
                  LineString([at["B"], at["D"]])],
        crs=CRS,
    )  # fmt: skip
    nodes = gpd.GeoDataFrame(
        {"node_id": list(at), "control": ["none"] * 5},
        geometry=[Point(p) for p in at.values()],
        crs=CRS,
    )
    return links, nodes


def _write(tmp_path: Path, items: list[dict[str, Any]]) -> Path:
    path = tmp_path / "overrides.yaml"
    path.write_text(yaml.safe_dump(items))
    return path


def _item(action: str, body: Any, **extra: Any) -> dict[str, Any]:
    return {"id": f"fix-{action}", "reason": "made up", "source": "test", action: body, **extra}


def test_a_footway_opened_to_cycling_is_routable_by_bike(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = _write(tmp_path, [_item("change", {"ways": [2], "set": {"bike_allowed": True,
                                                                   "bike_facility": "path"}})])  # fmt: skip
    new, kept = overrides.apply(links, nodes, overrides.load(path))
    assert new.bike_allowed.tolist() == [True, True, True]
    assert new.override_id.isna().tolist() == [True, True, False]
    assert new.override_id.iloc[2] == "fix-change"
    edges = prepare.physical_edges(new).set_index("physical_edge_id")
    assert bool(edges.bike_allowed["w2-0"]) and edges.lts_best["w2-0"] == 1
    assert len(kept) == 4  # E, used by nothing, is dropped
    assert links.bike_allowed.tolist() == [True, True, False]  # the input is left alone


def test_near_narrows_a_way_to_the_pieces_beside_a_point(tmp_path: Path) -> None:
    links, nodes = _streets()
    east = _xy_to_lonlat(500_180, 4_430_005)
    path = _write(tmp_path, [_item("change", {"ways": [1], "near": east,
                                              "set": {"bike_facility": "lane", "speed_mph": 20}})])  # fmt: skip
    new, _ = overrides.apply(links, nodes, overrides.load(path))
    assert new.bike_facility.tolist() == ["none", "lane", "none"]
    assert new.speed_mph.tolist() == [None, 20.0, None]


def test_a_street_can_be_marked_a_bike_boulevard(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = _write(tmp_path, [_item("change", {"ways": [1], "set": {"bike_facility": "boulevard"}})])
    new, _ = overrides.apply(links, nodes, overrides.load(path))
    assert "boulevard" in set(new.bike_facility)


def test_a_traffic_count_can_be_set_and_is_credited(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = _write(tmp_path, [_item("change", {"ways": [1], "set": {"traffic_aadt": 9500}})])
    new, _ = overrides.apply(links, nodes, overrides.load(path))
    counted = new[new.traffic_aadt.notna()]
    assert len(counted) and counted.traffic_aadt.astype(float).eq(9500.0).all()
    assert set(counted.traffic_aadt_source) == {"override fix-change"}
    assert new[new.traffic_aadt.isna()].traffic_aadt_source.isna().all()


def test_a_correction_replaces_a_local_layers_credit(tmp_path: Path) -> None:
    links, nodes = _streets()
    links["bike_facility_source"] = "County bikeways"
    links["speed_source"] = "City centerlines"
    path = _write(tmp_path, [_item("change", {"ways": [1], "set": {"bike_facility": "lane"}})])
    new, _ = overrides.apply(links, nodes, overrides.load(path))
    changed = new.override_id.notna()
    assert set(new[changed].bike_facility_source) == {"override fix-change"}
    assert set(new[~changed].bike_facility_source) == {"County bikeways"}
    assert set(new.speed_source) == {"City centerlines"}  # a value it did not set keeps its credit


def test_links_are_removed_and_added_between_existing_nodes(tmp_path: Path) -> None:
    links, nodes = _streets()
    path = _write(tmp_path, [
        _item("remove", {"links": ["w1-1"]}),
        _item("add", {"line": [_xy_to_lonlat(500_103, 4_429_918), _xy_to_lonlat(500_130, 4_429_900),
                               _xy_to_lonlat(500_158, 4_429_922)],
                      "set": {"highway": "footway", "bike_allowed": False}}),
    ])  # fmt: skip
    new, kept = overrides.apply(links, nodes, overrides.load(path))
    assert new.link_id.tolist() == ["w1-0", "w2-0", "override-fix-add"]
    added = new.iloc[-1]
    assert (added.u, added.v, added.highway, added.bike_allowed) == ("D", "E", "footway", False)
    # The ends snap exactly onto the nodes; the middle point is kept.
    coordinates = list(added.geometry.coords)
    assert coordinates[0] == (500_100.0, 4_429_920.0) and coordinates[-1] == (
        500_160.0,
        4_429_920.0,
    )
    assert len(coordinates) == 3
    assert sorted(kept.node_id) == ["A", "B", "D", "E"]  # C lost its only link
    edges = prepare.physical_edges(new)
    assert "override-fix-add" in set(edges.physical_edge_id)


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (_item("change", {"ways": [9], "set": {"lanes": 2}}), "9 is not in the street network"),
        (_item("remove", {"links": ["w7-0"]}), "w7-0 is not in the street network"),
        (_item("change", {"ways": [1], "near": _xy_to_lonlat(501_000, 4_431_000), "set": {"lanes": 2}}),
         "nothing selected lies within 30 m"),
        (_item("add", {"line": [[0.0, 0.0], [0.001, 0.0]]}), "no street node within 15 m"),
    ],
)  # fmt: skip
def test_stale_overrides_are_errors(tmp_path: Path, item: dict[str, Any], message: str) -> None:
    links, nodes = _streets()
    with pytest.raises(config.ConfigError, match=message):
        overrides.apply(links, nodes, overrides.load(_write(tmp_path, [item])))


def test_both_ends_on_one_node_is_an_error(tmp_path: Path) -> None:
    links, nodes = _streets()
    near_a = [_xy_to_lonlat(500_000, 4_430_001), _xy_to_lonlat(500_002, 4_430_000)]
    with pytest.raises(config.ConfigError, match="same node"):
        overrides.apply(
            links, nodes, overrides.load(_write(tmp_path, [_item("add", {"line": near_a})]))
        )


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ("just text", "expected a list"),
        (["x"], r"overrides\[1\]: expected a mapping"),
        ([{"id": "a", "reason": "r", "source": "s", "colour": 1}], "unknown setting 'colour'"),
        ([{"id": "a", "source": "s", "remove": {"ways": [1]}}], "reason is needed"),
        ([{"id": "a", "reason": "r", "source": "s"}], "exactly one of"),
        ([_item("remove", {"ways": [1]}, add={"line": []})], "exactly one of"),
        ([_item("remove", "w1-0")], "remove expects a mapping"),
        ([_item("remove", {"ways": [1], "set": {"lanes": 1}})], "remove has no setting 'set'"),
        ([_item("remove", {})], "name the ways or links"),
        ([_item("remove", {"ways": ["1"]})], "ways must be a list of OSM way numbers"),
        ([_item("remove", {"links": [1]})], "links must be a list of link ids"),
        ([_item("change", {"ways": [1]})], "change needs set"),
        ([_item("change", {"ways": [1], "set": "lane"})], "set expects a mapping"),
        ([_item("change", {"ways": [1], "set": {"colour": "red"}})], "cannot set 'colour'"),
        ([_item("change", {"ways": [1], "set": {"bike_facility": "sharrow"}})], "'sharrow'"),
        ([_item("change", {"ways": [1], "set": {"bike_allowed": "yes"}})], "true or false"),
        ([_item("change", {"ways": [1], "set": {"lanes": -1}})], "lanes: expected a positive"),
        ([_item("change", {"ways": [1], "set": {"name": ""}})], "name must be text"),
        ([_item("change", {"ways": [1], "near": [200, 0], "set": {"lanes": 1}})], "is not a longitude and latitude"),
        ([_item("change", {"ways": [1], "near_m": 0, "set": {"lanes": 1}})], "near_m: expected"),
        ([_item("add", {"line": [[0, 0]]})], "two or more"),
        ([{**_item("remove", {"ways": [1]}), "id": "a b"}], "only letters, digits"),
        ([_item("remove", {"ways": [1]}), _item("remove", {"ways": [2]})], "used twice"),
    ],
)  # fmt: skip
def test_bad_override_files_are_errors(tmp_path: Path, items: Any, message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        overrides.load(_write(tmp_path, items))


def test_missing_or_broken_files_are_errors(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="no such file"):
        overrides.load(tmp_path / "gone.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- [unclosed")
    with pytest.raises(config.ConfigError, match="not valid YAML"):
        overrides.load(bad)


def test_the_study_config_names_the_file(tmp_path: Path) -> None:
    raw: dict[str, Any] = {
        "study": "t", "area": {"source": "demo"},
        "destinations": [{"id": "d", "name": "D", "kind": "point"}],
        "modes": ["walk"], "presets": {"walk": ["legacy"]}, "time_caps_min": {"walk": 10},
        "demand": {"weight": "residents"}, "overrides": "fixes.yaml",
    }  # fmt: skip
    with pytest.raises(config.ConfigError, match="the demo town takes no overrides"):
        config.parse(raw, base=tmp_path)
    raw["area"] = {"source": "place", "place": "Somewhere, Oregon"}
    raw["destinations"][0]["location"] = [-122.1, 37.4]
    study = config.parse(raw, base=tmp_path)
    assert study.overrides == (tmp_path / "fixes.yaml").resolve()
    assert replace(study, overrides=None).overrides is None


def test_the_example_overrides_file_reads() -> None:
    root = Path(__file__).resolve().parents[1]
    study = config.load(root / "config" / "los_altos_downtown.yaml")
    assert study.overrides is not None
    found = overrides.load(study.overrides)
    assert [o.id for o in found] == [
        "almond_edith_rear_path",
        "main_street_downtown_speed",
        "cuesta_drive_lane_west",
        "san_antonio_edith_slow_signal",
    ]
    assert found[0].values == {"bike_allowed": True}


def test_one_way_values_are_the_ones_routing_reads(tmp_path: Path) -> None:
    from walkbike_routes.attributes import oneway_allows

    links, nodes = _streets()
    path = _write(tmp_path, [_item("change", {"links": ["w1-0"], "set": {"oneway": "-1"}})])
    new, _ = overrides.apply(links, nodes, overrides.load(path))
    value = new.oneway.iloc[0]
    assert not oneway_allows(value, None, forward=True) and oneway_allows(value, None, False)
    with pytest.raises(config.ConfigError, match="'forward' is not one of"):
        overrides.load(_write(tmp_path, [_item("change", {"ways": [1],
                                                          "set": {"oneway": "forward"}})]))  # fmt: skip


def test_ids_accumulate_and_a_change_already_in_osm_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    links, nodes = _streets()
    path = _write(tmp_path, [
        {**_item("change", {"links": ["w1-0"], "set": {"speed_mph": 15}}), "id": "slow"},
        {**_item("change", {"links": ["w1-0"], "set": {"bike_facility": "lane"}}), "id": "lane"},
        {**_item("change", {"ways": [2], "set": {"ped_allowed": True}}), "id": "stale"},
    ])  # fmt: skip
    new, _ = overrides.apply(links, nodes, overrides.load(path))
    assert new.override_id.iloc[0] == "slow,lane" and new.speed_mph.iloc[0] == 15.0
    assert [r.getMessage() for r in caplog.records] == [
        "override 'stale' changes nothing: OSM may be fixed; retire it"
    ]


def test_unquoted_yaml_one_way_values_work(tmp_path: Path) -> None:
    path = tmp_path / "o.yaml"
    path.write_text(
        "- {id: a, reason: r, source: s, change: {ways: [1], set: {oneway: yes}}}\n"
        "- {id: b, reason: r, source: s, change: {ways: [1], set: {oneway: -1}}}\n"
        "- {id: c, reason: r, source: s, change: {ways: [1], set: {oneway_bicycle: no}}}\n"
    )
    found = overrides.load(path)
    assert [o.values for o in found] == [
        {"oneway": "yes"}, {"oneway": "-1"}, {"oneway_bicycle": "no"},
    ]  # fmt: skip


def test_a_link_beside_an_existing_one_is_split_so_both_stay_routable(tmp_path: Path) -> None:
    links, nodes = _streets()
    # A second way from A to B, bowed 20 m north of the street.
    line = [_xy_to_lonlat(500_000, 4_430_000), _xy_to_lonlat(500_050, 4_430_020),
            _xy_to_lonlat(500_100, 4_430_000)]  # fmt: skip
    path = _write(tmp_path, [_item("add", {"line": line})])
    new, kept = overrides.apply(links, nodes, overrides.load(path))
    added = new[new.override_id.eq("fix-add")]
    assert added.link_id.tolist() == ["override-fix-add#0", "override-fix-add#1"]
    assert list(zip(added.u, added.v, strict=True)) == [
        ("A", "override-fix-add#middle"), ("override-fix-add#middle", "B"),
    ]  # fmt: skip
    middle = kept.set_index("node_id").geometry["override-fix-add#middle"]
    assert middle.distance(Point(500_050, 4_430_020)) < 1.0
    assert added.length.sum() == pytest.approx(2 * (50**2 + 20**2) ** 0.5, abs=0.01)


def test_a_signal_can_ban_right_turns_on_red(tmp_path: Path) -> None:
    links, nodes = _streets()
    nodes.loc[nodes.node_id == "B", "control"] = "signal"
    near = _xy_to_lonlat(500_105.0, 4_430_000.0)
    body = {"near": near, "set": {"right_on_red": False, "extra_wait_s": 50}}
    _, fixed = overrides.apply(
        links, nodes, overrides.load(_write(tmp_path, [_item("signal", body)]))
    )
    at = fixed.set_index("node_id")
    assert at.loc["B", "right_on_red"] == "no" and at.loc["B", "extra_wait_s"] == 50
    assert at.loc["A", "right_on_red"] == "" and at.extra_wait_s.isna()["A"]
    kept = prepare.nodes(fixed).set_index("node_id")
    assert kept.loc["B", "right_on_red"] == "no" and kept.loc["B", "extra_wait_s"] == 50


@pytest.mark.parametrize(
    ("body", "message"),
    [({"set": {"right_on_red": False}}, "needs near"),
     ({"near": [0, 0], "set": {"right_on_red": "no"}}, "true or false"),
     ({"near": [0, 0], "set": {"extra_wait_s": -5}}, "positive"),
     ({"near": [0, 0], "set": {}}, "needs set"),
     ({"near": [0, 0], "set": {"right_on_red": False, "speed_mph": 25}}, "cannot set"),
     ({"near": [0, 0], "ways": [1], "set": {"right_on_red": False}}, "no setting")],
)  # fmt: skip
def test_signal_overrides_are_checked(tmp_path: Path, body: dict[str, Any], message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        overrides.load(_write(tmp_path, [_item("signal", body)]))


def test_a_signal_override_must_find_a_signal(tmp_path: Path) -> None:
    links, nodes = _streets()  # no signals at all
    body = {"near": _xy_to_lonlat(500_100.0, 4_430_000.0), "set": {"right_on_red": False}}
    with pytest.raises(config.ConfigError, match="no signal lies within"):
        overrides.apply(links, nodes, overrides.load(_write(tmp_path, [_item("signal", body)])))


def test_a_signal_override_replaces_whole_number_waits(tmp_path: Path) -> None:
    links, nodes = _streets()
    nodes.loc[nodes.node_id == "B", "control"] = "signal"
    nodes["extra_wait_s"] = [50] * len(nodes)  # whole numbers: an int column
    body = {"near": _xy_to_lonlat(500_100.0, 4_430_000.0),
            "set": {"extra_wait_s": 12.5}}  # fmt: skip
    _, fixed = overrides.apply(
        links, nodes, overrides.load(_write(tmp_path, [_item("signal", body)]))
    )
    at = fixed.set_index("node_id")
    assert at.loc["B", "extra_wait_s"] == 12.5 and at.loc["B", "right_on_red"] == ""
