"""Broach's (2016) walking route-choice preset."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import LineString, Point

from walkbike_routes import config, demo, study
from walkbike_routes.costs import presets
from walkbike_routes.network import osm_streets as o
from walkbike_routes.network import prepare
from walkbike_routes.network.base import routable_links_to_graph
from walkbike_routes.network.broach_walk import (
    WalkBroachMovements,
    WalkBroachProfile,
    build_walk_broach_links,
    crossing_metres,
)
from walkbike_routes.network.sidewalks import match_sidewalks, streets_with_separate_sidewalks

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"
CRS = 26910
SPEED = 3 * 0.44704  # the preset's 3 mph, in m/s


def _profile(**changes: Any) -> WalkBroachProfile:
    profile = presets.load("walk", "broach-2016").profile()
    assert isinstance(profile, WalkBroachProfile)
    if changes:
        profile = WalkBroachProfile(**{**profile.__dict__, **changes})
    return profile


# Table 11: distance equivalents, from the unrounded estimates.
TABLE_11_METRES = [("turns", 54), ("unsignalised_arterial_crossings", 73),
                   ("unmarked_collector_crossings", 28)]  # fmt: skip
TABLE_11_PERCENT = [("steep_upslope_m", 99), ("substandard_m", 51), ("busy_m", 14),
                    ("commercial_m", -28)]  # fmt: skip


@pytest.mark.parametrize(("name", "metres"), TABLE_11_METRES)
def test_the_coefficients_reproduce_table_11_distances(name: str, metres: int) -> None:
    assert round(_profile().metres(name)) == metres


@pytest.mark.parametrize(("name", "percent"), TABLE_11_PERCENT)
def test_the_coefficients_reproduce_table_11_shares(name: str, percent: int) -> None:
    # Table 10 prints three decimals, so small terms differ from Table 11 by rounding.
    assert -100 * _profile().metres(name) == pytest.approx(-percent, abs=3)


def test_traveling_together_raises_the_cost_of_distance() -> None:
    together = _profile(together=True)
    assert together.distance_coefficient == pytest.approx(-0.028)
    # Table 11: +85% detour cost, from the unrounded estimates.
    assert 0.013 / 0.015 == pytest.approx(0.85, abs=0.03)
    assert together.metres("turns") == pytest.approx(0.809 / 0.028)


def test_the_logit_scale_follows_from_the_distance_coefficient() -> None:
    assert _profile().logit_scale_s == pytest.approx(1 / (0.015 * SPEED))


def test_bad_profiles_are_refused() -> None:
    good = dict(_profile().coefficients)
    with pytest.raises(ValueError, match="missing"):
        WalkBroachProfile("x", coefficients={k: v for k, v in good.items() if k != "turns"})
    with pytest.raises(ValueError, match="negative"):
        WalkBroachProfile("x", coefficients={**good, "distance_m": 0.01})
    with pytest.raises(ValueError, match="path-size"):
        WalkBroachProfile("x", coefficients={**good, "ln_path_size": -1.0})
    with pytest.raises(ValueError, match="speed"):
        WalkBroachProfile("x", coefficients=good, speed_mph=0)


def _net(
    extra: list[tuple[str, str, str, str, dict[str, Any]]] | None = None,
    control_c: str = "none",
    shapes: dict[str, LineString] | None = None,
    main: str = "secondary",
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Elm, residential, runs a-b-c-d; Main, a secondary road, crosses it at c
    (n to s).  An alley leaves b to the south.  a-b climbs 15%."""
    points = {"a": (0, 0), "b": (200, 0), "c": (400, 0), "d": (600, 0), "n": (400, 200),
              "s": (400, -200), "y": (200, -100), "w1": (380, 30), "w2": (420, 30),
              "q": (420, 230)}  # fmt: skip
    nodes = gpd.GeoDataFrame(
        {"node_id": list(points),
         "control": ["none", "none", control_c, "none", "none", "none", "none", "none", "none",
                     "none"],
         "elevation_m": [0.0] + [30.0] * 9},
        geometry=[Point(p) for p in points.values()], crs=CRS,
    )  # fmt: skip
    rows: list[tuple[str, str, str, str, dict[str, Any]]] = [
        ("ab", "a", "b", "residential", {"name": "Elm"}),
        ("bc", "b", "c", "residential", {"name": "Elm"}),
        ("cd", "c", "d", "residential", {"name": "Elm"}),
        ("nc", "n", "c", main, {"name": "Main"}),
        ("cs", "c", "s", main, {"name": "Main"}),
        ("by", "b", "y", "service", {"service": "alley"}),
        *(extra or []),
    ]
    columns = ["name", "service", "surface", "footway", "crossed_road", "crossing_marking",
               "crossing", "movement_type", "osm_way_id"]  # fmt: skip
    links = gpd.GeoDataFrame(
        {"link_id": [r[0] for r in rows], "u": [r[1] for r in rows], "v": [r[2] for r in rows],
         "highway": [r[3] for r in rows], "bike_facility": ["none"] * len(rows),
         "lanes": [None] * len(rows), "speed_mph": [None] * len(rows),
         "sidewalk": ["unknown"] * len(rows),
         **{c: [r[4].get(c, "" if c != "osm_way_id" else None) for r in rows] for c in columns}},
        geometry=[(shapes or {}).get(r[0]) or LineString([points[r[1]], points[r[2]]])
                  for r in rows], crs=CRS,
    )  # fmt: skip
    return prepare.physical_edges(links, nodes), prepare.nodes(nodes)


