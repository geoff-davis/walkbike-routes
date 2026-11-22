"""A directed car network with turn and intersection delays, and routes over it.

Cars use the physical edges they are allowed on, in the directions one-way
rules allow.  Running time is the edge length at 80% of the posted speed, or
of a default speed for the road class when none is posted.  Turns and
controls add the delays of a ``motor_delays`` preset at each node: a signal
or stop sign is charged once per intersection, so nearby signal nodes (the
two carriageways of a divided road, say) are grouped within 35 m.

Stop signs are charged on every movement through their node.  OpenStreetMap
rarely says which approaches a stop sign controls, so a two-way stop also
delays the through street; a study can drop such stops from its nodes.

Defaults are hand-set planning values, not estimated from observed delays.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import geopandas as gpd
import networkx as nx
import pandas as pd

from walkbike_routes.network.motor import (
    MotorPenaltyProfile,
    build_motor_arcs,
    motor_transition_cost,
)
from walkbike_routes.routing.routes import best_end, street_place
from walkbike_routes.routing.transitions import transition_arrival_paths, transition_departure_paths

MPS_PER_MPH = 0.44704
RUNNING_SPEED_FACTOR = 0.80
CONTROL_CLUSTER_M = 35.0
DEFAULT_SPEED_MPH = {
    "motorway": 65.0, "motorway_link": 35.0, "trunk": 35.0, "trunk_link": 25.0,
    "primary": 35.0, "primary_link": 25.0, "secondary": 35.0, "secondary_link": 25.0,
    "tertiary": 25.0, "tertiary_link": 20.0, "unclassified": 25.0, "residential": 25.0,
    "living_street": 10.0, "service": 10.0, "road": 20.0,
}  # fmt: skip
FALLBACK_SPEED_MPH = 20.0
CONTROL_TYPES = {"traffic_signals": "signal", "signal": "signal", "stop": "stop"}


@dataclass(frozen=True)
class CarNetwork:
    """The directed graph, its node controls and the delays charged at nodes."""

    graph: nx.DiGraph[str]
    controls: dict[str, tuple[str, str]]
    penalties: MotorPenaltyProfile

    def transition_s(self, u: str, v: str, w: str) -> float:
        """Turn and control delay moving from edge ``u-v`` onto ``v-w``."""
        costs, _, _ = motor_transition_cost(
            self.graph[u][v], self.graph[v][w], via_node=v, controls=self.controls,
            penalties=self.penalties,
        )  # fmt: skip
        return float(costs.generalized_cost_s)


def car_edges(edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The edges cars may use, with their running time."""
    allowed = (
        edges.car_allowed.astype(bool)
        if "car_allowed" in edges
        else edges.highway.isin(DEFAULT_SPEED_MPH)
    )
    cars = edges[allowed].copy()
    posted = pd.to_numeric(cars.speed_mph, errors="coerce") if "speed_mph" in cars else None
    default = cars.highway.astype(str).map(DEFAULT_SPEED_MPH).fillna(FALLBACK_SPEED_MPH)
    speed = default if posted is None else posted.where(posted.gt(0), default)
    cars["car_speed_mph"] = speed.astype(float)
    cars["car_speed_source"] = (
        "default for road class"
        if posted is None
        else posted.gt(0).map({True: "posted", False: "default for road class"})
    )
    cars["travel_time_s"] = cars.length_m / (
        cars.car_speed_mph * MPS_PER_MPH * RUNNING_SPEED_FACTOR
    )
    return gpd.GeoDataFrame(cars, geometry="geometry", crs=edges.crs)


