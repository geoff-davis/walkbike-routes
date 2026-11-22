"""Generic directed motor arcs and turn/control cost components."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import pandas as pd

from walkbike_routes.attributes import junction_tag, oneway_allows, physical_edge_id
from walkbike_routes.numeric import require_finite
from walkbike_routes.routing.costs import CostComponents


@dataclass(frozen=True)
class MotorPenaltyProfile:
    """Planning-level categorical movement and control delays."""

    profile_id: str
    signal_s: float
    stop_s: float
    right_turn_s: float
    left_turn_s: float
    u_turn_s: float

    def __post_init__(self) -> None:
        for name in ("signal_s", "stop_s", "right_turn_s", "left_turn_s", "u_turn_s"):
            require_finite(getattr(self, name), name)


def _first_unit_vector(coordinates: list[tuple[float, float]]) -> tuple[float, float]:
    x0, y0 = coordinates[0]
    for x1, y1 in coordinates[1:]:
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length > 1e-8:
            return dx / length, dy / length
    return 0.0, 0.0


def _edge_tangents(
    geometry: Any,
    forward: bool,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return travel-direction tangents at departure and arrival."""
    coordinates = list(geometry.coords)
    if not forward:
        coordinates.reverse()
    start = _first_unit_vector(coordinates)
    reverse_end = _first_unit_vector(list(reversed(coordinates)))
    end = (-reverse_end[0], -reverse_end[1])
    return start, end


def _source_id(edge: Any, physical_id: str) -> str:
    if hasattr(edge, "source_network_id"):
        return str(edge.source_network_id)
    if hasattr(edge, "osm_way_id"):
        return str(edge.osm_way_id)
    return physical_id


def _running_time(edge: Any) -> float:
    if hasattr(edge, "travel_time_s"):
        return float(str(edge.travel_time_s))
    return float(str(edge.edge_running_time_s))


def build_motor_arcs(
    edges: gpd.GeoDataFrame,
) -> tuple[pd.DataFrame, dict[str, list[int]], dict[str, list[int]]]:
    """Expand physical motor edges into directionally valid arcs."""
    rows: list[dict[str, object]] = []
    for raw_edge in edges.itertuples(index=False):
        edge: Any = raw_edge
        edge_id = physical_edge_id(edge)
        source_id = _source_id(edge, edge_id)
        running_time = _running_time(edge)
        require_finite(running_time, "motor running time")
        require_finite(float(edge.length_m), "motor length")  # zero: two nodes at one point
        for forward, u, v, suffix in (
            (True, str(edge.u), str(edge.v), "f"),
            (False, str(edge.v), str(edge.u), "r"),
        ):
            if not oneway_allows(getattr(edge, "oneway", ""), junction_tag(edge), forward):
                continue
            (start_dx, start_dy), (end_dx, end_dy) = _edge_tangents(edge.geometry, forward)
            rows.append(
                {
                    "arc_id": f"{edge_id}:{suffix}",
                    "edge_id": edge_id,
                    "osm_way_id": source_id,
                    "u": u,
                    "v": v,
                    "name": getattr(edge, "name", None),
                    "edge_time_s": running_time,
                    "length_m": float(str(edge.length_m)),
                    # dx/dy retain the arrival tangent used for the incoming
                    # side of a movement; start_* is the departure tangent.
                    "dx": end_dx,
                    "dy": end_dy,
                    "start_dx": start_dx,
                    "start_dy": start_dy,
                }
            )
    arcs = pd.DataFrame(rows).reset_index(names="arc_index")
    incoming: defaultdict[str, list[int]] = defaultdict(list)
    outgoing: defaultdict[str, list[int]] = defaultdict(list)
    for raw_arc in arcs.itertuples(index=False):
        arc: Any = raw_arc
        incoming[str(arc.v)].append(int(arc.arc_index))
        outgoing[str(arc.u)].append(int(arc.arc_index))
    return arcs, dict(incoming), dict(outgoing)


def movement_type(
    incoming: Mapping[str, object],
    outgoing: Mapping[str, object],
) -> str:
    """Classify a movement from normalized arc vectors."""
    return movement_type_from_values(
        incoming_u=str(incoming["u"]),
        outgoing_v=str(outgoing["v"]),
        incoming_dx=float(str(incoming["dx"])),
        incoming_dy=float(str(incoming["dy"])),
        outgoing_dx=float(str(outgoing.get("start_dx", outgoing["dx"]))),
        outgoing_dy=float(str(outgoing.get("start_dy", outgoing["dy"]))),
    )


SAME_STREET_CONTINUATION_MAX_ANGLE_DEG = 90.0


