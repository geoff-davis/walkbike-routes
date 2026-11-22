"""Canonical route measurement and origin tracing."""

from __future__ import annotations

import hashlib
import itertools
import math
from collections.abc import Mapping
from dataclasses import dataclass

import networkx as nx
import pandas as pd

from walkbike_routes.attributes import text_or_none
from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.models import Mode, TravelDirection
from walkbike_routes.numeric import require_finite
from walkbike_routes.routing.paths import PathTree

COMPONENT_FIELDS = (
    "movement_time_s",
    "crossing_wait_time_s",
    "stress_cost_s",
    "facility_cost_s",
    "crossing_cost_s",
    "turn_cost_s",
    "control_cost_s",
    "transition_cost_s",
)


@dataclass(frozen=True)
class PathMeasures:
    """Physical and generalized measurements for one graph path."""

    physical_length_m: float
    travel_time_s: float
    generalized_cost_s: float
    routable_link_ids: tuple[str, ...]
    physical_edge_ids: tuple[str, ...]
    cost_components_s: dict[str, float]

    @property
    def movement_time_s(self) -> float:
        """Return physical moving time represented by the route links."""
        return self.cost_components_s.get("movement_time_s", 0.0)

    @property
    def crossing_wait_time_s(self) -> float:
        """Return modeled physical waiting time at crossing/control events."""
        return self.cost_components_s.get("crossing_wait_time_s", 0.0)


def measure_path(graph: nx.Graph[str], path: list[str]) -> PathMeasures:
    """Measure a node path using canonical link attributes."""
    physical_length = 0.0
    travel_time = 0.0
    generalized_cost = 0.0
    routable_ids: list[str] = []
    physical_ids: list[str] = []
    components = dict.fromkeys(COMPONENT_FIELDS, 0.0)
    for u, v in itertools.pairwise(path):
        attrs = graph[u][v]
        if attrs.get("virtual"):  # a movement graph's link to a route's end
            continue
        link_id = str(attrs.get("routable_link_id", attrs.get("edge_id", f"{u}->{v}")))
        physical_id_raw = attrs.get("physical_edge_id", attrs.get("edge_id"))
        length = float(attrs.get("physical_length_m", attrs.get("length_m", 0.0)))
        if attrs.get("travel_time_s") is None:
            raise AccessibilityError(
                f"path link {link_id!r} has no travel_time_s; apply a speed policy first"
            )
        edge_time = float(attrs["travel_time_s"])
        edge_cost = float(attrs.get("generalized_cost_s", attrs.get("route_cost_m", edge_time)))
        if any(not math.isfinite(value) or value < 0 for value in (length, edge_time, edge_cost)):
            raise AccessibilityError(f"path link {link_id!r} has invalid measures")
        physical_length += length
        travel_time += edge_time
        generalized_cost += edge_cost
        routable_ids.append(link_id)
        if physical_id_raw is not None and not pd.isna(physical_id_raw):
            physical_ids.append(str(physical_id_raw))
        for field in COMPONENT_FIELDS:
            components[field] += float(attrs.get(field, 0.0))
    return PathMeasures(
        physical_length,
        travel_time,
        generalized_cost,
        tuple(routable_ids),
        tuple(physical_ids),
        components,
    )


def _as_bool(value: object) -> bool:
    """Read a boolean attachment flag, treating missing values as False."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return not (isinstance(value, float) and math.isnan(value)) and value != 0
    text = text_or_none(value)
    return text is not None and text.lower() in {"true", "1", "yes"}


def _as_float(value: object) -> float:
    """Read a numeric attachment field, treating missing values as NaN."""
    text = text_or_none(value)
    return float(text) if text is not None else math.nan


def _route_hash(link_ids: tuple[str, ...]) -> str:
    return hashlib.sha1(";".join(link_ids).encode(), usedforsecurity=False).hexdigest()


@dataclass(frozen=True)
class StreetPlace:
    """Where a home meets its street (see :func:`~walkbike_routes.demand.attach.attach_origins_to_edges`).

    ``forward_per_m`` and ``backward_per_m`` are the street's cost per metre
    from ``edge_u`` towards ``edge_v`` and back, in the route search's units;
    NaN where the mode may not go that way.
    """

    edge_u: str
    edge_v: str
    edge_distance_m: float
    to_u_m: float
    to_v_m: float
    forward_per_m: float
    backward_per_m: float


def best_end(
    cost_by_node: Mapping[str, float],
    place: StreetPlace,
    *,
    to_destination: bool,
) -> tuple[str | None, float]:
    """The end of a home's street to start from, and the distance to it.

    Each end costs its best route plus the stretch along the street to it,
    priced in the direction travelled: leaving home towards ``edge_v`` is
    forward, as is arriving home from ``edge_u``.  The distance to the street
    is the same either way, so it does not count in the choice.  A direction
    with no cost (NaN) is not allowed, as on a one-way street, unless the
    stretch is 0 m.  The cheaper allowed, reachable end wins, the nearer on
    a tie.  With none, the node is None: the home cannot get there.  The
    distance returned is to the street plus the stretch along it.
    """
    options = []
    for n, (node, along, heads_to_v) in enumerate(
        ((place.edge_u, place.to_u_m, False), (place.edge_v, place.to_v_m, True))
    ):
        forward = heads_to_v == to_destination
        rate = place.forward_per_m if forward else place.backward_per_m
        if along <= 0:
            rate = 0.0
        if node in cost_by_node and math.isfinite(along) and math.isfinite(rate):
            options.append((cost_by_node[node] + along * rate, along, n, node))
    if not options:
        return None, math.nan
    _, along, _, node = min(options)
    return node, place.edge_distance_m + along


def street_place(row: object) -> StreetPlace | None:
    """A home's :class:`StreetPlace` from its attachment row, if it has one."""

    def number(name: str) -> float:
        return _as_float(getattr(row, name, None))

    edge_u = text_or_none(getattr(row, "edge_u", None))
    edge_v = text_or_none(getattr(row, "edge_v", None))
    if edge_u is None or edge_v is None or not math.isfinite(number("edge_distance_m")):
        return None
    if not all(hasattr(row, f"{way}_cost_per_m") for way in ("forward", "backward")):
        raise AccessibilityError(
            "street attachments need forward_cost_per_m and backward_cost_per_m: the "
            "street's cost per metre each way, NaN where the mode may not go"
        )
    return StreetPlace(
        edge_u, edge_v, number("edge_distance_m"), number("to_u_m"), number("to_v_m"),
        number("forward_cost_per_m"), number("backward_cost_per_m"),
    )  # fmt: skip


