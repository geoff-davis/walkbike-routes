"""Car routes with turn and control delays, and the overlap index."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from walkbike_routes.analysis.overlap import mode_tiers, overlap, positive_percentile
from walkbike_routes.network import cars
from walkbike_routes.network.motor import MotorPenaltyProfile

CRS = 26910
FREE = MotorPenaltyProfile("free", 0, 0, 0, 0, 0)
DELAYS = MotorPenaltyProfile("delays", signal_s=30, stop_s=10, right_turn_s=3,
                             left_turn_s=60, u_turn_s=90)  # fmt: skip

# A square block, 100 m sides, plus a cul-de-sac:  C--D
#                                                   |  |
#                                          E-------A--B
SPOTS = {"A": (0, 0), "B": (100, 0), "C": (0, 100), "D": (100, 100), "E": (-100, 0)}


def _edges(rows: list[tuple[str, str, str, dict[str, object]]]) -> gpd.GeoDataFrame:
    records = []
    for edge_id, u, v, extra in rows:
        line = LineString([SPOTS[u], SPOTS[v]])
        records.append(
            {"physical_edge_id": edge_id, "u": u, "v": v, "length_m": line.length,
             "highway": "residential", "car_allowed": True, "speed_mph": None, "oneway": "no",
             "name": edge_id, "geometry": line, **extra}
        )  # fmt: skip
    return gpd.GeoDataFrame(records, geometry="geometry", crs=CRS)


def _nodes(controls: dict[str, str] | None = None) -> gpd.GeoDataFrame:
    controls = controls or {}
    return gpd.GeoDataFrame(
        {"node_id": list(SPOTS), "highway": [controls.get(n, "") for n in SPOTS]},
        geometry=[Point(xy) for xy in SPOTS.values()],
        crs=CRS,
    )


def _attachments(*nodes: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"origin_id": [f"o{n}" for n in nodes], "attached_node_id": list(nodes),
         "snap_distance_m": 0.0, "demand_weight": 1.0}
    )  # fmt: skip


SQUARE: list[tuple[str, str, str, dict[str, object]]] = [
    ("AB", "A", "B", {}), ("BD", "B", "D", {}), ("DC", "D", "C", {}), ("CA", "C", "A", {}),
    ("EA", "E", "A", {}),
]  # fmt: skip


def test_running_time_uses_the_posted_speed_or_the_class_default() -> None:
    edges = _edges([("AB", "A", "B", {"speed_mph": 50}), ("BD", "B", "D", {"highway": "service"}),
                    ("DC", "D", "C", {"highway": "footway", "car_allowed": False})])  # fmt: skip
    timed = cars.car_edges(edges).set_index("physical_edge_id")
    assert list(timed.index) == ["AB", "BD"]
    assert timed.travel_time_s["AB"] == pytest.approx(100 / (50 * 0.44704 * 0.8))
    assert timed.travel_time_s["BD"] == pytest.approx(100 / (10 * 0.44704 * 0.8))
    assert timed.car_speed_source.tolist() == ["posted", "default for road class"]


def test_one_way_streets_make_the_trip_there_and_back_differ() -> None:
    yes: dict[str, object] = {"oneway": "yes"}
    one_way = [(e, u, v, yes if e in {"AB", "BD", "DC", "CA"} else x)
               for e, u, v, x in SQUARE]  # fmt: skip
    network = cars.build(_edges(one_way), _nodes(), FREE)
    there = cars.routes(network, _attachments("B"), ["C"], to_destination=True, snap_speed_mps=4)
    back = cars.routes(network, _attachments("B"), ["C"], to_destination=False, snap_speed_mps=4)
    assert there.physical_edge_ids.iloc[0] == "BD;DC"
    assert back.physical_edge_ids.iloc[0] == "CA;AB"
    assert there.gate_node_id.iloc[0] == back.gate_node_id.iloc[0] == "C"


def test_a_left_turn_delay_can_send_a_car_the_long_way_round() -> None:
    # From E to D: A-B-D turns left at B; A-C-D turns left at A... both turn
    # left once, so make the B corner a signal: the route avoids it.
    network = cars.build(_edges(SQUARE), _nodes({"B": "traffic_signals"}), DELAYS)
    there = cars.routes(network, _attachments("E"), ["D"], to_destination=True, snap_speed_mps=4)
    assert there.physical_edge_ids.iloc[0] == "EA;CA;DC"
    free = cars.build(_edges(SQUARE), _nodes(), FREE)
    plain = cars.routes(free, _attachments("E"), ["D"], to_destination=True, snap_speed_mps=4)
    assert plain.network_travel_time_s.iloc[0] < there.network_travel_time_s.iloc[0]


def test_unreachable_and_unattached_origins_are_kept() -> None:
    network = cars.build(_edges(SQUARE[:1]), _nodes(), FREE)
    frame = pd.concat([_attachments("A", "E"), _attachments("B").assign(attached_node_id=None)])
    result = cars.routes(network, frame, ["B"], to_destination=True, snap_speed_mps=4)
    assert result.route_reachable.tolist() == [True, False, False]
    assert (
        cars.routes(
            network, frame, ["X"], to_destination=True, snap_speed_mps=4
        ).route_reachable.sum()
        == 0
    )


def test_nearby_signals_are_one_intersection() -> None:
    nodes = gpd.GeoDataFrame(
        {"node_id": ["s1", "s2", "s3", "t"], "highway": ["traffic_signals"] * 3 + ["stop"]},
        geometry=[Point(0, 0), Point(20, 0), Point(500, 0), Point(30, 0)],
        crs=CRS,
    )
    clusters = cars.control_clusters(nodes, {"s1", "s2", "s3", "t"})
    assert clusters["s1"] == clusters["s2"] == ("signal", "s1")
    assert clusters["s3"] == ("signal", "s3") and clusters["t"] == ("stop", "t")
    assert "s3" not in cars.control_clusters(nodes, {"s1"})


def test_overlap_ranks_streets_where_car_and_active_flows_meet() -> None:
    edges = _edges(SQUARE)
    car = pd.Series({"AB": 100.0, "BD": 10.0, "DC": 50.0})
    walk = pd.Series({"AB": 5.0, "CA": 20.0})
    bike = pd.Series({"BD": 30.0, "DC": 1.0})
    layer = overlap(edges, car, {"walk": walk, "bike": bike}).set_index("physical_edge_id")
    # Car percentiles: BD 1/3, DC 2/3, AB 1.  Active: max of walk (AB .5, CA 1) and bike (DC .5, BD 1).
    assert layer.overlap_index["AB"] == pytest.approx((1.0 * 0.5) ** 0.5)
    assert layer.overlap_index["BD"] == pytest.approx((1 / 3 * 1.0) ** 0.5)
    assert layer.overlap_index["CA"] == 0.0 and layer.overlap_index["EA"] == 0.0
    assert layer.overlap_tier["AB"] == "top 1%" and layer.overlap_tier["CA"] == ""
    assert layer.not_observed_flow_or_risk.all()
    with pytest.raises(ValueError, match="walking or cycling"):
        overlap(edges, car, {})


def test_each_mode_view_ranks_the_overlap_from_its_own_trips() -> None:
    edges = _edges(SQUARE)
    car = pd.Series({"AB": 100.0, "BD": 10.0, "DC": 50.0})
    walk = pd.Series({"AB": 5.0, "CA": 20.0})
    bike = pd.Series({"BD": 1.0, "DC": 40.0})
    layer = overlap(edges, car, {"walk": walk, "bike": bike}).set_index("physical_edge_id")
    walking, cycling = mode_tiers(layer, "walk"), mode_tiers(layer, "bike")
    # Walking meets cars only on AB.  Cycling meets them on BD and DC, with
    # DC higher (car 2/3, bike 1 against car 1/3, bike 1/2); AB has no bikes.
    assert walking[walking.ne("")].to_dict() == {"AB": "top 1%"}
    assert cycling[cycling.ne("")].to_dict() == {"DC": "top 1%"}
    # The combined tiers, from the higher of the two, are what driving shows.
    assert mode_tiers(layer, "car").tolist() == layer.overlap_tier.tolist()
    walk_only = overlap(edges, car, {"walk": walk})
    assert mode_tiers(walk_only, "bike").eq("").all()


def test_percentiles_leave_zeros_at_zero() -> None:
    ranks = positive_percentile(pd.Series([0.0, 4.0, None, 2.0, -1.0]))
    assert ranks.tolist() == [0.0, 1.0, 0.0, 0.5, 0.0]


@pytest.mark.parametrize("order", [1, -1])
def test_the_faster_of_two_parallel_streets_is_kept(order: int) -> None:
    rows: list[tuple[str, str, str, dict[str, object]]] = [
        ("fast", "A", "B", {"speed_mph": 50}),
        ("slow", "A", "B", {"speed_mph": 10}),
    ]
    if order < 0:
        rows.reverse()
    network = cars.build(_edges(rows), _nodes(), FREE)
    there = cars.routes(network, _attachments("A"), ["B"], to_destination=True, snap_speed_mps=4)
    assert there.physical_edge_ids.iloc[0] == "fast"