def _row(links: gpd.GeoDataFrame, edge: str, direction: str = "both") -> Any:
    return links[(links.physical_edge_id == edge) & (links.direction == direction)].iloc[0]


def test_link_costs_match_a_hand_calculation() -> None:
    edges, nodes = _net()
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    flat = 200 / SPEED
    up, down = _row(links, "ab", "forward"), _row(links, "ab", "reverse")
    assert up.generalized_cost_s == pytest.approx(flat * (1 + 1.0))  # 15% climb: x 2
    assert down.generalized_cost_s == pytest.approx(flat)
    assert up.travel_time_s > flat  # hills slow travel time, apart from the cost
    assert _row(links, "nc").generalized_cost_s == pytest.approx(flat * (1 + 0.002 / 0.015))
    alley = 100 / SPEED
    assert _row(links, "by").generalized_cost_s == pytest.approx(alley * (1 + 0.008 / 0.015))
    assert _row(links, "bc").generalized_cost_s == pytest.approx(flat)


def test_unknown_and_missing_sidewalks_are_walked_at_no_extra_cost() -> None:
    edges, nodes = _net()
    edges["pedestrian_status"] = "problematic"  # a sidewalk known to be missing
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    assert set(links.physical_edge_id) == set(edges.physical_edge_id)
    assert _row(links, "bc").generalized_cost_s == pytest.approx(200 / SPEED)


def _child() -> WalkBroachProfile:
    profile = presets.load("walk", "broach-2016-child").profile()
    assert isinstance(profile, WalkBroachProfile)
    return profile


def test_the_child_preset_doubles_the_stress_terms_only() -> None:
    adult, child = _profile(), _child()
    for name in ("busy_m", "unsignalised_arterial_crossings", "unmarked_collector_crossings"):
        assert child.metres(name) == pytest.approx(2 * adult.metres(name))
    for name in ("turns", "steep_upslope_m", "substandard_m"):
        assert child.metres(name) == pytest.approx(adult.metres(name))
    assert child.values_sidewalks and not adult.values_sidewalks


def test_the_child_preset_charges_for_missing_sidewalk_evidence() -> None:
    edges, nodes = _net()
    child = _child()
    flat = 200 / (2.5 * 0.44704)
    links = build_walk_broach_links(edges, child, nodes=nodes)
    # Unknown evidence on a residential street: no sidewalk charge.
    assert _row(links, "bc").generalized_cost_s == pytest.approx(flat)
    # Main, a secondary road: twice its length, plus twice the busy term (about 2.3).
    assert _row(links, "nc").generalized_cost_s == pytest.approx(flat * (2 + 2 * 0.002 / 0.015))
    # A mapped sidewalk on Main costs only the busy term; a known missing one, as unknown.
    edges["pedestrian_status"] = ["supported" if e == "nc" else "problematic"
                                  for e in edges.physical_edge_id]  # fmt: skip
    links = build_walk_broach_links(edges, child, nodes=nodes)
    assert _row(links, "nc").generalized_cost_s == pytest.approx(flat * (1 + 2 * 0.002 / 0.015))
    assert _row(links, "cs").generalized_cost_s == pytest.approx(flat * (2 + 2 * 0.002 / 0.015))
    assert _row(links, "cd").generalized_cost_s == pytest.approx(flat)
    assert set(links.physical_edge_id) == set(edges.physical_edge_id)  # nothing left out


