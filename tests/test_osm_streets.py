"""OpenStreetMap ways into the street table, and a real-area build end to end."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyarrow.fs as pafs
import pytest
from shapely.geometry import box

from tests import fake_services as fake
from walkbike_routes import __main__ as cli
from walkbike_routes import config, layers
from walkbike_routes.download import census, overture
from walkbike_routes.download.http import DownloadError
from walkbike_routes.network import osm_streets as o
from walkbike_routes.network import prepare


@pytest.mark.parametrize(
    ("value", "mph"),
    [("25 mph", 25.0), ("40", 24.9), ("50;30", 31.1), ("10 knots", 11.5), ("none", None),
     ("US:urban", None), (None, None)],
)  # fmt: skip
def test_speed_limits_are_read_in_their_units(value: str | None, mph: float | None) -> None:
    assert o.speed_mph(value) == mph


@pytest.mark.parametrize(
    ("tags", "facility"),
    [({"highway": "cycleway"}, "path"),
     ({"highway": "footway", "bicycle": "designated"}, "path"),
     ({"highway": "footway"}, "none"),
     ({"highway": "residential", "cycleway:right": "lane"}, "lane"),
     ({"highway": "residential", "cycleway": "lane", "cycleway:left": "track"}, "track"),
     ({"highway": "residential", "cycleway:both": "shared_lane"}, "none")],
)  # fmt: skip
def test_bike_facilities_prefer_the_most_separated(tags: dict[str, str], facility: str) -> None:
    assert o.bike_facility(tags) == facility


@pytest.mark.parametrize(
    ("tags", "sidewalk"),
    [({"highway": "footway"}, "path"),
     ({"highway": "residential", "sidewalk": "yes"}, "both"),
     ({"highway": "residential", "sidewalk": "no"}, "none"),
     ({"highway": "residential", "sidewalk": "left"}, "left"),
     ({"highway": "residential", "sidewalk:both": "separate"}, "separate"),
     ({"highway": "residential", "sidewalk:left": "yes", "sidewalk:right": "yes"}, "both"),
     ({"highway": "residential", "sidewalk:right": "yes"}, "right"),
     ({"highway": "residential", "sidewalk:both": "no"}, "none"),
     ({"highway": "residential", "sidewalk:left": "no"}, "unknown"),
     ({"highway": "residential"}, "unknown")],
)  # fmt: skip
def test_sidewalk_tags_in_all_their_forms(tags: dict[str, str], sidewalk: str) -> None:
    assert o.sidewalk(tags) == sidewalk


@pytest.mark.parametrize(
    ("tags", "walk", "bike", "car"),
    [({"highway": "residential"}, True, True, True),
     ({"highway": "motorway"}, False, False, True),
     ({"highway": "footway"}, True, False, False),
     ({"highway": "footway", "bicycle": "yes"}, True, True, False),
     ({"highway": "service", "access": "private"}, False, False, False),
     ({"highway": "service", "access": "private", "foot": "yes"}, True, False, False),
     ({"highway": "primary", "bicycle": "use_sidepath"}, True, False, True),
     ({"highway": "residential", "motor_vehicle": "no"}, True, True, False),
     ({"highway": "service", "access": "no", "motorcar": "yes"}, False, False, True),
     ({"highway": "track", "motor_vehicle": "yes"}, True, True, True),
     ({"highway": "service", "motor_vehicle": "no", "motorcar": "yes"}, True, True, True),
     ({"highway": "residential", "vehicle": "no"}, True, False, False),
     ({"highway": "residential", "vehicle": "no", "bicycle": "yes"}, True, True, False),
     ({"highway": "footway", "vehicle": "yes"}, True, True, True)],
)  # fmt: skip
def test_access_tags_decide_who_may_use_a_way(
    tags: dict[str, str], walk: bool, bike: bool, car: bool
) -> None:
    assert (o.allowed(tags, "foot"), o.allowed(tags, "bicycle"), o.car_allowed(tags)) == (
        walk,
        bike,
        car,
    )


@pytest.mark.parametrize(
    ("tags", "oneway"),
    [({"highway": "residential", "oneway": "yes"}, "yes"),
     ({"highway": "residential", "oneway": "-1"}, "-1"),
     ({"highway": "residential", "junction": "roundabout"}, "yes"),
     ({"highway": "residential", "junction": "roundabout", "oneway": "no"}, "no"),
     ({"highway": "motorway"}, "yes"),
     ({"highway": "residential"}, "no")],
)  # fmt: skip
def test_one_way_rules(tags: dict[str, str], oneway: str) -> None:
    assert o.oneway(tags) == oneway


@pytest.mark.parametrize(
    ("tags", "allowed"),
    [({"red_turn:right": "no"}, "no"), ({"red_turn:right": "yes"}, "yes"),
     ({"red_turn:right": "no", "red_turn:right:bicycle": "yes"}, "yes"),
     ({"red_turn:right": "maybe"}, ""), ({}, "")],
)  # fmt: skip
def test_right_on_red_is_read_from_the_signal(tags: dict[str, str], allowed: str) -> None:
    assert o.right_on_red(tags) == allowed


def test_ways_split_at_junctions_and_controlled_nodes() -> None:
    answer: dict[str, Any] = {
        "elements": [
            *(
                {"type": "node", "id": i, "lat": 40.0, "lon": -100 + i / 1000}
                for i in range(1, 6)
            ),
            {"type": "node", "id": 9, "lat": 40.001, "lon": -99.997},
            {"type": "node", "id": 4, "lat": 40.0, "lon": -99.996,
             "tags": {"crossing": "traffic_signals"}},
            {"type": "way", "id": 1, "nodes": [1, 2, 3, 4, 5], "tags": {"highway": "residential"}},
            {"type": "way", "id": 2, "nodes": [3, 9], "tags": {"highway": "service"}},
            {"type": "way", "id": 3, "nodes": [9, 9], "tags": {"highway": "service"}},
            {"type": "way", "id": 4, "nodes": [1, 77], "tags": {"highway": "path"}},
            {"type": "way", "id": 5, "nodes": [1, 2], "tags": {"building": "yes"}},
        ]
    }  # fmt: skip
    links, nodes = o.streets(answer)
    # Way 1 splits at node 3 (meets way 2) and node 4 (a signal); node 2 stays inside.
    assert list(zip(links.link_id, links.u, links.v, strict=True)) == [
        ("w1-0", "n1", "n3"),
        ("w1-1", "n3", "n4"),
        ("w1-2", "n4", "n5"),
        ("w2-0", "n3", "n9"),
    ]
    assert len(links.geometry[0].coords) == 3
    assert dict(zip(nodes.node_id, nodes.control, strict=True))["n4"] == "signal"
    assert set(nodes.node_id) == {"n1", "n3", "n4", "n5", "n9"}


def test_closed_loops_are_cut_into_distinct_pieces() -> None:
    # A cul-de-sac circle 1-2-3-4-1 meeting the street 0-1 only at node 1, and an
    # out-and-back spur 3-5-3 that encloses nothing.
    spots = {
        0: (-100.002, 40.0),
        1: (-100.001, 40.0),
        2: (-100.0, 40.001),
        3: (-99.999, 40.0),
        4: (-100.0, 39.999),
    }
    answer: dict[str, Any] = {"elements": [
        *({"type": "node", "id": i, "lon": x, "lat": y} for i, (x, y) in spots.items()),
        {"type": "way", "id": 1, "nodes": [0, 1], "tags": {"highway": "residential"}},
        {"type": "way", "id": 2, "nodes": [1, 2, 3, 4, 1], "tags": {"highway": "residential"}},
        {"type": "node", "id": 5, "lon": -99.998, "lat": 40.0},
        {"type": "way", "id": 3, "nodes": [3, 5, 3], "tags": {"highway": "service"}},
    ]}  # fmt: skip
    links, _ = o.streets(answer)
    loop = links[links.osm_way_id.eq(2)]
    # Node 3 is a junction, so the loop's halves 1-2-3 and 3-4-1 would both join
    # nodes 1 and 3; the second is cut at node 4.
    assert list(zip(loop.u, loop.v, strict=True)) == [("n1", "n3"), ("n3", "n4"), ("n4", "n1")]
    assert sum(len(g.coords) - 1 for g in loop.geometry) == 4  # every segment kept
    assert not links.osm_way_id.eq(3).any()


def test_a_figure_eight_way_splits_where_it_crosses_itself() -> None:
    spots = {
        1: (-100.0, 40.0),
        2: (-100.001, 40.001),
        3: (-100.001, 39.999),
        4: (-99.999, 40.001),
        5: (-99.999, 39.999),
    }
    answer: dict[str, Any] = {"elements": [
        *({"type": "node", "id": i, "lon": x, "lat": y} for i, (x, y) in spots.items()),
        {"type": "way", "id": 1, "nodes": [1, 2, 3, 1, 4, 5, 1], "tags": {"highway": "path"}},
    ]}  # fmt: skip
    links, _ = o.streets(answer)
    pairs = [frozenset((u, v)) for u, v in zip(links.u, links.v, strict=True)]
    assert len(pairs) == len(set(pairs)) == 6 and all(len(p) == 2 for p in pairs)


def test_parallel_streets_between_two_junctions_both_survive() -> None:
    spots = {1: (-100.0, 40.0), 2: (-99.999, 40.001), 3: (-99.998, 40.0), 4: (-99.999, 39.999)}
    answer: dict[str, Any] = {"elements": [
        *({"type": "node", "id": i, "lon": x, "lat": y} for i, (x, y) in spots.items()),
        {"type": "way", "id": 1, "nodes": [1, 2, 3], "tags": {"highway": "residential"}},
        {"type": "way", "id": 2, "nodes": [1, 4, 3], "tags": {"highway": "residential"}},
        {"type": "way", "id": 3, "nodes": [1, 3], "tags": {"highway": "service"}},
    ]}  # fmt: skip
    links, _ = o.streets(answer)
    pairs = [frozenset((u, v)) for u, v in zip(links.u, links.v, strict=True)]
    assert len(links) == 4 and len(set(pairs)) == 3  # the straight service link has no inner node
    assert list(links.link_id) == ["w1-0", "w2-0", "w2-1", "w3-0"]


def test_the_street_table_feeds_the_core() -> None:
    links, nodes = o.streets(fake.overpass_answer())
    edges = prepare.physical_edges(links.to_crs(26914))
    main = edges[edges.name.eq("Main")]
    assert set(main.lts_best) == {4} and len(main) == 4
    assert set(edges[edges.name.eq("Edge Path")].lts_best) == {1}
    assert set(prepare.nodes(nodes.to_crs(26914)).highway) == {"traffic_signals", "stop", ""}
    status = dict(zip(edges.name, edges.pedestrian_status, strict=False))
    assert status["Row 0"] == "supported" and status["Column 1"] == "unknown"


def _study(tmp_path: Path, **demand: Any) -> config.Study:
    raw = {
        "study": "Gridville test",
        "area": {"source": "place", "place": "Gridville, Nebraska", "buffer_m": 100},
        "destinations": [
            {"id": "school", "name": "School", "kind": "point", "location": [-99.996, 40.004],
             "access_points": [[-99.996, 40.0035]]},
            {"id": "centre", "name": "Centre", "kind": "area", "boundary_file": "centre.geojson"},
        ],
        "modes": ["walk", "bike"],
        "presets": {"walk": ["legacy"], "bike": ["legacy"]},
        "time_caps_min": {"walk": 20, "bike": 15},
        "demand": demand or {"weight": "residents", "min_age": 5, "max_age": 11},
    }  # fmt: skip
    centre = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": fake.square(-99.997, 40.003, 0.002)}]}  # fmt: skip
    (tmp_path / "centre.geojson").write_text(json.dumps(centre))
    return config.parse(raw, base=tmp_path)


@pytest.fixture(autouse=True)
def offline_overture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "overture"
    fake.overture_buildings(root)
    monkeypatch.setattr(overture, "BUCKET", str(root))
    monkeypatch.setattr(overture, "_filesystem", pafs.LocalFileSystem)
    return root


def test_a_real_area_builds_from_the_public_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    calls: list[str] = []
    town = layers.build(_study(tmp_path), tmp_path / "data", fake.fetcher(tmp_path / "data", calls))
    assert len(town.links) == 40 and town.links.crs == "EPSG:26914"
    assert town.nodes.elevation_m.notna().sum() == len(town.nodes) - 1  # one NoData sample
    # 15 populated blocks: two spread over three buildings, thirteen kept whole.
    homes = town.homes
    assert len(homes) == 16 and homes.residents_5_11.gt(0).all()
    assert homes.placed_by.value_counts().to_dict() == {"block": 13, "building": 3}
    assert homes.groupby("block_id").residents.sum().to_numpy() == pytest.approx(40.0)
    built = homes[homes.placed_by.eq("building")]
    assert set(built.building_source) == {f"Overture Maps {fake.OVERTURE_RELEASE}"}
    assert homes.home_id.is_unique and homes.home_id.str.startswith("b").all()
    assert list(town.destinations.kind) == ["point", "area"]
    assert town.destinations.geometry.iloc[1].geom_type == "Polygon"
    assert town.access_points.access_point_id.tolist() == ["school_1"]
    # HPMS counts: Main and column 3 are counted; the rest, and the path, are not.
    aadt = town.links.set_index("name").traffic_aadt
    assert aadt.loc[["Main"]].tolist() == [13000.0] * 4
    assert aadt.loc[["Column 3"]].tolist() == [3000.0] * 4
    assert aadt.drop(["Main", "Column 3"]).isna().all()
    assert set(town.links.traffic_aadt_source.dropna()) == {"FHWA HPMS NE 2025"}
    # A second build reads only from the cache.
    again: list[str] = []
    layers.build(_study(tmp_path), tmp_path / "data", fake.fetcher(tmp_path / "data", again))
    assert again == []


def test_local_layers_are_laid_over_the_streets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    plain = layers.build(_study(tmp_path), tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    # A made-up city sidewalk layer: a line along every street, in lon/lat.
    plain.links[["geometry"]].to_crs(4326).to_file(tmp_path / "sidewalks.gpkg")
    study = replace(
        _study(tmp_path),
        local_layers=(config.LocalLayer(kind="sidewalks", file=tmp_path / "sidewalks.gpkg",
                                        match_m=5.0),),
    )  # fmt: skip
    town = layers.build(study, tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    assert town.links.sidewalk.eq("local").all()
    assert town.links.sidewalk_source.eq("sidewalks.gpkg").all()


def test_overrides_are_applied_to_the_streets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    plain = layers.build(_study(tmp_path), tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    way = int(plain.links.osm_way_id.iloc[0])
    fixes = tmp_path / "fixes.yaml"
    fixes.write_text(
        f"- {{id: slow, reason: made up, source: test, change: {{ways: [{way}], "
        "set: {speed_mph: 15}}}\n"
    )
    study = replace(_study(tmp_path), overrides=fixes)
    town = layers.build(study, tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    changed = town.links[town.links.osm_way_id.eq(way)]
    assert len(changed) and changed.speed_mph.eq(15.0).all()
    assert changed.override_id.eq("slow").all()


def test_traffic_counts_can_be_turned_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    base = _study(tmp_path)
    study = replace(base, area=replace(base.area, traffic_counts="none"))
    calls: list[str] = []
    town = layers.build(study, tmp_path / "data", fake.fetcher(tmp_path / "data", calls))
    assert "traffic_aadt" not in town.links
    assert not any("HPMS" in c or "State_County" in c for c in calls)


def test_households_need_no_ages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    study = _study(tmp_path, weight="households")
    town = layers.build(study, tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    by_block = town.homes.groupby("block_id").households
    assert by_block.sum().to_numpy() == pytest.approx(14.0) and "residents_5_11" not in town.homes
    split = town.homes[town.homes.block_id.eq("310010001001101")].households
    assert sorted(split) == pytest.approx(
        [14 / 5, 14 * 4 / 5], rel=1e-4
    )  # 1 : 4, give or take projection


def test_the_run_command_writes_a_real_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    real_build = layers.build

    def offline(study: config.Study, data: Path, fetcher: Any = None) -> Any:
        return real_build(study, data, fake.fetcher(data, []))

    monkeypatch.setattr(layers, "build", offline)
    study = _study(tmp_path)
    monkeypatch.chdir(tmp_path)  # the default cache folder is relative
    manifest = json.loads(cli.build(study, tmp_path / "out").read_text())
    assert manifest["crs"] == "EPSG:26914" and not manifest["synthetic"]
    assert gpd.read_parquet(tmp_path / "out" / "homes.parquet").crs == "EPSG:26914"
    assert (tmp_path / "data" / "raw" / "gridville_test").is_dir()


def test_homes_can_stay_one_point_per_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    study = _study(tmp_path)
    study = replace(study, area=replace(study.area, buildings="none"))
    town = layers.build(study, tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    assert len(town.homes) == 15 and town.homes.placed_by.eq("block").all()
    assert town.homes.building_source.eq("").all()


def test_a_pinned_release_is_read_and_cached(tmp_path: Path, offline_overture: Path) -> None:
    bbox = (fake.WEST, fake.SOUTH, fake.WEST + 4 * fake.STEP, fake.SOUTH + 4 * fake.STEP)
    assert overture.latest_release() == fake.OVERTURE_RELEASE
    found, release = overture.buildings(bbox, tmp_path / "cache", release=fake.OVERTURE_RELEASE)
    assert release == fake.OVERTURE_RELEASE and len(found) == 5 and found.crs == 4326
    shutil.rmtree(offline_overture)  # a second read comes from the cache
    again, _ = overture.buildings(bbox, tmp_path / "cache", release=fake.OVERTURE_RELEASE)
    assert again.building_id.tolist() == found.building_id.tolist()
    with pytest.raises(DownloadError, match="could not list Overture releases"):
        overture.latest_release()
    with pytest.raises(DownloadError, match="could not read Overture buildings"):
        overture.buildings(bbox, tmp_path / "other", release="2020-01-01.0")


def test_an_empty_bucket_has_no_release(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(DownloadError, match="no Overture release found"):
        overture.latest_release(pafs.LocalFileSystem(), str(tmp_path / "empty"))


def test_buildings_are_read_for_the_whole_of_every_block_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    asked: list[tuple[float, float, float, float]] = []
    real = overture.buildings

    def spy(bbox: tuple[float, float, float, float], *args: Any, **kwargs: Any) -> Any:
        asked.append(bbox)
        return real(bbox, *args, **kwargs)

    monkeypatch.setattr(overture, "buildings", spy)
    real_blocks = census.blocks

    def wider(*args: Any, **kwargs: Any) -> gpd.GeoDataFrame:
        found = real_blocks(*args, **kwargs).copy()
        last = found.index[-1]  # the north-east block reaches a block's width past the box
        found.loc[[last], "geometry"] = found.loc[[last]].geometry.scale(
            2.0, 1.0, origin=(fake.WEST + 3 * fake.STEP, 0)
        )
        return found

    monkeypatch.setattr(census, "blocks", wider)
    town = layers.build(_study(tmp_path), tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    west, south, east, north = asked[0]
    k_east = fake.WEST + 5 * fake.STEP
    k_west, k_south, k_north = fake.WEST, fake.SOUTH, fake.SOUTH + 4 * fake.STEP
    assert len(town.homes)
    tiny = 1e-9
    assert west <= k_west + tiny and south <= k_south + tiny
    assert east >= k_east - tiny and north >= k_north - tiny


def test_the_cache_tells_nearby_boxes_and_other_sources_apart(
    tmp_path: Path, offline_overture: Path
) -> None:
    bbox = (fake.WEST, fake.SOUTH, fake.WEST + 4 * fake.STEP, fake.SOUTH + 4 * fake.STEP)
    nudged = (*bbox[:3], bbox[3] + 1e-7)
    overture.buildings(bbox, tmp_path / "cache", release=fake.OVERTURE_RELEASE)
    overture.buildings(nudged, tmp_path / "cache", release=fake.OVERTURE_RELEASE)
    other = tmp_path / "copy"
    shutil.copytree(offline_overture, other)
    overture.buildings(bbox, tmp_path / "cache", release=fake.OVERTURE_RELEASE, root=str(other))
    assert len(list((tmp_path / "cache").glob("overture-buildings-*.parquet"))) == 3


def test_hidden_folders_are_skipped_and_files_may_differ_in_integer_width(
    tmp_path: Path, offline_overture: Path
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    folder = offline_overture / fake.OVERTURE_RELEASE / "theme=buildings" / "type=building"
    table = pq.read_table(folder / "part-0.parquet")
    for hidden in ("_temporary", ".backup"):
        (folder / hidden).mkdir()
        pq.write_table(table, folder / hidden / "part-0.parquet")
    (folder / "_temporary" / "status.json").write_text("{}")
    narrow = table.slice(0, 1).set_column(
        table.schema.get_field_index("num_floors"),
        "num_floors",
        table["num_floors"].slice(0, 1).cast(pa.int16()),
    )
    pq.write_table(narrow.set_column(0, "id", pa.array(["extra"])), folder / "part-1.parquet")
    bbox = (fake.WEST, fake.SOUTH, fake.WEST + 4 * fake.STEP, fake.SOUTH + 4 * fake.STEP)
    found, _ = overture.buildings(bbox, tmp_path / "cache", release=fake.OVERTURE_RELEASE)
    assert len(found) == 6 and found.building_id.is_unique


def test_each_destination_has_its_own_ages_and_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from walkbike_routes import study as runner

    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    base = _study(tmp_path)
    school, centre = base.destinations
    teen_school = replace(school, min_age=14, max_age=17, serves=tmp_path / "west.geojson")
    study = replace(base, destinations=(teen_school, centre))
    town = layers.build(study, tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    # The serves area: the west half of the homes' extent.
    west, south, east, north = town.homes.to_crs(4326).total_bounds
    half = gpd.GeoDataFrame(
        geometry=[box(west - 0.001, south - 0.001, (west + east) / 2, north + 0.001)], crs=4326
    )
    half.to_file(tmp_path / "west.geojson")
    assert {"residents_5_11", "residents_14_17"} <= set(town.homes.columns)
    homes = town.homes.rename(columns={"home_id": "origin_id"})
    teens = runner.origins_for(study, teen_school, homes)
    everyone = runner.origins_for(study, centre, homes)
    assert set(teens.weight_unit) == {"residents aged 14-17"}
    assert set(everyone.weight_unit) == {"residents aged 5-11"}
    inside = homes.geometry.covered_by(layers.outline(tmp_path / "west.geojson", str(homes.crs)))
    assert 0 < len(teens) == int(inside.sum()) < len(everyone)
    gpd.GeoDataFrame(geometry=[box(0, 0, 0.001, 0.001)], crs=4326).to_file(tmp_path / "far.geojson")
    nowhere = replace(teen_school, serves=tmp_path / "far.geojson")
    with pytest.raises(config.ConfigError, match="no homes lie inside its serves area"):
        runner.origins_for(study, nowhere, homes)
    # An attendance area given as an outline limits the homes too, unless told not to.
    half.assign(SCHOOL=["Teen"]).to_file(tmp_path / "areas.geojson")
    drawn = config.Outline(file=tmp_path / "areas.geojson", where={"SCHOOL": ("Teen",)})
    limited = replace(teen_school, serves=None, outline=drawn)
    assert len(runner.origins_for(study, limited, homes)) == len(teens)
    shown = replace(limited, outline=replace(drawn, limits=False))
    assert len(runner.origins_for(study, shown, homes)) > len(teens)


def test_an_outline_keeps_the_shapes_its_where_names(tmp_path: Path) -> None:
    study = _study(tmp_path)
    school, centre = study.destinations
    areas = gpd.GeoDataFrame(
        {"SCHOOL": ["Almond", "Santa Rita ", "Almond"]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(5, 5, 6, 6)],
        crs=4326,
    )
    areas.to_file(tmp_path / "areas.geojson")
    drawn = config.Outline(file=tmp_path / "areas.geojson", where={"SCHOOL": ("Almond",)})
    found = layers.outlines(
        replace(study, destinations=(replace(school, outline=drawn), centre)), "EPSG:4326"
    )
    assert list(found.destination_id) == [school.id]
    assert found.geometry.iloc[0].area == pytest.approx(2.0)  # both Almond pieces, not Santa Rita
    rita = replace(drawn, where={"SCHOOL": ("Santa Rita",)})  # stray spaces are ignored
    assert layers.outlines(
        replace(study, destinations=(replace(school, outline=rita),)), "EPSG:4326"
    ).geometry.iloc[0].area == pytest.approx(1.0)
    served = replace(school, serves=tmp_path / "areas.geojson")
    both = layers.outlines(replace(study, destinations=(served,)), "EPSG:4326")
    assert list(both.served) == [True] and list(both.name) == ["Homes counted"]
    cases: list[tuple[dict[str, tuple[str, ...]], str]] = [({"SCHOOL": ("Loyola",)}, "no shape in areas.geojson"),
                           ({"NAME": ("Almond",)}, "no column 'NAME'")]  # fmt: skip
    for where, message in cases:
        wrong = replace(school, outline=replace(drawn, where=where))
        with pytest.raises(config.ConfigError, match=message):
            layers.outlines(replace(study, destinations=(wrong,)), "EPSG:4326")


def _junction_answer() -> dict[str, Any]:
    """A main road (north-south) meets a collector at a signal, with two crosswalks.

    Positions are in metres from the junction J; one crosswalk crosses the
    main road north of J, the other the collector east of J.
    """
    at = {
        1: (0, 100), 2: (0, 12), 3: (0, 0), 4: (0, -100),  # main road: PN, C1, J, PS
        5: (-100, 0), 6: (12, 0), 7: (100, 0),  # collector: TW, (J), C2, TE
        8: (-10, 12), 9: (10, 12),  # crosswalk over the main road, through C1
        10: (12, 10), 11: (12, -10),  # crosswalk over the collector, through C2
    }  # fmt: skip
    tags = {2: {"crossing": "traffic_signals"}, 3: {"highway": "traffic_signals"},
            6: {"crossing": "traffic_signals"}}  # fmt: skip
    nodes = [
        {"type": "node", "id": i, "lat": 40 + y / 111_000, "lon": -100 + x / 85_280,
         **({"tags": tags[i]} if i in tags else {})}
        for i, (x, y) in at.items()
    ]  # fmt: skip
    ways = [
        (1, [1, 2, 3, 4], {"highway": "primary", "sidewalk": "separate"}),
        (2, [5, 3, 6, 7], {"highway": "tertiary", "sidewalk": "both"}),
        (3, [8, 2, 9], {"highway": "footway", "footway": "crossing"}),
        (4, [10, 6, 11], {"highway": "footway", "footway": "crossing",
                          "crossing": "traffic_signals"}),
        (5, [9, 10], {"highway": "footway", "footway": "sidewalk"}),  # north-east corner
    ]  # fmt: skip
    return {"elements": [*nodes, *({"type": "way", "id": i, "nodes": n, "tags": t}
                                   for i, n, t in ways)]}  # fmt: skip


def test_signalised_crosswalks_become_crossing_links() -> None:
    links, _ = o.streets(_junction_answer())
    kinds = links.groupby("osm_way_id")[["movement_type", "crossing", "crossing_road"]].first()
    assert list(kinds.loc[3]) == ["across", "traffic_signals", "primary"]
    assert list(kinds.loc[4]) == ["across", "traffic_signals", "tertiary"]
    assert list(kinds.loc[5]) == ["along", "", ""]  # the sidewalk
    assert list(kinds.loc[1]) == ["along", "", ""]
    unmarked = o.crossing_attributes(
        {"highway": "footway", "footway": "crossing", "crossing": "unmarked"},
        [2],
        {2: "signal"},
        {},
    )
    assert unmarked["movement_type"] == "across"  # paint says nothing about signals
    for tags in ({"crossing": "uncontrolled"}, {"crossing": "no"}, {"crossing:signals": "no"}):
        ruled_out = o.crossing_attributes(
            {"highway": "footway", "footway": "crossing", **tags}, [2], {2: "signal"}, {}
        )
        assert ruled_out["movement_type"] == "along"
    signals_tag = o.crossing_attributes(
        {"highway": "footway", "footway": "crossing", "crossing": "unmarked",
         "crossing:signals": "yes"}, [2], {}, {}
    )  # fmt: skip
    assert signals_tag["movement_type"] == "across"
    newer_tags = o.crossing_attributes(
        {"highway": "footway", "crossing:signals": "yes", "crossing:markings": "zebra"}, [2], {}, {}
    )
    assert newer_tags["movement_type"] == "across"
    assert o.control({"highway": "crossing", "crossing:signals": "yes"}) == "signal"
    link_road = o.crossing_attributes(
        {"highway": "footway", "footway": "crossing", "crossing": "traffic_signals"},
        [1, 2], {}, {1: {"tertiary"}, 2: {"primary_link"}},
    )  # fmt: skip
    assert link_road["crossing_road"] == "primary_link"


def test_crossing_a_main_road_waits_longer_than_going_along_it() -> None:
    from walkbike_routes.network import (
        build_bicycle_links,
        build_pedestrian_links,
        build_signal_wait_inventory,
        routable_links_to_graph,
    )
    from walkbike_routes.network.bicycle import BicycleProfile
    from walkbike_routes.network.pedestrian import PedestrianProfile
    from walkbike_routes.routing import measure_path

    links, nodes = o.streets(_junction_answer())
    edges = prepare.physical_edges(links.to_crs(26914))
    inventory = build_signal_wait_inventory(prepare.nodes(nodes.to_crs(26914)), edges)
    assert len(set(inventory.cluster_by_node.values())) == 1
    walk = routable_links_to_graph(
        build_pedestrian_links(
            edges,
            PedestrianProfile("t", signal_wait_s=30, signal_wait_cross_arterial_s=50,
                              signal_wait_along_arterial_s=15),
            signal_wait_inventory=inventory,
        ),
        directed=True,
    )  # fmt: skip

    def waits(graph: Any, path: list[int]) -> float:
        return measure_path(graph, [f"n{n}" for n in path]).crossing_wait_time_s

    assert waits(walk, [8, 2, 9]) == pytest.approx(50)  # across the main road
    assert waits(walk, [9, 2, 8]) == pytest.approx(50)
    assert waits(walk, [10, 6, 11]) == pytest.approx(15)  # across the collector
    # The collector's sidewalks are on the road link, so walking it pays at
    # the crosswalks, not on arrival.
    assert waits(walk, [7, 6]) == pytest.approx(0)
    # The main road's sidewalks are separate ways: walking its carriageway
    # inside the intersection pays as crossing it.
    assert waits(walk, [2, 3]) == pytest.approx(50)
    # So the cheapest way from the crosswalk's west end to the collector's
    # south side cannot dodge the main-road wait.
    import networkx as nx

    best = nx.shortest_path(walk, "n8", "n11", weight="generalized_cost_s")
    assert waits(walk, [int(n[1:]) for n in best]) >= 50
    bike = routable_links_to_graph(
        build_bicycle_links(
            edges,
            BicycleProfile("t", signal_wait_s=20, signal_wait_cross_arterial_s=40,
                           signal_wait_along_arterial_s=15),
            signal_wait_inventory=inventory,
        ),
        directed=True,
    )  # fmt: skip
    assert waits(bike, [1, 2, 3, 4]) == pytest.approx(15)  # along the main road
    assert waits(bike, [7, 6, 3, 5]) == pytest.approx(40)  # across it on the collector


def test_a_missing_census_key_stops_the_run_before_the_streets_are_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    calls: list[str] = []
    with pytest.raises(census.CensusKeyMissing):
        layers.build(_study(tmp_path), tmp_path / "data", fake.fetcher(tmp_path / "data", calls))
    assert not any("geofabrik" in call for call in calls)
    assert not (tmp_path / "data" / "gridland-latest.osm.pbf").exists()


def test_a_second_run_needs_no_network_and_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline_overture: Path
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    study, data = _study(tmp_path), tmp_path / "data"
    first = layers.build(study, data, fake.fetcher(data, []))
    assert first.sources["overture_release"] == fake.OVERTURE_RELEASE
    assert first.sources["openstreetmap"]["extract"] == "us/gridland"
    assert "data_until" in first.sources["openstreetmap"]
    # Now with nothing to reach: no key, no Overture listing, and every service refusing.
    monkeypatch.delenv("CENSUS_API_KEY")
    shutil.rmtree(offline_overture)
    calls: list[str] = []
    again = layers.build(study, data, fake.fetcher(data, calls))
    assert calls == []
    assert again.homes.home_id.tolist() == first.homes.home_id.tolist()
    assert again.sources == first.sources


def test_a_damaged_saved_building_file_is_read_again(tmp_path: Path) -> None:
    bbox = (fake.WEST, fake.SOUTH, fake.WEST + 4 * fake.STEP, fake.SOUTH + 4 * fake.STEP)
    found, _ = overture.buildings(bbox, tmp_path / "cache")
    (saved,) = (tmp_path / "cache").glob("overture-buildings-*.parquet")
    saved.write_bytes(b"half a file")
    again, _ = overture.buildings(bbox, tmp_path / "cache")
    assert again.building_id.tolist() == found.building_id.tolist()
    assert not list((tmp_path / "cache").glob("*.part"))


def test_with_no_listing_and_nothing_saved_the_buildings_are_an_error(
    tmp_path: Path, offline_overture: Path
) -> None:
    bbox = (fake.WEST, fake.SOUTH, fake.WEST + 4 * fake.STEP, fake.SOUTH + 4 * fake.STEP)
    shutil.rmtree(offline_overture)
    with pytest.raises(DownloadError, match="could not list Overture releases"):
        overture.buildings(bbox, tmp_path / "cache")


def test_a_destination_outside_the_area_is_refused_before_the_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    study = _study(tmp_path)
    swapped = replace(study.destinations[0], location=(40.004, -99.996))  # latitude first
    calls: list[str] = []
    with pytest.raises(config.ConfigError, match=r"'school': location .* outside the study area"):
        layers.build(
            replace(study, destinations=(swapped, study.destinations[1])),
            tmp_path / "data",
            fake.fetcher(tmp_path / "data", calls),
        )
    assert not any("geofabrik" in call or "Tracts_Blocks" in call for call in calls)
    far = replace(study.destinations[0], access_points=((-99.5, 40.004),))
    with pytest.raises(config.ConfigError, match="access point"):
        layers.build(
            replace(study, destinations=(far, study.destinations[1])),
            tmp_path / "data",
            fake.fetcher(tmp_path / "data", []),
        )


def test_a_study_s_own_street_file_is_named_not_quoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    own = tmp_path / "streets.json"
    own.write_text(
        json.dumps({**fake.overpass_answer(), "source": {"file": "/home/someone/private.osm.pbf"}})
    )
    study = _study(tmp_path)
    study = replace(study, area=replace(study.area, osm_file=own))
    town = layers.build(study, tmp_path / "data", fake.fetcher(tmp_path / "data", []))
    assert town.sources["openstreetmap"] == {"file": "streets.json"}


def test_a_boundary_file_that_cannot_be_read_is_a_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.geojson"
    bad.write_text("not a map file")
    with pytest.raises(config.ConfigError, match="could not read"):
        layers.outline(bad, "EPSG:26914")


def test_the_manifest_says_what_a_run_used(tmp_path: Path) -> None:
    study = config.load(Path(__file__).resolve().parents[1] / "config" / "demo.yaml")
    manifest = json.loads(cli.build(study, tmp_path / "out").read_text())
    assert manifest["settings"]["destinations"][0]["id"] == "school"
    assert manifest["settings"]["source"] == "demo.yaml"  # a file's name, never its folder
    assert manifest["settings"]["time_caps_min"] == {"walk": 20.0, "bike": 20.0}
    assert {"geopandas", "shapely", "pyproj"} <= set(manifest["packages"])
    assert manifest["written"].endswith("+00:00") and manifest["local_config"] is None
    assert manifest["inputs"] == {} and manifest["data"] == {}
    plain = cli._plain(_study(tmp_path))
    assert plain["destinations"][1]["boundary_file"] == "centre.geojson"
    assert str(tmp_path) not in json.dumps(plain)


def test_refreshing_the_streets_removes_the_saved_copies(tmp_path: Path) -> None:
    from walkbike_routes.download import geofabrik

    cache = tmp_path / "cache"
    bbox = (fake.WEST - 0.001, fake.SOUTH - 0.001, fake.WEST + 0.0045, fake.SOUTH + 0.0045)
    geofabrik.area_elements(bbox, fake.fetcher(cache, []))
    (cache / "blocks-0123.json").write_text("{}")  # other saved answers stay
    removed = geofabrik.forget(cache)
    assert sorted(p.name.split("-")[0] for p in removed) == ["geofabrik", "gridland", "osm"]
    assert [p.name for p in cache.iterdir()] == ["blocks-0123.json"]
    calls: list[str] = []
    geofabrik.area_elements(bbox, fake.fetcher(cache, calls))
    assert any("gridland-latest.osm.pbf" in call for call in calls)
    assert geofabrik.forget(tmp_path / "nowhere") == []
    study = _study(tmp_path)
    own = replace(study, area=replace(study.area, osm_file=tmp_path / "centre.geojson"))
    with pytest.raises(config.ConfigError, match="--refresh-osm"):
        cli.build(own, tmp_path / "out", tmp_path / "data", refresh_osm=True)