def control_clusters(nodes: gpd.GeoDataFrame, used: set[str]) -> dict[str, tuple[str, str]]:
    """Each controlled node's type and intersection; signals within 35 m share one."""
    kind = (
        nodes.highway.astype(str).map(CONTROL_TYPES)
        if "highway" in nodes
        else nodes.control.astype(str).map(CONTROL_TYPES)
    )
    controlled = nodes[kind.notna() & nodes.node_id.astype(str).isin(used)].copy()
    controlled["kind"] = kind[controlled.index]
    controlled["node_id"] = controlled.node_id.astype(str)
    parent = {n: n for n in controlled.node_id}

    def root(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    signals = controlled[controlled.kind.eq("signal")]
    if len(signals) > 1:
        near = gpd.sjoin(signals, signals, predicate="dwithin", distance=CONTROL_CLUSTER_M)
        for a, b in zip(near.node_id_left, near.node_id_right, strict=True):
            first, second = root(str(a)), root(str(b))
            if first != second:
                parent[max(first, second)] = min(first, second)
    return {n: (str(k), root(n)) for n, k in zip(controlled.node_id, controlled.kind, strict=True)}


def build(
    edges: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame, penalties: MotorPenaltyProfile
) -> CarNetwork:
    """The car network for a preset's delays."""
    cars = car_edges(edges)
    arcs, _, _ = build_motor_arcs(cars)
    graph: nx.DiGraph[str] = nx.DiGraph()
    for arc in arcs.to_dict("records"):
        u, v = str(arc["u"]), str(arc["v"])
        if graph.has_edge(u, v) and graph[u][v]["edge_time_s"] <= arc["edge_time_s"]:
            continue  # of parallel streets, keep the faster
        graph.add_edge(str(arc["u"]), str(arc["v"]), **{str(k): v for k, v in arc.items()})
    return CarNetwork(graph, control_clusters(nodes, set(graph.nodes)), penalties)


def directional_cost_per_m(edges: gpd.GeoDataFrame) -> pd.DataFrame:
    """Each car edge's driving time per metre from its ``u`` (forward) and back.

    From every drivable edge's arcs, before parallel streets are merged in
    the graph, so a home on the slower of two can still be costed.  NaN
    where cars may not go that way.
    """
    cars = car_edges(edges)
    arcs, _, _ = build_motor_arcs(cars)
    rows = [  # arc ids end ":f" along the edge from u, ":r" back (loops too)
        (str(a["edge_id"]), "forward" if str(a["arc_id"]).endswith(":f") else "backward",
         float(a["edge_time_s"]) / float(a["length_m"]))
        for a in arcs.to_dict("records") if float(a["length_m"]) > 0
    ]  # fmt: skip
    frame = pd.DataFrame(rows, columns=["physical_edge_id", "way", "per_m"])
    return (
        frame.pivot_table(index="physical_edge_id", columns="way", values="per_m", aggfunc="min")
        .reindex(columns=["forward", "backward"])
        .rename(columns=lambda c: f"{c}_cost_per_m")
    )


def _edge_time(_u: str, _v: str, attrs: Mapping[str, Any]) -> float:
    return float(attrs["edge_time_s"])


def routes(
    network: CarNetwork,
    attachments: pd.DataFrame,
    gate_nodes: Iterable[str],
    *,
    to_destination: bool,
    snap_speed_mps: float,
) -> pd.DataFrame:
    """Fastest turn-aware route for each attached origin, to or from the gates.

    ``attachments`` has ``origin_id``, ``attached_node_id``, ``snap_distance_m``
    and ``demand_weight``.  Origins the network cannot reach are kept with
    ``route_reachable`` false.
    """
    gates = [g for g in gate_nodes if g in network.graph]
    attached = attachments[attachments.attached_node_id.notna()]
    origin_nodes = set(attached.attached_node_id.astype(str))
    for end in ("edge_u", "edge_v"):  # a home may start from either end of its street
        if end in attached:
            origin_nodes |= set(attached[end].dropna().astype(str))
    if gates and origin_nodes:
        search = transition_arrival_paths if to_destination else transition_departure_paths
        cost, paths = search(network.graph, gates, origin_nodes, _edge_time, network.transition_s)
    else:
        cost, paths = {}, {}
    rows: list[dict[str, object]] = []
    for origin in attachments.itertuples(index=False):
        node = None if pd.isna(cast(Any, origin.attached_node_id)) else str(origin.attached_node_id)
        snap_m = float(str(origin.snap_distance_m))
        place = street_place(origin) if node is not None else None
        if place is not None:
            node, snap_m = best_end(cost, place, to_destination=to_destination)
        path = paths.get(node) if node is not None else None
        snap_s = snap_m / snap_speed_mps if path is not None else math.nan
        edge_ids: list[str] = []
        length = 0.0
        if path is not None:
            for u, v in zip(path, path[1:], strict=False):
                attrs = network.graph[u][v]
                edge_ids.append(str(attrs["edge_id"]))
                length += float(attrs["length_m"])
        rows.append(
            {
                "origin_id": str(origin.origin_id),
                "direction": "to_destination" if to_destination else "from_destination",
                "demand_weight": float(str(origin.demand_weight)),
                "attached_node_id": node,
                "gate_node_id": (path[-1] if to_destination else path[0]) if path else None,
                "route_reachable": path is not None,
                "physical_length_m": length if path is not None else math.nan,
                "network_travel_time_s": cost.get(node, math.nan) if node else math.nan,
                "snap_travel_time_s": snap_s,
                "total_travel_time_s": (cost[node] + snap_s)
                if path is not None and node
                else math.nan,
                "physical_edge_ids": ";".join(edge_ids),
                "not_observed_route": True,
            }
        )
    return pd.DataFrame(rows)