def test_the_sidewalk_preset_charges_only_busier_roads_without_evidence() -> None:
    profile = presets.load("walk", "broach-2016-sidewalks").profile()
    assert isinstance(profile, WalkBroachProfile) and profile.values_sidewalks
    edges, nodes = _net()
    links = build_walk_broach_links(edges, profile, nodes=nodes)
    flat = 200 / SPEED
    # Elm, residential, unknown evidence: no sidewalk charge.
    assert _row(links, "bc").generalized_cost_s == pytest.approx(flat)
    # Main, a secondary road: 1.5 times, plus the busy term.
    assert _row(links, "nc").generalized_cost_s == pytest.approx(flat * (1.5 + 0.002 / 0.015))
    edges["pedestrian_status"] = "supported"
    links = build_walk_broach_links(edges, profile, nodes=nodes)
    assert _row(links, "nc").generalized_cost_s == pytest.approx(flat * (1 + 0.002 / 0.015))


def test_bad_child_settings_are_refused() -> None:
    with pytest.raises(ValueError, match="stress_scale"):
        _profile(stress_scale=0.0)
    with pytest.raises(ValueError, match="at least 1"):
        _profile(unknown_local_factor=0.5)


def test_access_rules_still_apply() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id == "bc", "ped_allowed"] = False
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    assert "bc" not in set(links.physical_edge_id)


def test_signal_waits_stay_in_travel_time_but_not_in_the_cost() -> None:
    edges, nodes = _net(control_c="signal")
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    into = links[(links.physical_edge_id == "bc") & (links.signal_wait_s > 0)]
    assert not into.empty
    row = into.iloc[0]
    assert row.travel_time_s == pytest.approx(200 / SPEED + row.signal_wait_s)
    assert row.generalized_cost_s == pytest.approx(200 / SPEED)


def test_unpaved_streets_are_substandard_but_unpaved_paths_are_not() -> None:
    extra = [("dirt", "d", "q", "residential", {"surface": "dirt"}),
             ("trail", "q", "n", "path", {"surface": "gravel"})]  # fmt: skip
    edges, nodes = _net(extra)
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    dirt = _row(links, "dirt")
    assert dirt.stress_cost_s == pytest.approx(dirt.physical_length_m / SPEED * 0.008 / 0.015)
    assert _row(links, "trail").stress_cost_s == 0


def test_a_crossing_way_charges_its_crossing_once_across_its_pieces() -> None:
    crossing = {"crossed_road": "secondary", "osm_way_id": 7}
    extra = [("x1", "w1", "c", "footway", crossing), ("x2", "c", "w2", "footway", crossing)]
    edges, nodes = _net(extra)
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    total = _row(links, "x1").crossing_cost_s + _row(links, "x2").crossing_cost_s
    assert total == pytest.approx(1.090 / 0.015 / SPEED)


def test_crossing_terms_depend_on_the_road_marking_and_signal() -> None:
    profile = _profile()
    arterial = {"crossed_road": "primary", "crossing_marking": "marked"}
    assert crossing_metres(profile, arterial) == pytest.approx(1.090 / 0.015)
    assert crossing_metres(profile, {**arterial, "crossing": "traffic_signals"}) == 0
    collector = {"crossed_road": "tertiary", "crossing_marking": "unmarked"}
    assert crossing_metres(profile, collector) == pytest.approx(0.419 / 0.015)
    assert crossing_metres(profile, {**collector, "crossing_marking": "marked"}) == 0
    assert crossing_metres(profile, {**collector, "crossing_marking": ""}) == 0  # unknown: marked
    assert crossing_metres(profile, {"crossed_road": "residential"}) == 0
    signalled = {
        "crossing_road": "primary",
        "crossing": "traffic_signals",
        "movement_type": "across",
    }
    assert crossing_metres(profile, signalled) == 0


