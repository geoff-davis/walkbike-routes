"""One driving run to a destination: the fastest turn-aware routes there and back, and their flows."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from walkbike_routes import config as settings
from walkbike_routes.demand import attach_origins_to_edges
from walkbike_routes.models import Mode
from walkbike_routes.network import (
    cars,
)
from walkbike_routes.network.motor import MotorPenaltyProfile
from walkbike_routes.routing.flows import shared_route_flows
from walkbike_routes.study_context import (
    CAR_SNAP_SPEED_MPH,
    MPS_PER_MPH,
    Context,
    connected,
    summary,
    write,
)
from walkbike_routes.study_entrances import car_gates

log = logging.getLogger(__name__)


def car_network(context: Context, profile: MotorPenaltyProfile) -> cars.CarNetwork:
    """The streets open to cars with the profile's delays: the same for every destination."""
    network = cars.build(context.edges, context.nodes, profile)
    if network.graph.number_of_nodes() == 0:
        raise settings.ConfigError("car: no street in the study area is open to cars")
    return network


def car_run(
    context: Context,
    destination: settings.Destination,
    name: str,
    profile: MotorPenaltyProfile,
    folder: Path,
    network: cars.CarNetwork | None = None,
) -> tuple[list[Path], list[dict[str, object]], pd.Series]:
    """``network`` is :func:`car_network` for this profile, built here when not given."""
    network = network or car_network(context, profile)
    nodes = context.nodes[context.nodes.node_id.isin(set(network.graph.nodes))]
    driven = cars.car_edges(context.edges)
    attachments = attach_origins_to_edges(
        context.origins, connected(driven, nodes), nodes, mode=Mode.CAR,
        maximum_snap_distance_m=None, attachment_policy="nearest street",
    ).join(
        cars.directional_cost_per_m(context.edges), on="attached_edge_id"
    ).merge(context.origins[["origin_id", "demand_weight"]], on="origin_id")  # fmt: skip
    gates = car_gates(context, destination, nodes)
    snap = CAR_SNAP_SPEED_MPH * MPS_PER_MPH
    there = cars.routes(network, attachments, gates, to_destination=True, snap_speed_mps=snap)
    back = cars.routes(network, attachments, gates, to_destination=False, snap_speed_mps=snap)
    cap = context.study.time_caps_min.get("car")
    trips = pd.concat([there, back], ignore_index=True)
    trips["within_time_limit"] = trips.route_reachable & (
        trips.total_travel_time_s.le(cap * 60.0 + 1e-9) if cap else True
    )
    used = trips[trips.within_time_limit].assign(assigned_weight=lambda t: t.demand_weight)
    flow = {
        direction: shared_route_flows(used[used.direction.eq(direction)], context.edges)
        .set_index("physical_edge_id")
        .potential_flow
        for direction in ("to_destination", "from_destination")
    }
    flows = shared_route_flows(used, context.edges)
    ids = flows.physical_edge_id.astype(str)
    flows["flow_to_destination"] = ids.map(flow["to_destination"]).to_numpy()
    flows["flow_from_destination"] = ids.map(flow["from_destination"]).to_numpy()
    entrances = nodes[nodes.node_id.isin(gates)][["node_id", "geometry"]]
    files = write(folder, routes=trips, flows=flows, entrances=entrances)
    to_rows = trips[trips.direction.eq("to_destination")]
    row = summary(context, destination, "car", name, to_rows, None, cap)
    return files, [row], flows.set_index("physical_edge_id").potential_flow
