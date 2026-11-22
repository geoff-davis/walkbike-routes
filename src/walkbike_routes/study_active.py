"""One walking or cycling run to a destination: reach, route shares, flows and the improvement screen."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from walkbike_routes import config as settings
from walkbike_routes.analysis import calculate_accessibility, screens
from walkbike_routes.choice import bound_to_best, near_best_alternatives, split_routes
from walkbike_routes.costs import presets
from walkbike_routes.demand import decay
from walkbike_routes.destinations.virtual_nodes import access_point_node_map
from walkbike_routes.models import Mode, TravelDirection
from walkbike_routes.network.broach import BroachProfile
from walkbike_routes.network.broach_walk import (
    WalkBroachProfile,
)
from walkbike_routes.routing.flows import shared_route_flows
from walkbike_routes.routing.turns import place, sink
from walkbike_routes.study_context import CORE_MODES, MPS_PER_MPH, Context, summary, write
from walkbike_routes.study_entrances import access_points
from walkbike_routes.study_networks import ModeNetwork, mode_network, preset_links, turn_costs

log = logging.getLogger(__name__)


def active_run(
    context: Context,
    destination: settings.Destination,
    mode: settings.Mode,
    preset: presets.Preset,
    folder: Path,
    *,
    screen: bool,
    network: ModeNetwork | None = None,
) -> tuple[list[Path], list[dict[str, object]], pd.Series]:
    core_mode = CORE_MODES[mode]
    built = network or mode_network(context, mode, preset)
    links, graph, nodes, attachments, search = (
        built.links, built.graph, built.nodes, built.attachments, built.search
    )  # fmt: skip
    access = access_points(context, destination, nodes, links, core_mode)
    search_access = (
        access if search is graph else access.assign(node_id=access.node_id.astype(str).map(sink))
    )
    cap = context.study.time_caps_min[mode]
    speed = float(preset.parameters["speed_mph"]) * MPS_PER_MPH
    result = calculate_accessibility(
        search, context.origins, attachments, search_access,
        study_id=context.study.study, destination_id=destination.id, profile_id=preset.name,
        scenario_id=f"{mode}:{preset.name}", mode=core_mode,
        direction=TravelDirection.TO_DESTINATION, route_weight=preset.objective,
        thresholds_min=tuple(b for b in context.study.time_bands_min if b < cap) + (cap,),
        time_limit_min=cap, snap_speed_mps=speed,
    )  # fmt: skip
    curve_name = settings.decay_for(context.study, destination, mode)
    curve = decay.load(curve_name) if curve_name else None
    routes = decay.apply(result.routes, curve)
    weighted = routes.assign(demand_weight=routes.expected_weight)
    lengths = dict(zip(context.edges.physical_edge_id, context.edges.length_m, strict=True))
    choice = context.study.route_choice
    profile = preset.profile()
    if isinstance(profile, BroachProfile):  # the model sets its own route split
        choice = dataclasses.replace(
            choice,
            path_size_exponent=profile.coefficients["ln_path_size"],
            log_cost_exponent=-profile.distance_coefficient,
        )
    if isinstance(profile, WalkBroachProfile):  # linear in distance: a fixed logit scale
        choice = dataclasses.replace(
            choice,
            path_size_exponent=profile.coefficients["ln_path_size"],
            logit_scale_s=profile.logit_scale_s,
            log_cost_exponent=None,
        )
    eligible = routes[routes.route_reachable.astype(bool) & routes.within_time_limit.astype(bool)]
    alternatives = (
        near_best_alternatives(
            search,
            access_point_node_map(search_access),
            weight=preset.objective,
            settings=choice,
            nodes=set(eligible.attached_node_id.astype(str)),
            weight_per_second=speed if preset.objective == "physical_length_m" else 1.0,
            place=place if search is not graph else None,
        )  # fmt: skip
        if choice.routes > 1 and not eligible.empty
        else {}
    )
    if search is not graph:
        cost = (
            eligible.physical_length_m / speed
            if preset.objective == "physical_length_m"
            else eligible.generalized_cost_s
        )  # what the routes compete on
        best = cost.groupby(eligible.attached_node_id.astype(str)).min()
        costs = {str(node): float(cost) for node, cost in best.items()}
        alternatives = bound_to_best(alternatives, costs, settings=choice)
    shares = split_routes(
        weighted, alternatives, settings=choice, edge_lengths=lengths,
        length_speed_mps=speed if preset.objective == "physical_length_m" else None,
    )  # fmt: skip
    flows = shared_route_flows(shares, context.edges)
    frames: dict[str, pd.DataFrame] = {
        "routes": routes, "route_shares": shares, "flows": flows, "entrances": access
    }  # fmt: skip
    if screen and context.study.screen_candidates and preset.objective == "generalized_cost_s":
        # Weighted at today's route length.  Only homes within the limit today count.
        decayed = routes.assign(demand_weight=routes.demand_weight * routes.decay_factor)
        frames["screen"] = _screen(context, preset, links, decayed, access, core_mode, cap)
    files = write(folder, **frames)
    row = summary(context, destination, mode, preset.name, routes, shares, cap)
    row["decay_curve"] = curve_name or settings.NO_DECAY
    row["expected_weight"] = float(routes.expected_weight.sum())
    return files, [row], flows.set_index("physical_edge_id").potential_flow


def _screen(
    context: Context,
    preset: presets.Preset,
    links: gpd.GeoDataFrame,
    routes: pd.DataFrame,
    access: gpd.GeoDataFrame,
    mode: Mode,
    cap: float,
) -> gpd.GeoDataFrame:
    """Where each of the mode's treatments would save the most time, one table."""
    attached = routes[routes.attached_node_id.notna()]
    origins = pd.DataFrame(
        {
            "node_id": attached.attached_node_id.astype(str),
            "weight": attached.demand_weight.astype(float),
            "snap_travel_time_s": attached.snap_travel_time_s.astype(float),
            "within_limit": attached.within_time_limit.astype(bool),
        }
    )
    profile = preset.profile()
    tables = []
    for treatment in screens.treatments(mode):
        # The walking model as printed has no sidewalk term, so a sidewalk
        # has nothing to show unless the profile adds one.
        if treatment is screens.SIDEWALK and (
            isinstance(profile, WalkBroachProfile) and not profile.values_sidewalks
        ):
            continue
        edges = screens.treat_edges(context.edges, treatment)
        treated = preset_links(edges, context.nodes, preset)
        if treatment is screens.WALK_SIGNAL:
            wait = float(getattr(profile, "signal_wait_s", 0.0))
            # Broach's walking model has no signal-delay term: waits stay in time only.
            in_cost = not isinstance(profile, WalkBroachProfile)
            treated = screens.add_signal_wait(treated, context.edges, edges, wait, in_cost)
        graph, improved = screens.screen_graph(links, treated, preferred_cost=preset.objective)
        movements = turn_costs(context, preset, graph)
        tables.append(
            screens.improvement_screen(
                graph,
                sorted(set(access.node_id.astype(str))),
                origins,
                improved=improved,
                edges=context.edges,
                mode=mode,
                candidates=context.study.screen_candidates,
                time_limit_min=cap,
                movements=movements,
                treatment=treatment,
                junction=screens.junction_cost(movements, treatment),
            )  # fmt: skip
        )
    tables = [t for t in tables if not t.empty]
    if not tables:
        return gpd.GeoDataFrame(columns=screens.SCREEN_COLUMNS, geometry="geometry",
                                crs=context.edges.crs)  # fmt: skip
    return gpd.GeoDataFrame(pd.concat(tables, ignore_index=True), crs=context.edges.crs)