def _moves(edges: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame) -> WalkBroachMovements:
    profile = _profile()
    links = build_walk_broach_links(edges, profile, nodes=nodes)
    graph = routable_links_to_graph(links, directed=True)
    return WalkBroachMovements(graph, edges, nodes, profile)  # type: ignore[arg-type]


def test_walking_on_across_an_arterial_without_a_signal_pays_the_crossing() -> None:
    moves = _moves(*_net())
    ahead = moves("b", "c", "d")  # along Elm, across Main
    assert ahead.crossing_cost_s == pytest.approx(1.090 / 0.015 / SPEED)
    assert ahead.turn_cost_s == 0
    onto = moves("b", "c", "n")  # turning onto Main: walked along, not crossed
    assert onto.crossing_cost_s == 0
    assert onto.turn_cost_s == pytest.approx(0.809 / 0.015 / SPEED)
    assert moves("n", "c", "s").crossing_cost_s == 0  # along Main past Elm


def test_a_signal_removes_the_crossing_but_not_the_turn() -> None:
    moves = _moves(*_net(control_c="signal"))
    assert moves("b", "c", "d").crossing_cost_s == 0
    assert moves("b", "c", "n").turn_cost_s > 0


def test_a_collector_crossing_counts_as_unmarked() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id.isin(["nc", "cs"]), "highway"] = "tertiary"
    assert _moves(edges, nodes)("b", "c", "d").crossing_cost_s == pytest.approx(
        0.419 / 0.015 / SPEED
    )


def test_a_bend_where_only_two_ways_meet_is_no_turn() -> None:
    extra = [("dq", "d", "q", "residential", {"name": "Oak"})]
    moves = _moves(*_net(extra))
    assert moves("c", "d", "q").turn_cost_s == 0


def test_stepping_onto_a_crossing_way_is_no_turn_and_no_second_crossing() -> None:
    crossing = {"crossed_road": "secondary"}
    extra = [("x1", "w1", "c", "footway", crossing), ("x2", "c", "w2", "footway", crossing)]
    moves = _moves(*_net(extra))
    step = moves("w1", "c", "w2")
    assert step.turn_cost_s == 0
    assert step.crossing_cost_s == 0


def test_a_separate_sidewalk_takes_its_streets_class_and_name() -> None:
    extra = [("sw", "w1", "q", "footway", {"footway": "sidewalk"})]
    # Ends at w1 and q, but drawn alongside Main, 8 m west of it.
    shape = LineString([(380, 30), (392, 40), (392, 190), (420, 230)])
    edges, _ = _net(extra, shapes={"sw": shape})
    beside = match_sidewalks(edges)
    row = beside.loc[edges.physical_edge_id == "sw"].iloc[0]
    assert (row.beside_road, row.beside_name) == ("secondary", "Main")
    assert (beside.loc[edges.physical_edge_id == "bc"] == "").all(axis=None)


def test_a_sidewalk_across_or_far_from_a_street_is_not_matched() -> None:
    extra = [("sw", "w1", "w2", "footway", {"footway": "sidewalk"}),
             ("far", "d", "q", "footway", {"footway": "sidewalk"})]  # fmt: skip
    edges, _ = _net(extra, shapes={"far": LineString([(600, 0), (600, 300)])})
    beside = match_sidewalks(edges)
    # sw runs east-west across Main, 30 m from Elm; far runs north-south, 200 m from Main.
    assert (beside.loc[edges.physical_edge_id.isin(["sw", "far"])] == "").all(axis=None)


def _lined(edges: gpd.GeoDataFrame, edge: str) -> Any:
    return edges.loc[edges.physical_edge_id == edge].iloc[0]


def test_a_street_lined_by_a_separate_sidewalk_counts_as_having_one() -> None:
    # One sidewalk way, 8 m north of Elm, from b to d: beside two pieces of Elm.
    extra = [("sw", "b", "d", "footway", {"footway": "sidewalk"})]
    shape = LineString([(200, 0), (205, 8), (595, 8), (600, 0)])
    edges, _ = _net(extra, shapes={"sw": shape})
    for piece in ("bc", "cd"):
        row = _lined(edges, piece)
        assert row.sidewalk == "separate" and row.sidewalk_inferred
        assert row.pedestrian_status == "supported"
    row = _lined(edges, "ab")  # no sidewalk beside it
    assert row.sidewalk == "unknown" and not row.sidewalk_inferred
    assert row.pedestrian_status == "unknown"
    assert not _lined(edges, "nc").sidewalk_inferred  # Main crosses it, not lined by it


