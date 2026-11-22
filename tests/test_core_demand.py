"""Tests for generic origin-to-network attachment."""

from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point

from walkbike_routes.demand import attach_origins_to_edges
from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.models import Mode
from walkbike_routes.routing.routes import StreetPlace, best_end

CRS = "EPSG:26910"


def _street_and_driveway() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    # Elm runs a(-20,0)-b(260,0), drawn from b to a.  A driveway's end d is at
    # (100, 150), 110 m from the home at (100, 40); the home is 40 m from Elm,
    # whose corners are 126 and 165 m away.  The driveway leads off to e.
    nodes = gpd.GeoDataFrame(
        {"node_id": ["a", "b", "d", "e", "lonely"]},
        geometry=[Point(-20, 0), Point(260, 0), Point(100, 150), Point(400, 150), Point(100, 20)],
        crs=CRS,
    )
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["elm", "drive", "orphan"], "u": ["a", "d", "lonely"],
         "v": ["b", "e", "nowhere"]},
        geometry=[LineString([(260, 0), (-20, 0)]), LineString([(100, 150), (400, 150)]),
                  LineString([(100, 20), (100, 30)])],
        crs=CRS,
    )  # fmt: skip
    return edges, nodes


def test_homes_join_their_own_street_not_the_nearest_node() -> None:
    edges, nodes = _street_and_driveway()
    origins = gpd.GeoDataFrame(
        {"origin_id": ["home", "far", "east"]},
        geometry=[Point(100, 40), Point(100, 900), Point(250, 5)],
        crs=CRS,
    )
    new = attach_origins_to_edges(
        origins, edges, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=200,
        attachment_policy="t",
    ).set_index("origin_id")  # fmt: skip
    # Elm, 40 m away; its nearer corner is a, 120 m along: 160 m in all.
    assert new.loc["home", "attached_node_id"] == "a"
    assert new.loc["home", "attached_edge_id"] == "elm"
    assert new.loc["home", "edge_distance_m"] == pytest.approx(40)
    assert new.loc["home", "snap_distance_m"] == pytest.approx(160)
    assert new.loc["east", "attached_node_id"] == "b"  # 5 m off, 10 m from b
    assert new.loc["east", "snap_distance_m"] == pytest.approx(15)
    # The limit is on the distance to the street; "orphan" ends at no node, so is unusable.
    assert not bool(new.loc["far", "network_attached"])
    assert new.snap_distance_m.isna()["far"]  # not 0: it is not on the network


def test_edge_attachment_ties_and_limits() -> None:
    edges, nodes = _street_and_driveway()
    origins = gpd.GeoDataFrame(
        {"origin_id": ["middle", "on", "off"]},
        geometry=[Point(130, 10), Point(30, 0), Point(30, 1)],
        crs=CRS,
    )
    result = attach_origins_to_edges(
        origins, edges, nodes, mode=Mode.BICYCLE, maximum_snap_distance_m=0,
        attachment_policy="t",
    ).set_index("origin_id")  # fmt: skip
    assert result.network_attached.tolist() == [False, True, False]  # 0 m: on the line only
    assert result.loc["on", "snap_distance_m"] == pytest.approx(50)
    halfway = attach_origins_to_edges(
        origins.iloc[:1], edges, nodes, mode=Mode.BICYCLE, maximum_snap_distance_m=50,
        attachment_policy="t",
    ).iloc[0]  # fmt: skip
    assert halfway.attached_node_id == "b" and halfway.snap_distance_m == pytest.approx(140)
    with pytest.raises(AccessibilityError, match="missing attachment fields"):
        attach_origins_to_edges(
            origins, edges.drop(columns="u"), nodes, mode=Mode.BICYCLE,
            maximum_snap_distance_m=10, attachment_policy="t",
        )  # fmt: skip


def _place(fwd: float = 1.0, back: float = 1.0, to_u: float = 120.0) -> StreetPlace:
    # Elm, a to b, 280 m; the home is 40 m off it.
    return StreetPlace("a", "b", 40.0, to_u, 280.0 - to_u, fwd, back)