def movement_angle_deg(
    incoming_dx: float,
    incoming_dy: float,
    outgoing_dx: float,
    outgoing_dy: float,
) -> float:
    """Return the signed turn angle in degrees; positive values turn left."""
    cross = incoming_dx * outgoing_dy - incoming_dy * outgoing_dx
    dot = incoming_dx * outgoing_dx + incoming_dy * outgoing_dy
    return math.degrees(math.atan2(cross, dot))


def movement_type_from_values(
    *,
    incoming_u: str,
    outgoing_v: str,
    incoming_dx: float,
    incoming_dy: float,
    outgoing_dx: float,
    outgoing_dy: float,
) -> str:
    """Classify a movement without requiring temporary arc dictionaries."""
    if incoming_u == outgoing_v:
        return "u_turn"
    angle = movement_angle_deg(incoming_dx, incoming_dy, outgoing_dx, outgoing_dy)
    if abs(angle) <= 30:
        return "straight"
    if abs(angle) >= 150:
        return "u_turn"
    return "left_turn" if angle > 0 else "right_turn"


def _normalized_street_name(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).casefold().split())


def resolved_movement_type(
    *,
    incoming_u: str,
    outgoing_v: str,
    incoming_dx: float,
    incoming_dy: float,
    outgoing_dx: float,
    outgoing_dy: float,
    incoming_osm_way_id: object,
    outgoing_osm_way_id: object,
    incoming_name: object,
    outgoing_name: object,
) -> str:
    """Classify a movement, treating a named or same-way continuation as through travel.

    The continuation override applies only up to a right angle. A same-name
    movement that turns more sharply, such as a median crossover, keeps its
    geometric classification and its turn cost.
    """
    movement = movement_type_from_values(
        incoming_u=incoming_u,
        outgoing_v=outgoing_v,
        incoming_dx=incoming_dx,
        incoming_dy=incoming_dy,
        outgoing_dx=outgoing_dx,
        outgoing_dy=outgoing_dy,
    )
    if movement in {"u_turn", "straight"}:
        return movement
    angle = abs(movement_angle_deg(incoming_dx, incoming_dy, outgoing_dx, outgoing_dy))
    if angle > SAME_STREET_CONTINUATION_MAX_ANGLE_DEG + 1e-9:
        return movement
    same_way = str(incoming_osm_way_id) == str(outgoing_osm_way_id)
    incoming_street = _normalized_street_name(incoming_name)
    same_named_street = bool(incoming_street) and incoming_street == _normalized_street_name(
        outgoing_name
    )
    return "straight" if same_way or same_named_street else movement


def control_delay_for_transition(
    *,
    source_node: str,
    via_node: str,
    controls: Mapping[str, tuple[str, str]],
    penalties: MotorPenaltyProfile | Mapping[str, float],
) -> tuple[float, str | None]:
    """Charge a control once when a route enters its contiguous cluster footprint."""
    control = controls.get(via_node)
    if control is None:
        return 0.0, None
    control_type, cluster_id = control
    source_control = controls.get(source_node)
    inside_cluster = source_control is not None and source_control[1] == cluster_id
    if inside_cluster:
        return 0.0, control_type
    key = f"{control_type}_s"
    value = penalties[key] if isinstance(penalties, Mapping) else getattr(penalties, key)
    return float(value), control_type


def motor_transition_cost(
    incoming: Mapping[str, object],
    outgoing: Mapping[str, object],
    *,
    via_node: str,
    controls: Mapping[str, tuple[str, str]],
    penalties: MotorPenaltyProfile,
) -> tuple[CostComponents, str, str | None]:
    """Return auditable turn and control costs for one arc transition."""
    movement = resolved_movement_type(
        incoming_u=str(incoming["u"]),
        outgoing_v=str(outgoing["v"]),
        incoming_dx=float(str(incoming["dx"])),
        incoming_dy=float(str(incoming["dy"])),
        outgoing_dx=float(str(outgoing.get("start_dx", outgoing["dx"]))),
        outgoing_dy=float(str(outgoing.get("start_dy", outgoing["dy"]))),
        incoming_osm_way_id=incoming.get("osm_way_id"),
        outgoing_osm_way_id=outgoing.get("osm_way_id"),
        incoming_name=incoming.get("name"),
        outgoing_name=outgoing.get("name"),
    )
    turn_cost = float(getattr(penalties, f"{movement}_s", 0.0))
    control_cost, control_type = control_delay_for_transition(
        source_node=str(incoming["u"]),
        via_node=via_node,
        controls=controls,
        penalties=penalties,
    )
    return (
        CostComponents(turn_cost_s=turn_cost, control_cost_s=control_cost),
        movement,
        control_type,
    )