def test_a_street_lined_for_only_part_of_its_length_is_left_alone() -> None:
    extra = [("sw", "b", "y", "footway", {"footway": "sidewalk"})]
    shape = LineString([(200, 0), (205, 8), (290, 8)])  # under half of b-c
    edges, _ = _net(extra, shapes={"sw": shape})
    assert not _lined(edges, "bc").sidewalk_inferred
    assert _lined(edges, "bc").pedestrian_status == "unknown"


def test_a_sidewalk_does_not_line_a_bend_of_its_street_running_across_it() -> None:
    streets = gpd.GeoDataFrame(
        {"highway": ["residential", "residential", "footway"], "footway": ["", "", "sidewalk"],
         "name": pd.Categorical(["Elm", "Elm", None])},
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(100, 0), (100, 20)]),
                  LineString([(0, 8), (100, 8)])], crs=CRS,
    )  # fmt: skip
    # The sidewalk lines the first piece; the short second piece turns across it.
    assert streets_with_separate_sidewalks(streets).tolist() == [True, False, False]
    assert match_sidewalks(streets).beside_name.tolist() == ["", "", "Elm"]


def test_only_unknown_or_no_sidewalk_tags_are_replaced() -> None:
    edges, _ = _net([("sw", "b", "d", "footway", {"footway": "sidewalk"})],
                    shapes={"sw": LineString([(200, 0), (205, 8), (595, 8), (600, 0)])})  # fmt: skip
    links = edges.drop(columns=["sidewalk_inferred"]).copy()
    links["sidewalk"] = ["local" if e == "bc" else "no" if e == "cd" else "unknown"
                         for e in links.physical_edge_id]  # fmt: skip
    prepare._infer_separate_sidewalks(links)
    assert _lined(links, "bc").sidewalk == "local"  # a local layer's word stands
    assert _lined(links, "cd").sidewalk == "separate"  # an old-style sidewalk=no is replaced
    assert streets_with_separate_sidewalks(links)[links.physical_edge_id.eq("bc")].all()


def test_a_sidewalk_beside_a_busy_street_pays_the_busy_term() -> None:
    extra = [("sw", "w1", "q", "footway", {"footway": "sidewalk"})]
    # Ends at w1 and q, but drawn alongside Main, 8 m east of it.
    shape = LineString([(380, 30), (408, 40), (408, 190), (420, 230)])
    edges, nodes = _net(extra, shapes={"sw": shape})
    assert edges.set_index("physical_edge_id").beside_name["sw"] == "Main"
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    row = _row(links, "sw")
    assert row.stress_cost_s == pytest.approx(shape.length / SPEED * 0.002 / 0.015)


def test_osm_crossing_ways_keep_their_road_and_marking() -> None:
    def way(wid: int, nodes: list[int], **tags: str) -> dict[str, Any]:
        return {"type": "way", "id": wid, "nodes": nodes, "tags": tags}

    answer = {
        "elements": [
            {"type": "node", "id": 1, "lat": 37.0, "lon": -122.0},
            {"type": "node", "id": 2, "lat": 37.0, "lon": -121.999},
            {"type": "node", "id": 3, "lat": 37.0005, "lon": -121.9995},
            {"type": "node", "id": 4, "lat": 36.9995, "lon": -121.9995},
            {"type": "node", "id": 5, "lat": 37.0, "lon": -121.9995},
            way(10, [1, 5, 2], highway="secondary", name="Main"),
            way(11, [3, 5, 4], highway="footway", footway="crossing", crossing="unmarked"),
            way(12, [1, 3], highway="service", service="alley", surface="gravel"),
        ]
    }
    links, _ = o.streets(answer)
    crossing = links[links.osm_way_id == 11]
    assert set(crossing.crossed_road) == {"secondary"}
    assert set(crossing.crossing_marking) == {"unmarked"}
    assert set(crossing.movement_type) == {"along"}  # unsignalised: still an ordinary path
    alley = links[links.osm_way_id == 12].iloc[0]
    assert (alley.service, alley.surface, alley.crossed_road) == ("alley", "gravel", "")


