"""A study's analyses, run end to end on the made-up demo town."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import Point, box

from walkbike_routes import __main__ as cli
from walkbike_routes import config, demo, study
from walkbike_routes.network import osm_streets as o

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"


@pytest.fixture(scope="module")
def town() -> demo.Town:
    return demo.build()


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("demo")
    manifest = json.loads(cli.build(config.load(DEMO), out).read_text())
    return out, manifest


def test_every_destination_mode_and_preset_is_written_and_listed(
    run: tuple[Path, dict[str, Any]],
) -> None:
    out, manifest = run
    for destination in ("school", "downtown"):
        for mode, preset in (("walk", "broach-2016"), ("bike", "broach-2011"), ("car", "central")):
            folder = Path(destination) / f"{mode}-{preset}"
            for name in ("routes", "flows", "entrances"):
                assert str(folder / f"{name}.parquet") in manifest["files"]
        assert f"{destination}/overlap.parquet" in manifest["files"]
    assert "summary.csv" in manifest["files"]
    summary = pd.read_csv(out / "summary.csv")
    assert len(summary) == 6
    # The school counts its own ages; Downtown counts everyone.
    assert set(zip(summary.destination_id, summary.weight_unit, strict=True)) == {
        ("school", "residents aged 5-11"),
        ("downtown", "residents"),
    }


def test_route_shares_add_up_to_each_origin_s_weight(run: tuple[Path, dict[str, Any]]) -> None:
    out, _ = run
    shares = pd.read_parquet(out / "school" / "bike-broach-2011" / "route_shares.parquet")
    by_origin = shares.groupby("origin_id")
    assert by_origin.route_share.sum().to_numpy() == pytest.approx(1.0)
    assert by_origin.size().max() <= 3 and by_origin.size().max() > 1
    flows = gpd.read_parquet(out / "school" / "bike-broach-2011" / "flows.parquet")
    assert flows.potential_flow.max() <= shares.assigned_weight.sum() + 1e-9
    assert flows.not_observed_use.all()


def test_walking_is_slower_than_cycling_and_the_cap_bites(run: tuple[Path, dict[str, Any]]) -> None:
    out, _ = run
    summary = pd.read_csv(out / "summary.csv").set_index(["destination_id", "mode"])
    school = summary.loc["school"]
    assert school.median_minutes["walk"] > school.median_minutes["bike"]
    assert school.within_cap_weight["walk"] < school.total_weight["walk"]
    assert pd.isna(school.time_cap_min["car"])
    assert (school.within_5_min_weight <= school.within_10_min_weight).all()
    # The median shown beside the limit is of the homes within it, so never above it.
    walk = school.loc["walk"].to_dict()
    assert walk["median_minutes_within_cap"] <= min(walk["time_cap_min"], walk["median_minutes"])
    assert school.median_minutes_within_cap["car"] == school.median_minutes["car"]  # no cap


def test_cars_go_there_and_back(run: tuple[Path, dict[str, Any]]) -> None:
    out, _ = run
    routes = pd.read_parquet(out / "school" / "car-central" / "routes.parquet")
    assert set(routes.direction) == {"to_destination", "from_destination"}
    flows = gpd.read_parquet(out / "school" / "car-central" / "flows.parquet")
    total = flows.flow_to_destination.fillna(0) + flows.flow_from_destination.fillna(0)
    assert flows.potential_flow.to_numpy() == pytest.approx(total.to_numpy())


def test_the_overlap_layer_marks_tiers(run: tuple[Path, dict[str, Any]]) -> None:
    out, _ = run
    layer = gpd.read_parquet(out / "school" / "overlap.parquet")
    assert {"top 1%", "top 5%", "top 10%"} <= set(layer.overlap_tier)
    assert layer.overlap_index.between(0, 1).all()


def _study(**changes: Any) -> config.Study:
    raw = copy.deepcopy(yaml.safe_load(DEMO.read_text()))
    raw.update(changes)
    modes = set(raw["modes"])
    for key in ("presets", "time_caps_min"):
        if isinstance(raw.get(key), dict):
            raw[key] = {m: v for m, v in raw[key].items() if m in modes}
    return config.parse(raw, base=DEMO.parent)


def test_all_or_nothing_and_households(town: demo.Town, tmp_path: Path) -> None:
    settings = _study(
        modes=["bike"], route_choice={"routes": 1}, demand={"weight": "households"},
        destinations=[{"id": "school", "name": "School", "kind": "point"}],
    )  # fmt: skip
    study.analyze(settings, town, tmp_path)
    shares = pd.read_parquet(tmp_path / "school" / "bike-broach-2011" / "route_shares.parquet")
    assert shares.route_share.eq(1.0).all() and shares.origin_id.is_unique
    summary = pd.read_csv(tmp_path / "summary.csv")
    assert summary.weight_unit.tolist() == ["households"]
    assert summary.total_weight.iloc[0] == town.homes.households.sum()


def test_configured_car_stops_are_used_and_entrances_otherwise(
    town: demo.Town, tmp_path: Path
) -> None:
    gates = town.access_points.assign(car=False)
    curb = gpd.GeoDataFrame(
        {"access_point_id": ["school_car_1"], "destination_id": ["school"], "car": [True]},
        geometry=[Point(500840, 4401140)],
        crs=town.access_points.crs,
    )
    with_curb = demo.Town(
        town.nodes, town.links, town.homes, town.destinations, pd.concat([gates, curb])
    )
    settings = _study(modes=["walk", "car"], destinations=[
        {"id": "school", "name": "School", "kind": "point"}])  # fmt: skip
    study.analyze(settings, with_curb, tmp_path)
    car_stops = gpd.read_parquet(tmp_path / "school" / "car-central" / "entrances.parquet")
    assert len(car_stops) == 1 and car_stops.geometry.iloc[0].distance(curb.geometry.iloc[0]) <= 60
    walk = gpd.read_parquet(tmp_path / "school" / "walk-broach-2016" / "entrances.parquet")
    assert set(walk.access_point_id) == set(town.access_points.access_point_id)


def test_an_area_with_no_street_inside_or_across_is_refused(
    town: demo.Town, tmp_path: Path
) -> None:
    far = town.destinations.copy()
    far = far.set_geometry(
        [
            box(0, 0, 10, 10) if d == "downtown" else g
            for d, g in zip(far.destination_id, far.geometry, strict=True)
        ]
    )
    settings = _study(modes=["walk"], destinations=[
        {"id": "downtown", "name": "D", "kind": "area"}])  # fmt: skip
    lonely = demo.Town(town.nodes, town.links, town.homes, far, town.access_points)
    with pytest.raises(config.ConfigError, match="no street enters its area"):
        study.analyze(settings, lonely, tmp_path)


def test_far_entrances_and_a_missing_weight_column_are_refused(
    town: demo.Town, tmp_path: Path
) -> None:
    far = town.access_points.copy()
    far = far.set_geometry([Point(0, 0)] * len(far))
    settings = _study(modes=["walk"], destinations=[
        {"id": "school", "name": "S", "kind": "point"}])  # fmt: skip
    with pytest.raises(config.ConfigError, match="more than 200 m"):
        study.analyze(settings, demo.Town(town.nodes, town.links, town.homes,
                                          town.destinations, far), tmp_path)  # fmt: skip
    with pytest.raises(
        config.ConfigError,
        match="no count of residents aged 0-4 .they have: households, residents, residents_5_11.",
    ):
        study.analyze(_study(demand={"weight": "residents", "max_age": 4}), town, tmp_path)


@pytest.mark.parametrize(
    ("demand", "column", "unit"),
    [(config.Demand("households"), "households", "households"),
     (config.Demand("residents"), "residents", "residents"),
     (config.Demand("residents", min_age=65), "residents_65_plus", "residents aged 65+"),
     (config.Demand("residents", max_age=17), "residents_0_17", "residents aged 0-17")],
)  # fmt: skip
def test_weight_columns_follow_the_demand(demand: config.Demand, column: str, unit: str) -> None:
    assert study.weight_column(demand) == (column, unit)


@pytest.mark.parametrize(
    ("raw", "message"),
    [({"route_choice": {"routes": 2.5}}, "whole number"),
     ({"route_choice": {"routes": 0}}, "route_choice: route choice needs"),
     ({"route_choice": {"bogus": 1}}, "bogus"),
     ({"time_bands_min": [10, 5]}, "must increase"),
     ({"time_bands_min": []}, "list of minutes")],
)  # fmt: skip
def test_bad_route_choice_and_bands_are_refused(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        _study(**raw)


def test_car_access_points_reach_the_layers() -> None:
    settings = _study(destinations=[
        {"id": "s", "name": "S", "kind": "point", "car_access_points": [[-122.1, 37.4]]}])  # fmt: skip
    assert settings.destinations[0].car_access_points == ((-122.1, 37.4),)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [({"highway": "residential", "oneway": "yes", "oneway:bicycle": "no"}, "no"),
     ({"highway": "residential", "oneway": "yes", "cycleway": "opposite_lane"}, "no"),
     ({"highway": "residential", "oneway": "yes", "cycleway:left:oneway": "-1"}, "no"),
     ({"highway": "residential", "oneway:bicycle": "yes"}, "yes"),
     ({"highway": "residential", "oneway": "yes"}, None)],
)  # fmt: skip
def test_bicycle_exceptions_to_one_way_rules(tags: dict[str, str], expected: str | None) -> None:
    assert o.oneway_bicycle(tags) == expected


def test_a_study_with_no_street_open_to_cars_is_refused(town: demo.Town, tmp_path: Path) -> None:
    paths = town.links.assign(highway="footway", car_allowed=False)
    settings = _study(modes=["car"], destinations=[
        {"id": "school", "name": "S", "kind": "point"}])  # fmt: skip
    no_cars = demo.Town(town.nodes, paths, town.homes, town.destinations, town.access_points)
    with pytest.raises(config.ConfigError, match="no street in the study area is open to cars"):
        study.analyze(settings, no_cars, tmp_path)


def _a_screen(path: Path) -> None:
    screen = gpd.read_parquet(path)
    assert len(screen) > 0
    for _, ranked in screen.groupby("treatment"):  # each treatment is ranked on its own
        assert len(ranked) <= 25 and ranked["rank"].tolist() == list(range(1, len(ranked) + 1))
        assert ranked.weighted_generalized_minutes_saved.is_monotonic_decreasing


def test_walk_and_bike_get_screens_and_cars_do_not(
    run: tuple[Path, dict[str, Any]], town: demo.Town, tmp_path: Path
) -> None:
    out, manifest = run
    assert "school/bike-broach-2011/screen.parquet" in manifest["files"]
    _a_screen(out / "school" / "bike-broach-2011" / "screen.parquet")
    # The adult walking model has no sidewalk term, so its sidewalk screen is not run;
    # crosswalks and signals are.
    walk = gpd.read_parquet(out / "school" / "walk-broach-2016" / "screen.parquet")
    assert "sidewalk" not in set(walk.treatment)
    assert set(walk.treatment) == {"marked crosswalk", "signal or crossing beacon"}
    bike = gpd.read_parquet(out / "school" / "bike-broach-2011" / "screen.parquet")
    assert set(bike.treatment) == {"protected bike lane", "signal or crossing beacon"}
    assert not (out / "school" / "car-central" / "screen.parquet").exists()
    # The hand-set stress-factor kind (bike/furth) is screened too.
    settings = _study(modes=["bike"], presets={"bike": ["furth"]})
    study.analyze(settings, town, tmp_path)
    _a_screen(tmp_path / "school" / "bike-furth" / "screen.parquet")


def test_screens_can_be_turned_off(town: demo.Town, tmp_path: Path) -> None:
    settings = _study(modes=["walk"], screen_candidates=0)
    study.analyze(settings, town, tmp_path)
    assert not (tmp_path / "school" / "walk-broach-2016" / "screen.parquet").exists()
    for bad in (-1, 2.5, "many"):
        with pytest.raises(config.ConfigError, match="screen_candidates"):
            _study(screen_candidates=bad)


@pytest.mark.parametrize(
    ("area", "message"),
    [({"buildings": "lidar"}, "area.buildings"),
     ({"overture_release": "latest"}, "expected a name like"),
     ({"traffic_counts": "counted"}, "area.traffic_counts")],
)  # fmt: skip
def test_bad_building_settings_are_refused(area: dict[str, Any], message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        _study(area={"source": "place", "place": "Somewhere, Oregon", **area})


def test_a_pinned_overture_release_is_kept() -> None:
    area = config._area(
        {"source": "place", "place": "Somewhere, Oregon", "overture_release": "2026-10-21.0"},
        Path("."),
    )
    assert area.overture_release == "2026-10-21.0" and area.buildings == "overture"


def test_the_median_within_the_limit_works_out_the_limit_if_routes_lack_the_flag() -> None:
    from types import SimpleNamespace

    from walkbike_routes import study_context

    routes = pd.DataFrame(
        {"demand_weight": [1.0, 3.0], "route_reachable": [True, True],
         "total_travel_time_s": [5 * 60.0, 30 * 60.0]}
    )  # fmt: skip
    context = SimpleNamespace(
        origins=pd.DataFrame({"weight_unit": ["residents"]}),
        study=SimpleNamespace(time_bands_min=[5, 10, 15, 20]),
    )
    destination = SimpleNamespace(id="school", outline=None, serves=None, nearest=None)
    row = study_context.summary(context, destination, "walk", "p", routes, None, 20.0)  # type: ignore[arg-type]
    assert row["median_minutes_within_cap"] == 5.0 and row["within_cap_weight"] == 1.0
    assert row["median_minutes"] == 30.0
    assert row["homes_counted"] == ""  # no area of its own


def test_each_destination_says_whose_homes_it_counts() -> None:
    from types import SimpleNamespace

    from walkbike_routes import config, study_context

    def school(id: str, **kw: object) -> SimpleNamespace:
        given = {"outline": None, "serves": None, "nearest": None} | kw
        return SimpleNamespace(id=id, name=f"{id.title()} School", **given)

    district = config.Outline(file=Path("d.shp"), name="Hill district")
    drawn = config.Outline(file=Path("d.shp"), name="Hill district", limits=False)
    a, b = school("a", outline=district, nearest="high"), school("b", nearest="high")
    c, d = school("c", nearest="high"), school("d", serves=Path("s.geojson"))
    study = SimpleNamespace(destinations=[a, b, c, d])
    words: Any = study_context.homes_counted  # takes stand-ins for a study and destinations
    assert (
        words(study, a)
        == "in the Hill district, nearer by street to it than to B School or C School"
    )
    assert words(study, b) == "nearer by street to it than to A School or C School"
    assert words(study, d) == "in the area it serves"
    plain = config.Outline(file=Path("d.shp"), name="Attendance area")
    assert words(study, school("e", outline=plain)) == "in the attendance area"
    assert words(study, school("f", outline=drawn)) == ""
    assert words(study, school("g")) == ""


def test_a_presets_network_is_built_once_for_all_destinations(
    town: demo.Town, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from walkbike_routes import study_networks
    from walkbike_routes.network import cars

    built: list[str] = []
    links, roads = study_networks.preset_links, cars.build

    def count_links(edges: Any, nodes: Any, preset: Any, signals: Any = None) -> Any:
        built.append(preset.name)
        return links(edges, nodes, preset, signals)

    def count_roads(*args: Any, **kwargs: Any) -> Any:
        built.append("car")
        return roads(*args, **kwargs)

    monkeypatch.setattr(study_networks, "preset_links", count_links)
    monkeypatch.setattr(cars, "build", count_roads)
    settings = _study()
    assert len(settings.destinations) == 2
    study.analyze(settings, town, tmp_path)
    assert sorted(built) == sorted(
        ["car", *(name for mode in ("walk", "bike") for name in settings.presets[mode])]
    )
    # Rows stay in the order of destinations, then modes, then presets.
    summary = pd.read_csv(tmp_path / "summary.csv")
    assert summary.destination_id.tolist() == sorted(
        summary.destination_id, key=[d.id for d in settings.destinations].index
    )


def test_homes_join_the_main_piece_of_the_network_however_far() -> None:
    from shapely.geometry import LineString

    from walkbike_routes.demand import attach_origins_to_edges
    from walkbike_routes.models import Mode
    from walkbike_routes.study_context import connected

    crs = "EPSG:32613"
    # A made-up main street in two blocks, and a loose aisle joined to nothing.
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["main-1", "main-2", "aisle"], "u": ["a", "b", "x"],
         "v": ["b", "c", "y"]},
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)]),
                  LineString([(50, 300), (60, 300)])],
        crs=crs,
    )  # fmt: skip
    nodes = gpd.GeoDataFrame(
        {"node_id": ["a", "b", "c", "x", "y"]},
        geometry=[Point(0, 0), Point(100, 0), Point(200, 0), Point(50, 300), Point(60, 300)],
        crs=crs,
    )
    assert list(connected(edges, nodes).physical_edge_id) == ["main-1", "main-2"]
    assert connected(edges.iloc[:0], nodes).empty
    # Streets whose ends routes cannot use are no part of any piece.
    only = nodes[nodes.node_id.isin(["x", "y"])]
    assert list(connected(edges, only).physical_edge_id) == ["aisle"]
    # One home beside the aisle, 290 m from the main street; one a kilometre off.
    homes = gpd.GeoDataFrame(
        {"origin_id": ["by-aisle", "far"]}, geometry=[Point(55, 290), Point(150, 1000)], crs=crs
    )

    def attach(streets: gpd.GeoDataFrame, limit: float | None) -> pd.DataFrame:
        return attach_origins_to_edges(
            homes, streets, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=limit,
            attachment_policy="nearest street",
        ).set_index("origin_id")  # fmt: skip

    joined = attach(connected(edges, nodes), None)
    assert list(joined.attached_edge_id) == ["main-1", "main-2"]
    assert joined.network_attached.all()
    assert joined.edge_distance_m.tolist() == pytest.approx([290.0, 1000.0])
    # On the whole map the first home lands on the aisle; with a limit, both are left out.
    assert attach(edges, None).attached_edge_id["by-aisle"] == "aisle"
    assert not attach(connected(edges, nodes), 200.0).network_attached.any()