def _start(
    cost_by_node: Mapping[str, float], row: object, *, to_destination: bool
) -> tuple[bool, str, float]:
    """Whether a home is attached, the node it starts from and its snap distance."""
    attached = _as_bool(getattr(row, "network_attached", False))
    node = text_or_none(getattr(row, "attached_node_id", None)) or ""
    snap = _as_float(getattr(row, "snap_distance_m", None))
    place = street_place(row) if attached else None
    if place is None:
        return attached, node, snap
    best, metres = best_end(cost_by_node, place, to_destination=to_destination)
    if best is None:  # no end it may reach: attached, but it cannot get there
        return True, "", snap
    return True, best, metres


def trace_routes(
    graph: nx.Graph[str],
    tree: PathTree,
    origins: pd.DataFrame,
    attachments: pd.DataFrame,
    *,
    study_id: str,
    destination_id: str,
    profile_id: str,
    scenario_id: str,
    mode: Mode,
    time_limit_min: float | None,
    snap_speed_mps: float,
) -> pd.DataFrame:
    """Trace every origin, retaining unattached and unreachable rows explicitly."""
    require_finite(snap_speed_mps, "snap_speed_mps", positive=True, error_type=AccessibilityError)
    if time_limit_min is not None:
        require_finite(
            time_limit_min, "time_limit_min", positive=True, error_type=AccessibilityError
        )
    selected_attachments = attachments[attachments["mode"].astype(str).eq(mode.value)].copy()
    if selected_attachments.duplicated("origin_id").any():
        raise AccessibilityError(f"attachments contain duplicate {mode.value} origin rows")
    to_destination = tree.direction is TravelDirection.TO_DESTINATION
    if not to_destination and any(data.get("virtual") for _, data in graph.nodes(data=True)):
        # On a movement graph a trip from the destination ends at a sink, not
        # at the home's node: every home would read as unreachable.
        raise AccessibilityError("routes from the destination cannot be traced on a turn graph")
    lookup = {
        str(row.origin_id): _start(tree.cost_by_node, row, to_destination=to_destination)
        for row in selected_attachments.itertuples(index=False)
    }
    rows: list[dict[str, object]] = []
    for origin in origins.itertuples(index=False):
        origin_id = str(origin.origin_id)
        attachment = lookup.get(origin_id)
        network_attached = attachment[0] if attachment is not None else False
        node_id = attachment[1] if attachment is not None and network_attached else ""
        snap_distance = attachment[2] if attachment is not None else math.nan
        path = tree.path_by_node.get(node_id) if network_attached else None
        route_reachable = path is not None
        measures = measure_path(graph, path) if path is not None else None
        snap_time = snap_distance / snap_speed_mps if route_reachable else math.nan
        total_time = measures.travel_time_s + snap_time if measures is not None else math.nan
        within_limit = bool(
            route_reachable
            and (time_limit_min is None or total_time <= time_limit_min * 60.0 + 1e-9)
        )
        routable_ids = measures.routable_link_ids if measures is not None else ()
        physical_ids = measures.physical_edge_ids if measures is not None else ()
        row: dict[str, object] = {
            "study_id": study_id,
            "destination_id": destination_id,
            "origin_id": origin_id,
            "profile_id": profile_id,
            "scenario_id": scenario_id,
            "mode": mode.value,
            "direction": tree.direction.value,
            "access_point_id": tree.access_point_by_node.get(node_id, pd.NA),
            "demand_weight": float(str(origin.demand_weight)),
            "weight_unit": str(origin.weight_unit),
            "attached_node_id": node_id if network_attached and node_id else pd.NA,
            "snap_distance_m": snap_distance,
            "network_attached": network_attached,
            "route_reachable": route_reachable,
            "within_time_limit": within_limit,
            "time_limit_min": time_limit_min,
            "physical_length_m": measures.physical_length_m if measures is not None else math.nan,
            "network_travel_time_s": measures.travel_time_s if measures is not None else math.nan,
            "snap_travel_time_s": snap_time,
            "total_travel_time_s": total_time,
            "generalized_cost_s": measures.generalized_cost_s if measures is not None else math.nan,
            "routable_link_ids": ";".join(routable_ids) if route_reachable else pd.NA,
            "physical_edge_ids": ";".join(physical_ids) if route_reachable else pd.NA,
            "route_path_sha1": _route_hash(routable_ids) if route_reachable else pd.NA,
            "not_observed_route": True,
        }
        for field in COMPONENT_FIELDS:
            row[field] = measures.cost_components_s[field] if measures is not None else math.nan
        rows.append(row)
    return pd.DataFrame(rows)