@pytest.mark.parametrize(
    ("tags", "marking"),
    [
        ({"crossing": "unmarked"}, "unmarked"),
        ({"crossing": "uncontrolled"}, "marked"),
        ({"crossing": "uncontrolled", "crossing:markings": "no"}, "unmarked"),
        ({"crossing:markings": "zebra"}, "marked"),
        ({"crossing_ref": "zebra"}, "marked"),
        ({}, ""),
    ],
)
def test_crossing_markings_are_read(tags: dict[str, str], marking: str) -> None:
    assert o.crossing_marking(tags) == marking


def test_split_crossing_pieces_share_by_length() -> None:
    crossing = {"crossed_road": "secondary", "osm_way_id": 7}
    extra = [("x1", "w1", "c", "footway", crossing), ("x2", "c", "w2", "footway", crossing)]
    edges, _ = _net(extra)
    shares = edges.set_index("physical_edge_id").crossing_share
    assert shares["x1"] + shares["x2"] == pytest.approx(1.0)
    assert shares["bc"] == 0


def test_a_study_runs_with_the_walking_preset(tmp_path: Path) -> None:
    raw: dict[str, Any] = copy.deepcopy(yaml.safe_load(DEMO.read_text()))
    raw["modes"] = ["walk"]
    raw["presets"] = {"walk": ["broach-2016", "shortest"]}
    raw["time_caps_min"] = {"walk": 20}
    raw["route_choice"] = {"routes": 3}
    study.analyze(config.parse(raw, base=DEMO.parent), demo.build(), tmp_path)
    summary = pd.read_csv(tmp_path / "summary.csv")
    assert set(summary.preset) >= {"broach-2016", "shortest"}
    folder = tmp_path / "school" / "walk-broach-2016"
    shares = pd.read_parquet(folder / "route_shares.parquet")
    assert shares.groupby("origin_id").route_share.sum().to_numpy() == pytest.approx(1.0)
    # No sidewalk term in the adult model, so no sidewalk screen; crossings are screened.
    screen = gpd.read_parquet(folder / "screen.parquet")
    assert "sidewalk" not in set(screen.treatment) and len(screen) > 0


def test_the_child_preset_gets_a_sidewalk_screen(tmp_path: Path) -> None:
    raw: dict[str, Any] = copy.deepcopy(yaml.safe_load(DEMO.read_text()))
    raw["modes"] = ["walk"]
    raw["presets"] = {"walk": ["broach-2016-child"]}
    raw["time_caps_min"] = {"walk": 20}
    study.analyze(config.parse(raw, base=DEMO.parent), demo.build(), tmp_path)
    screen = gpd.read_parquet(tmp_path / "school" / "walk-broach-2016-child" / "screen.parquet")
    assert set(screen.treatment) == {"sidewalk", "marked crosswalk", "signal or crossing beacon"}


def test_a_crossing_link_naming_only_its_road_is_charged() -> None:
    # A local layer's across link: crossing_road set, crossed_road not.
    extra = [("x", "w1", "w2", "footway", {"movement_type": "across"})]
    edges, nodes = _net(extra)
    raw = edges.copy()
    raw.loc[raw.physical_edge_id == "x", "crossing_road"] = "secondary"
    raw["crossed_road"] = ""
    raw = raw.rename(columns={"physical_edge_id": "link_id"})
    edges = prepare.physical_edges(raw.drop(columns=["crossing_share"]), None)
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    assert _row(links, "x").crossing_cost_s == pytest.approx(1.090 / 0.015 / SPEED)


def test_halves_of_one_crossing_drawn_as_separate_ways_charge_once() -> None:
    extra = [("x1", "w1", "c", "footway", {"crossed_road": "secondary", "osm_way_id": 7}),
             ("x2", "c", "w2", "footway", {"crossed_road": "secondary", "osm_way_id": 8})]  # fmt: skip
    edges, nodes = _net(extra)
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    total = _row(links, "x1").crossing_cost_s + _row(links, "x2").crossing_cost_s
    assert total == pytest.approx(1.090 / 0.015 / SPEED)


