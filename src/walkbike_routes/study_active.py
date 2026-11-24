"""One walking or cycling run to a destination: reach, route shares, flows and the improvement screen."""

from __future__ import annotations

import dataclasses
import logging
import math
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd

from walkbike_routes import config as settings
from walkbike_routes.analysis import calculate_accessibility, screens
from walkbike_routes.analysis.accessibility import AccessibilityResult
from walkbike_routes.choice import bound_to_best, near_best_alternatives, split_routes
from walkbike_routes.choice.path_size import Alternative
from walkbike_routes.costs import presets
from walkbike_routes.demand import decay
from walkbike_routes.destinations.virtual_nodes import access_point_node_map
from walkbike_routes.models import Mode, TravelDirection
from walkbike_routes.network.broach import BroachProfile
from walkbike_routes.network.broach_walk import (
    WalkBroachProfile,
)
from walkbike_routes.routing import local
from walkbike_routes.routing.flows import shared_route_flows
from walkbike_routes.routing.turns import place, sink
from walkbike_routes.study_context import (
    CORE_MODES,
    MPS_PER_MPH,
    Context,
    limit_folder,
    summary,
    write,
)
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
    per_second = speed if preset.objective == "physical_length_m" else 1.0

    def route(on: nx.Graph[str]) -> tuple[AccessibilityResult, dict[str, tuple[Alternative, ...]]]:
        """The best route from each home, and the near-best ones from those within the limit."""
        found = calculate_accessibility(
            on, context.origins, attachments, search_access,
            study_id=context.study.study, destination_id=destination.id, profile_id=preset.name,
            scenario_id=f"{mode}:{preset.name}", mode=core_mode,
            direction=TravelDirection.TO_DESTINATION, route_weight=preset.objective,
            thresholds_min=tuple(b for b in context.study.time_bands_min if b < cap) + (cap,),
            time_limit_min=cap, snap_speed_mps=speed,
        )  # fmt: skip
        ok = found.routes[
            found.routes.route_reachable.astype(bool) & found.routes.within_time_limit.astype(bool)
        ]
        near_best = (
            near_best_alternatives(
                on,
                access_point_node_map(search_access),
                weight=preset.objective,
                settings=choice,
                nodes=set(ok.attached_node_id.astype(str)),
                weight_per_second=per_second,
                place=place if search is not graph else None,
            )  # fmt: skip
            if choice.routes > 1 and not ok.empty
            else {}
        )
        return found, near_best

    result, alternatives = _nearby(
        route, built, access, search_access, context.origins,
        weight=preset.objective, gap=choice.maximum_cost_gap_s * per_second,
        label=f"{destination.id} by {mode} ({preset.name})",
    )  # fmt: skip
    curve_name = settings.decay_for(context.study, destination, mode)
    curve = decay.load(curve_name) if curve_name else None
    routes = decay.apply(result.routes, curve)
    weighted = routes.assign(demand_weight=routes.expected_weight)
    eligible = routes[routes.route_reachable.astype(bool) & routes.within_time_limit.astype(bool)]
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
    for limit in (m for m in context.study.shorter_limits_min if m < cap):
        # The same routes, split again as if the limit were shorter: a route
        # over it is dropped and the next near-best one may take its place.
        within = result.routes.route_reachable.astype(bool) & (
            result.routes.total_travel_time_s.astype(float) <= limit * 60.0 + 1e-9
        )
        cut = decay.apply(
            result.routes.assign(within_time_limit=within, time_limit_min=limit), curve
        )
        cut_shares = split_routes(
            cut.assign(demand_weight=cut.expected_weight), alternatives, settings=choice,
            edge_lengths=lengths,
            length_speed_mps=speed if preset.objective == "physical_length_m" else None,
        )  # fmt: skip
        sub = limit_folder(folder, limit)
        sub.mkdir(exist_ok=True)
        cut_row = summary(context, destination, mode, preset.name, cut, cut_shares, limit)
        cut_row["decay_curve"] = curve_name or settings.NO_DECAY
        cut_row["expected_weight"] = float(cut.expected_weight.sum())
        pd.DataFrame([cut_row]).to_csv(sub / "summary.csv", index=False)
        files += [
            *write(sub, route_shares=cut_shares,
                   flows=shared_route_flows(cut_shares, context.edges)),
            sub / "summary.csv",
        ]  # fmt: skip
    return files, [row], flows.set_index("physical_edge_id").potential_flow


Routed = tuple[AccessibilityResult, dict[str, tuple[Alternative, ...]]]
START = 1.5  # the first circle: this many times the farthest home's distance from an entrance
TRIES = 3  # circles tried before the whole network


def _nearby(
    route: Callable[[nx.Graph[str]], Routed],
    built: ModeNetwork,
    access: gpd.GeoDataFrame,
    search_access: gpd.GeoDataFrame,
    origins: pd.DataFrame,
    *,
    weight: str,
    gap: float,
    label: str,
) -> Routed:
    """``route`` on the network near the destination when that is provably exact, else on all.

    See :mod:`walkbike_routes.routing.local`.  The circle starts from the
    farthest home and widens to the radius a failed check asks for.
    """
    search = built.search
    points = built.nodes.geometry
    xy = {
        str(n): (float(x), float(y))
        for n, x, y in zip(built.nodes.node_id, points.x, points.y, strict=True)
    }
    per_m = local.cost_per_metre(search, xy, weight)
    away = local.distances(xy, access.node_id.astype(str))
    ends = local.home_ends(built.attachments, origins.origin_id)
    radius = START * max((away.get(e, math.inf) for e in ends), default=0.0)
    reachable: set[str] | None = None
    for _ in range(TRIES):
        cut = local.near(search, away, per_m, radius) if math.isfinite(radius) else None
        if cut is None:
            break
        result, alternatives = route(cut.graph)
        ok, needed = local.exact_homes(cut, result.tree.cost_by_node, ends, reachable)
        if not ok and reachable is None and math.isinf(needed):
            targets = access_point_node_map(search_access).values()
            reachable = local.reaches(search, map(str, targets))
            ok, needed = local.exact_homes(cut, result.tree.cost_by_node, ends, reachable)
        if ok:
            best = {
                node: (found[0].physical_length_m if weight == "physical_length_m"
                       else found[0].generalized_cost_s)
                for node, found in alternatives.items() if found
            }  # fmt: skip
            missing = {n for n, found in alternatives.items() if not found}
            ok, needed = local.exact_alternatives(cut, best, gap)
            if ok and not missing:
                logging.info(
                    "%s: routed within %.1f km of the entrances (%d%% of the network)",
                    label, radius / 1000,
                    round(100 * cut.graph.number_of_nodes() / search.number_of_nodes()),
                )  # fmt: skip
                return result, alternatives
            if missing:
                needed = math.inf
        radius = needed
    return route(search)


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