def test_attachment_gives_both_ends_and_the_cheaper_end_wins() -> None:
    edges, nodes = _street_and_driveway()
    origins = gpd.GeoDataFrame({"origin_id": ["home"]}, geometry=[Point(100, 40)], crs=CRS)
    row = attach_origins_to_edges(
        origins, edges, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=200,
        attachment_policy="t",
    ).iloc[0]  # fmt: skip
    assert (row.edge_u, row.edge_v, row.attached_node_id) == ("a", "b", "a")
    assert (row.to_u_m, row.to_v_m) == (pytest.approx(120), pytest.approx(160))
    # From a 300 s to go, from b 100 s; along Elm 1 s a metre: a 120 + 300, b 160 + 100.
    costs = {"a": 300.0, "b": 100.0}
    assert best_end(costs, _place(), to_destination=True) == ("b", 200.0)
    assert best_end({"a": 100.0, "b": 100.0}, _place(), to_destination=True) == ("a", 160.0)
    assert best_end({"a": 300.0}, _place(), to_destination=True) == ("a", 160.0)
    assert best_end({}, _place(), to_destination=True)[0] is None  # unreachable either way
    # One way, a to b only: leaving home it can only head for b; arriving, only from a.
    oneway = _place(back=math.nan)
    assert best_end({"a": 0.0, "b": 900.0}, oneway, to_destination=True) == ("b", 200.0)
    assert best_end(costs, oneway, to_destination=False) == ("a", 160.0)
    assert best_end({"a": 0.0}, oneway, to_destination=True)[0] is None  # no way out
    # At a itself, which way the street runs does not matter.
    assert best_end({"a": 0.0}, _place(back=math.nan, to_u=0.0), to_destination=True) == ("a", 40.0)
    # Uphill to b at 4 s a metre, 1 s back: b 160 x 4 + 100 = 740 > a 120 + 300.
    assert best_end(costs, _place(fwd=4.0), to_destination=True) == ("a", 160.0)
    # The 40 m to the street is the same either way and does not tip it.
    far_off = StreetPlace("a", "b", 100.0, 40.0, 60.0, 2.0, 1.0)
    assert best_end({"a": 100.0, "b": 0.0}, far_off, to_destination=True) == ("b", 160.0)


def test_loops_and_streets_drawn_in_pieces_measure_along_the_street() -> None:
    nodes = gpd.GeoDataFrame(
        {"node_id": ["u", "p", "q"]},
        geometry=[Point(0, 0), Point(0, 200), Point(100, 200)],
        crs=CRS,
    )
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["loop", "pieces"], "u": ["u", "p"], "v": ["u", "q"]},
        geometry=[
            LineString([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]),
            MultiLineString([[(0, 200), (50, 200)], [(100, 200), (50, 200)]]),
        ],
        crs=CRS,
    )  # fmt: skip
    origins = gpd.GeoDataFrame(
        {"origin_id": ["by_loop", "by_pieces"]},
        geometry=[Point(-5, 10), Point(40, 205)],
        crs=CRS,
    )
    result = attach_origins_to_edges(
        origins, edges, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=50,
        attachment_policy="t",
    ).set_index("origin_id")  # fmt: skip
    # The home is 390 m along the loop: 390 m back to its start, 10 m on to its end.
    assert result.loc["by_loop", "snap_distance_m"] == pytest.approx(5 + 10)
    assert result.to_u_m["by_loop"] == pytest.approx(390)
    assert result.to_v_m["by_loop"] == pytest.approx(10)
    # Drawn the other way round and one way: leaving home it must go back, 390 m.
    loop = StreetPlace("u", "u", 5.0, 390.0, 10.0, math.nan, 1.0)
    assert best_end({"u": 0.0}, loop, to_destination=True) == ("u", 395.0)
    # The two pieces join into one 100 m street: p is 40 m along, q 60 m.
    assert result.loc["by_pieces", "attached_node_id"] == "p"
    assert result.loc["by_pieces", "snap_distance_m"] == pytest.approx(5 + 40)
    assert result.loc["by_pieces", "to_v_m"] == pytest.approx(60)