def test_crossings_sharing_a_corner_are_not_merged() -> None:
    # Two crossings of Main meet at a corner (w1) that a driveway also reaches.
    extra = [("x1", "w1", "c", "footway", {"crossed_road": "secondary", "osm_way_id": 7}),
             ("x3", "w1", "n", "footway", {"crossed_road": "secondary", "osm_way_id": 9}),
             ("drive", "w1", "y", "service", {})]  # fmt: skip
    edges, _ = _net(extra)
    shares = edges.set_index("physical_edge_id").crossing_share
    assert shares["x3"] == pytest.approx(1.0)


def test_turning_onto_an_unnamed_arterial_does_not_cross_it() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id.isin(["nc", "cs"]), "name"] = ""
    moves = _moves(edges, nodes)
    assert moves("b", "c", "n").crossing_cost_s == 0
    assert moves("b", "c", "d").crossing_cost_s > 0


def test_a_marking_tagged_on_the_crossing_node_is_read() -> None:
    answer = {
        "elements": [
            {"type": "node", "id": 1, "lat": 37.0, "lon": -122.0},
            {"type": "node", "id": 2, "lat": 37.0, "lon": -121.999},
            {"type": "node", "id": 3, "lat": 37.0005, "lon": -121.9995},
            {"type": "node", "id": 4, "lat": 36.9995, "lon": -121.9995},
            {"type": "node", "id": 5, "lat": 37.0, "lon": -121.9995,
             "tags": {"highway": "crossing", "crossing": "unmarked"}},
            {"type": "way", "id": 10, "nodes": [1, 5, 2],
             "tags": {"highway": "tertiary", "name": "Oak"}},
            {"type": "way", "id": 11, "nodes": [3, 5, 4],
             "tags": {"highway": "footway", "footway": "crossing"}},
        ]
    }  # fmt: skip
    links, _ = o.streets(answer)
    assert set(links[links.osm_way_id == 11].crossing_marking) == {"unmarked"}


def test_a_sidewalk_beside_a_service_street_takes_the_nearer_street() -> None:
    extra = [("sw", "w1", "q", "footway", {"footway": "sidewalk"}),
             ("aisle", "w2", "q", "service", {})]  # fmt: skip
    # The sidewalk runs 15 m east of Main; the aisle 4 m east of the sidewalk.
    shapes = {"sw": LineString([(415, 20), (415, 190)]),
              "aisle": LineString([(419, 20), (419, 190)])}  # fmt: skip
    edges, _ = _net(extra, shapes=shapes)
    assert edges.set_index("physical_edge_id").beside_road["sw"] == "service"


def test_a_sidewalk_ignores_a_nearer_parking_aisle() -> None:
    extra = [("sw", "w1", "q", "footway", {"footway": "sidewalk"}),
             ("aisle", "w2", "q", "service", {"service": "parking_aisle"})]  # fmt: skip
    shapes = {"sw": LineString([(415, 20), (415, 190)]),
              "aisle": LineString([(419, 20), (419, 190)])}  # fmt: skip
    edges, _ = _net(extra, shapes=shapes)
    assert edges.set_index("physical_edge_id").beside_name["sw"] == "Main"


def test_a_marked_crossing_tagged_on_the_junction_node_is_not_unmarked() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id.isin(["nc", "cs"]), "highway"] = "tertiary"
    nodes["crossing_marking"] = ["marked" if n == "c" else "" for n in nodes.node_id]
    assert _moves(edges, nodes)("b", "c", "d").crossing_cost_s == 0


def test_osm_nodes_keep_a_crossing_marking() -> None:
    answer = {
        "elements": [
            {"type": "node", "id": 1, "lat": 37.0, "lon": -122.0},
            {"type": "node", "id": 2, "lat": 37.0, "lon": -121.999,
             "tags": {"highway": "crossing", "crossing:markings": "zebra"}},
            {"type": "node", "id": 3, "lat": 37.0, "lon": -121.998},
            {"type": "way", "id": 10, "nodes": [1, 2, 3], "tags": {"highway": "tertiary"}},
            {"type": "way", "id": 11, "nodes": [2, 3], "tags": {"highway": "residential"}},
        ]
    }  # fmt: skip
    _, nodes = o.streets(answer)
    marking = dict(zip(nodes.node_id, nodes.crossing_marking, strict=True))
    assert marking["n2"] == "marked" and marking["n1"] == ""
    assert "crossing_marking" in prepare.nodes(nodes)


