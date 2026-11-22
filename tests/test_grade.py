"""Grades from node elevations, and walking speed on hills."""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from walkbike_routes import demo
from walkbike_routes.costs import presets
from walkbike_routes.network import (
    build_bicycle_links,
    build_pedestrian_links,
    prepare,
    routable_links_to_graph,
)
from walkbike_routes.network import osm_streets as o
from walkbike_routes.network.bicycle import BicycleProfile
from walkbike_routes.network.grade import GRADE_LIMIT, link_grades, parkin_factor, tobler_factor
from walkbike_routes.network.pedestrian import PedestrianProfile
from walkbike_routes.routing import measure_path

CRS = 26910


def test_grades_are_rise_over_length_where_the_ground_is_the_way() -> None:
    heights = pd.Series({"a": 10.0, "b": 15.0, "c": 10.2, "d": float("nan"), "e": 100.0})
    got = link_grades(
        u=pd.Series(["a", "b", "a", "a", "x", "a", "e"]),
        v=pd.Series(["b", "a", "c", "d", "y", "e", "x"]),
        length_m=pd.Series([100.0, 100.0, 5.0, 100.0, 100.0, 100.0, 100.0]),
        structure=pd.Series(["", "", "", "", "bridge", "", ""]),
        elevation_by_node=pd.concat([heights, pd.Series({"x": 50.0, "y": 50.0})]),
    )
    assert got[:2] == pytest.approx([0.05, -0.05])
    assert np.isnan(got[6])  # meets the bridge's end, whose ground height may be below it
    assert np.isnan(got[2])  # too short: elevation error would swamp it
    assert np.isnan(got[3])  # no elevation
    assert np.isnan(got[4])  # a bridge: the ground model is under it
    assert got[5] == pytest.approx(GRADE_LIMIT)  # a 90% "grade" is an elevation error


def test_tobler_is_one_on_the_flat_and_fastest_on_a_gentle_descent() -> None:
    assert tobler_factor(0.0) == pytest.approx(1.0)
    assert tobler_factor(-0.05) == pytest.approx(math.exp(0.175))
    assert tobler_factor(0.10) == pytest.approx(math.exp(-0.35))
    assert tobler_factor(-0.20) == pytest.approx(math.exp(-0.35))  # mirrors +10% about -5%
    assert tobler_factor(-0.10) == pytest.approx(1.0)
    assert tobler_factor(float("nan")) == 1.0


@pytest.mark.parametrize(
    ("tags", "kind"),
    [({"bridge": "yes"}, "bridge"), ({"bridge": "viaduct"}, "bridge"), ({"tunnel": "yes"}, "tunnel"),
     ({"covered": "yes", "layer": "-1"}, "tunnel"), ({"bridge": "no"}, ""), ({}, "")],
)  # fmt: skip
def test_bridges_and_tunnels_are_marked(tags: dict[str, str], kind: str) -> None:
    assert o.structure(tags) == kind


def _slope() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    nodes = gpd.GeoDataFrame(
        {"node_id": ["low", "high"], "control": ["none", "none"], "elevation_m": [0.0, 10.0]},
        geometry=[Point(0, 0), Point(100, 0)],
        crs=CRS,
    )
    links = gpd.GeoDataFrame(
        {"link_id": ["up"], "u": ["low"], "v": ["high"], "highway": ["footway"],
         "lanes": [None], "speed_mph": [None], "bike_facility": ["none"], "sidewalk": ["path"]},
        geometry=[LineString([(0, 0), (100, 0)])],
        crs=CRS,
    )  # fmt: skip
    return links, nodes


def test_walkers_climb_slower_and_only_when_the_preset_says_so() -> None:
    links, nodes = _slope()
    edges = prepare.physical_edges(links, nodes)
    assert edges.grade.tolist() == pytest.approx([0.10])
    hills = routable_links_to_graph(
        build_pedestrian_links(edges, PedestrianProfile("h", grade_speeds=True)), directed=True
    )
    flat = routable_links_to_graph(
        build_pedestrian_links(edges, PedestrianProfile("f")), directed=True
    )
    level = flat["low"]["high"]["movement_time_s"]
    assert flat["high"]["low"]["movement_time_s"] == pytest.approx(level)
    up = measure_path(hills, ["low", "high"]).movement_time_s
    down = measure_path(hills, ["high", "low"]).movement_time_s
    assert up == pytest.approx(level / math.exp(-0.35))
    assert down == pytest.approx(level)  # a 10% descent is as quick as the flat
    assert up > level


def test_the_hills_preset_slows_walks_to_the_demo_hilltop() -> None:
    town = demo.build()
    edges = prepare.physical_edges(town.links, town.nodes)
    assert edges.grade.notna().any() and edges.grade.abs().max() > 0.01
    times = {}
    for name in ("legacy", "legacy-hills"):
        profile = presets.load("walk", name).profile()
        assert isinstance(profile, PedestrianProfile)
        links = build_pedestrian_links(edges, profile)
        times[name] = links.movement_time_s.sum()
    assert times["legacy-hills"] != pytest.approx(times["legacy"])


def test_parkin_matches_the_papers_table_4_and_holds_beyond_its_data() -> None:
    # Table 4 mean speeds, m/s: -3% 6.72, 0% 6.01, +3% 4.81, +7% 3.21.
    for grade, mps in ((-0.03, 6.72), (0.0, 6.01), (0.03, 4.81), (0.07, 3.21)):
        assert parkin_factor(grade) * 6.01 == pytest.approx(mps, abs=0.006)
    assert parkin_factor(0.20) == pytest.approx(parkin_factor(0.0934))  # no zero speed at 15%
    assert parkin_factor(-0.20) == pytest.approx(parkin_factor(-0.0839))
    assert parkin_factor(float("nan")) == 1.0


def test_riders_climb_slower_and_descend_faster_only_when_the_preset_says_so() -> None:
    links, nodes = _slope()
    edges = prepare.physical_edges(links.assign(highway="residential", sidewalk="both"), nodes)
    times = {}
    for hills in (True, False):
        built = build_bicycle_links(edges, BicycleProfile("b", grade_speeds=hills))
        times[hills] = built.set_index("direction").movement_time_s.to_dict()
    level = times[False]["forward"]
    assert times[False]["reverse"] == pytest.approx(level)
    assert times[True]["forward"] == pytest.approx(level / parkin_factor(0.10))
    assert times[True]["reverse"] == pytest.approx(level / parkin_factor(-0.10))
    assert times[True]["reverse"] < level < times[True]["forward"]