def test_a_street_in_pieces_that_will_not_join_is_measured_from_where_the_home_meets_it() -> None:
    nodes = gpd.GeoDataFrame(
        {"node_id": ["u", "v"]}, geometry=[Point(0, 0), Point(110, 0)], crs=CRS
    )
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["gap"], "u": ["u"], "v": ["v"]},
        geometry=[MultiLineString([[(0, 0), (10, 0)], [(100, 0), (110, 0)]])],
        crs=CRS,
    )
    origins = gpd.GeoDataFrame({"origin_id": ["h"]}, geometry=[Point(105, 1)], crs=CRS)
    row = attach_origins_to_edges(
        origins, edges, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=10,
        attachment_policy="t",
    ).iloc[0]  # fmt: skip
    assert row.attached_node_id == "v" and row.to_v_m == pytest.approx(5)
    assert row.to_u_m == pytest.approx(105)


def test_car_rates_tell_a_loops_two_ways_apart() -> None:
    from walkbike_routes.network.cars import directional_cost_per_m

    loop = LineString([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
    edges = gpd.GeoDataFrame(
        {
            "physical_edge_id": ["two_way", "against"], "u": ["n", "m"], "v": ["n", "m"],
            "highway": ["residential", "residential"], "oneway": ["no", "-1"],
            "maxspeed": ["25 mph", "25 mph"], "length_m": [400.0, 400.0],
        },
        geometry=[loop, loop],
        crs=CRS,
    )  # fmt: skip
    rates = directional_cost_per_m(edges)
    assert rates.loc["two_way"].notna().all()
    assert rates.forward_cost_per_m.isna()["against"]
    assert rates.backward_cost_per_m.gt(0)["against"]


def test_a_loop_drawn_in_pieces_is_measured_from_its_node() -> None:
    nodes = gpd.GeoDataFrame({"node_id": ["n"]}, geometry=[Point(100, 100)], crs=CRS)
    pieces = [
        [(100, 100), (0, 100)],
        [(0, 100), (0, 0)],
        [(0, 0), (100, 0)],
        [(100, 0), (100, 100)],
    ]
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["ring"], "u": ["n"], "v": ["n"]},
        geometry=[MultiLineString(pieces)],
        crs=CRS,
    )
    origins = gpd.GeoDataFrame(
        {"origin_id": ["at_node", "on_side"]}, geometry=[Point(100, 100), Point(50, 101)], crs=CRS
    )
    result = attach_origins_to_edges(
        origins, edges, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=10,
        attachment_policy="t",
    ).set_index("origin_id")  # fmt: skip
    assert result.snap_distance_m["at_node"] == pytest.approx(0)
    # Drawn n -> (0,100) first: the home is 50 m on from n, 350 m on round to it.
    assert result.to_u_m["on_side"] == pytest.approx(50)
    assert result.to_v_m["on_side"] == pytest.approx(350)


def test_ends_at_one_point_and_zero_length_pieces() -> None:
    nodes = gpd.GeoDataFrame(
        {"node_id": ["u", "v", "p", "q"]},
        geometry=[Point(0, 0), Point(0, 0), Point(500, 500), Point(500, 500)],
        crs=CRS,
    )
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["back", "dot"], "u": ["u", "p"], "v": ["v", "q"]},
        geometry=[
            LineString([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]),
            LineString([(500, 500), (500, 500)]),
        ],
        crs=CRS,
    )
    origins = gpd.GeoDataFrame(
        {"origin_id": ["near_end", "by_dot"]}, geometry=[Point(-1, 10), Point(500, 501)], crs=CRS
    )
    result = attach_origins_to_edges(
        origins, edges, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=200,
        attachment_policy="t",
    ).set_index("origin_id")  # fmt: skip
    # 390 m from u at the start, 10 m from v at the end, though both are at (0, 0).
    assert result.to_u_m["near_end"] == pytest.approx(390)
    assert result.to_v_m["near_end"] == pytest.approx(10)
    assert result.attached_node_id["near_end"] == "v"
    assert result.snap_distance_m["by_dot"] == pytest.approx(1)
    assert result.to_u_m["by_dot"] == 0 and result.to_v_m["by_dot"] == 0