def test_a_repeated_crossing_row_does_not_halve_its_share() -> None:
    extra = [("x", "w1", "w2", "footway", {"crossed_road": "secondary", "osm_way_id": 7})] * 2
    edges, _ = _net(extra)
    assert edges.set_index("physical_edge_id").crossing_share["x"] == pytest.approx(1.0)


def test_one_unmarked_half_makes_the_whole_crossing_unmarked() -> None:
    extra = [("x1", "w1", "c", "footway",
              {"crossed_road": "tertiary", "osm_way_id": 7, "crossing_marking": "unmarked"}),
             ("x2", "c", "w2", "footway", {"crossed_road": "tertiary", "osm_way_id": 8})]  # fmt: skip
    edges, nodes = _net(extra, main="tertiary")  # the halves meet on Main, at c
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    total = _row(links, "x1").crossing_cost_s + _row(links, "x2").crossing_cost_s
    assert total == pytest.approx(0.419 / 0.015 / SPEED)


def test_a_named_sidewalk_takes_its_streets_name() -> None:
    extra = [("sw", "w1", "q", "footway", {"footway": "sidewalk", "name": "Main sidewalk"})]
    shape = LineString([(380, 30), (392, 40), (392, 190), (420, 230)])
    edges, nodes = _net(extra, shapes={"sw": shape})
    moves = _moves(edges, nodes)
    assert moves.edges["sw"][0] == "main"


def test_an_unmarked_road_node_beats_a_marked_way() -> None:
    tags = {"highway": "footway", "footway": "crossing", "crossing": "uncontrolled"}
    roads = {5: {"tertiary"}}
    nodes = {5: {"highway": "crossing", "crossing:markings": "no"}}
    found = o.crossing_attributes(tags, [3, 5, 4], {}, roads, nodes)
    assert found["crossing_marking"] == "unmarked"


def test_sidewalks_beside_an_unnamed_arterial_are_on_that_road() -> None:
    edges, nodes = _net()
    edges.loc[edges.physical_edge_id.isin(["nc", "cs"]), "name"] = ""
    moves = _moves(edges, nodes)
    sidewalk = {"highway": "footway", "beside_road": "secondary", "beside_name": ""}
    road = {"highway": "secondary", "name": ""}
    from walkbike_routes.network.broach_walk import _road

    assert _road(sidewalk, "sw") == _road(road, "nc") == "unnamed secondary"
    assert moves("n", "c", "s").crossing_cost_s == 0


def test_crossings_of_different_roads_meeting_at_a_node_stay_apart() -> None:
    # A crossing of Main (secondary) and one of a tertiary road, meeting at c.
    extra: list[tuple[str, str, str, str, dict[str, Any]]] = [
        ("x1", "w1", "c", "footway", {"crossed_road": "secondary", "osm_way_id": 7}),
        ("x2", "c", "w2", "footway", {"crossed_road": "tertiary", "osm_way_id": 8}),
        ("t", "c", "q", "tertiary", {"name": "Oak"}),
    ]
    edges, _ = _net(extra)
    shares = edges.set_index("physical_edge_id").crossing_share
    assert shares["x1"] == pytest.approx(1.0) and shares["x2"] == pytest.approx(1.0)


def test_a_signal_on_one_half_covers_the_whole_crossing() -> None:
    extra = [("x1", "w1", "c", "footway",
              {"crossed_road": "secondary", "osm_way_id": 7, "crossing": "traffic_signals"}),
             ("x2", "c", "w2", "footway", {"crossed_road": "secondary", "osm_way_id": 8})]  # fmt: skip
    edges, nodes = _net(extra)
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    assert _row(links, "x1").crossing_cost_s == 0 and _row(links, "x2").crossing_cost_s == 0


def test_a_zero_length_piece_costs_nothing_and_is_not_refused() -> None:
    # OpenStreetMap has occasional zero-length pieces: two nodes at one point.
    edges, nodes = _net(shapes={"by": LineString([(200, 0), (200, 0)])})
    links = build_walk_broach_links(edges, _profile(), nodes=nodes)
    assert _row(links, "by").generalized_cost_s == 0
    assert _row(links, "bc").generalized_cost_s > 0