def test_a_ring_with_two_end_nodes_drawn_in_pieces() -> None:
    nodes = gpd.GeoDataFrame({"node_id": ["u", "v"]}, geometry=[Point(100, 100)] * 2, crs=CRS)
    pieces = [
        [(100, 100), (0, 100)],
        [(0, 100), (0, 0)],
        [(0, 0), (100, 0)],
        [(100, 0), (100, 100)],
    ]
    edges = gpd.GeoDataFrame(
        {"physical_edge_id": ["ring"], "u": ["u"], "v": ["v"]},
        geometry=[MultiLineString(pieces)],
        crs=CRS,
    )
    origins = gpd.GeoDataFrame({"origin_id": ["h"]}, geometry=[Point(50, 101)], crs=CRS)
    row = attach_origins_to_edges(
        origins, edges, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=10,
        attachment_policy="t",
    ).iloc[0]  # fmt: skip
    assert row.to_u_m == pytest.approx(50) and row.to_v_m == pytest.approx(350)
    assert row.attached_node_id == "u"


def test_costs_per_metre_leave_out_waits_at_points() -> None:
    from walkbike_routes.study_networks import cost_per_m as _cost_per_m

    links = pd.DataFrame(
        {
            "physical_edge_id": ["e", "e"], "direction": ["forward", "reverse"],
            "physical_length_m": [100.0, 100.0], "travel_time_s": [160.0, 100.0],
            "crossing_wait_time_s": [60.0, 0.0], "generalized_cost_s": [180.0, 100.0],
            "crossing_cost_s": [0.0, 0.0], "turn_cost_s": [0.0, 0.0],
            "control_cost_s": [0.0, 0.0], "transition_cost_s": [0.0, 0.0],
        }
    )  # fmt: skip
    # A 60 s signal wait at v is not spread along the street.
    timed = _cost_per_m(links, "travel_time_s")
    assert timed.forward_cost_per_m["e"] == pytest.approx(1.0)
    weighed = _cost_per_m(links, "generalized_cost_s")
    assert weighed.forward_cost_per_m["e"] == pytest.approx(1.2)
    assert weighed.backward_cost_per_m["e"] == pytest.approx(1.0)
    # Shortest-distance presets rank by metres: nothing is taken off.
    measured = _cost_per_m(links, "physical_length_m")
    assert measured.forward_cost_per_m["e"] == pytest.approx(1.0)


def test_broach_walking_keeps_its_flat_speed_cost_per_metre() -> None:
    from walkbike_routes.study_networks import cost_per_m as _cost_per_m

    # Uphill forward, downhill back: travel time differs, but the Broach cost
    # is 100 s each way, and its control cost is the hill correction.
    links = pd.DataFrame(
        {
            "physical_edge_id": ["e", "e"], "direction": ["forward", "reverse"],
            "link_kind": ["pedestrian_broach_2016"] * 2,
            "physical_length_m": [100.0, 100.0], "travel_time_s": [119.0, 84.0],
            "crossing_wait_time_s": [0.0, 0.0], "generalized_cost_s": [100.0, 100.0],
            "crossing_cost_s": [0.0, 0.0], "turn_cost_s": [0.0, 0.0],
            "control_cost_s": [-19.0, 16.0], "transition_cost_s": [0.0, 0.0],
        }
    )  # fmt: skip
    rates = _cost_per_m(links, "generalized_cost_s")
    assert rates.forward_cost_per_m["e"] == pytest.approx(1.0)
    assert rates.backward_cost_per_m["e"] == pytest.approx(1.0)


def test_no_homes_gives_an_empty_table_with_its_columns() -> None:
    edges, nodes = _street_and_driveway()
    none = gpd.GeoDataFrame({"origin_id": []}, geometry=[], crs=CRS)
    out = attach_origins_to_edges(
        none, edges, nodes, mode=Mode.PEDESTRIAN, maximum_snap_distance_m=200,
        attachment_policy="t",
    )  # fmt: skip
    assert out.empty and {"origin_id", "mode", "attached_node_id", "to_v_m"} <= set(out.columns)
