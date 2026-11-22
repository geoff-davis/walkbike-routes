"""A walking or cycling preset's network: its links, the graph searched, and the homes attached to it."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

import geopandas as gpd
import networkx as nx
import pandas as pd

from walkbike_routes import config as settings
from walkbike_routes.costs import presets
from walkbike_routes.demand import attach_origins_to_edges
from walkbike_routes.network import (
    build_bicycle_links,
    build_pedestrian_links,
    build_signal_wait_inventory,
    routable_links_to_graph,
)
from walkbike_routes.network.bicycle import BicycleProfile
from walkbike_routes.network.broach import BroachMovements, BroachProfile, build_broach_links
from walkbike_routes.network.broach_walk import (
    WalkBroachMovements,
    WalkBroachProfile,
    build_walk_broach_links,
)
from walkbike_routes.network.pedestrian import PedestrianProfile
from walkbike_routes.network.signals import SignalWaitInventory
from walkbike_routes.routing.turns import turn_graph
from walkbike_routes.study_context import CORE_MODES, Context, connected, signal_inventory

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModeNetwork:
    """A walk or bike preset's network with the homes attached: the same for every destination."""

    links: gpd.GeoDataFrame
    graph: nx.Graph[str]
    nodes: gpd.GeoDataFrame
    attachments: pd.DataFrame
    # The graph searched: ``graph`` itself, or its turn graph when the preset has turn costs.
    search: nx.Graph[str]


def mode_network(
    context: Context,
    mode: settings.Mode,
    preset: presets.Preset,
    like: ModeNetwork | None = None,
) -> ModeNetwork:
    """Build a walk or bike preset's network and attach ``context.origins`` to it.

    ``like`` is the same preset's network on the same streets, built for other
    homes (another destination's): its links and graphs are used again, and
    only the homes are attached afresh.
    """
    if like is not None:
        links, graph, nodes, search = like.links, like.graph, like.nodes, like.search
    else:
        links = preset_links(context.edges, context.nodes, preset, signal_inventory(context))
        graph = routable_links_to_graph(links, directed=True, preferred_cost=preset.objective)
        nodes = context.nodes[context.nodes.node_id.isin(set(graph.nodes))]
        search = _movement_graph(context, preset, graph)
    usable = context.edges[context.edges.physical_edge_id.isin(set(links.physical_edge_id))]
    attachments = attach_origins_to_edges(
        context.origins, connected(usable, nodes), nodes, mode=CORE_MODES[mode],
        maximum_snap_distance_m=None,
        attachment_policy="nearest street",
    )  # fmt: skip
    attachments = attachments.join(cost_per_m(links, preset.objective), on="attached_edge_id")
    return ModeNetwork(links, graph, nodes, attachments, search)


def cost_per_m(links: pd.DataFrame, cost: str) -> pd.DataFrame:
    """Each physical edge's cost per metre from its ``u`` (forward) and back.

    For starting a home from either end of its street; NaN where the mode
    may not go that way.  A link serving both directions counts for both.
    Costs that happen at a point (signal waits, crossings, turns, controls)
    are left out, so only the cost of moving along the street is spread over
    its length.
    """
    at_points = {  # an objective in metres (shortest) has no point costs
        "travel_time_s": ["crossing_wait_time_s"],
        "generalized_cost_s": ["crossing_wait_time_s", "crossing_cost_s", "turn_cost_s",
                               "control_cost_s", "transition_cost_s"],
    }.get(cost, [])  # fmt: skip
    along = pd.to_numeric(links[cost], errors="coerce")
    # Broach walking already leaves signal waits out of its cost, and its
    # control cost is the flat-speed correction, part of the street itself.
    flat_speed = (
        links.link_kind.astype(str).eq("pedestrian_broach_2016")
        if "link_kind" in links
        else pd.Series(False, index=links.index)
    )
    for column in at_points:
        if column in links:
            point = pd.to_numeric(links[column], errors="coerce").fillna(0.0)
            if cost == "generalized_cost_s" and column in (
                "crossing_wait_time_s",
                "control_cost_s",
            ):
                point = point.where(~flat_speed, 0.0)
            along = along - point
    metres = pd.to_numeric(links.physical_length_m, errors="coerce")
    per_m = along.clip(lower=0.0) / metres.where(metres.gt(0))
    direction = links.direction.astype(str)
    pid = links.physical_edge_id.astype(str)
    out = pd.DataFrame(
        {
            f"{way}_cost_per_m": per_m[direction.isin([way, "both"])].groupby(pid).min()
            for way in ("forward", "reverse")
        }
    )
    return out.rename(columns={"reverse_cost_per_m": "backward_cost_per_m"})


def turn_costs(
    context: Context, preset: presets.Preset, graph: nx.Graph[str]
) -> BroachMovements | WalkBroachMovements | None:
    """The preset's turn and crossing costs on ``graph``, if it has them."""
    profile = preset.profile()
    if not isinstance(profile, BroachProfile | WalkBroachProfile):
        return None
    directed = cast("nx.DiGraph[str]", graph)  # active-mode graphs are built directed
    signals = signal_inventory(context)
    if isinstance(profile, BroachProfile):
        return BroachMovements(
            directed, context.edges, context.nodes, profile, signal_wait_inventory=signals
        )
    return WalkBroachMovements(
        directed, context.edges, context.nodes, profile, signal_wait_inventory=signals
    )


def _movement_graph(
    context: Context, preset: presets.Preset, graph: nx.Graph[str]
) -> nx.Graph[str]:
    """The graph to search: with turn costs if the preset has them."""
    movements = turn_costs(context, preset, graph)
    if movements is None:
        return graph
    return turn_graph(cast("nx.DiGraph[str]", graph), movements)


def preset_links(
    edges: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    preset: presets.Preset,
    signals: SignalWaitInventory | None = None,
) -> gpd.GeoDataFrame:
    """The preset's routable links on ``edges``; ``signals`` are those streets' signals,
    found here when not given (changed streets, as the screen tries, need their own)."""
    profile = preset.profile()
    signals = signals or build_signal_wait_inventory(nodes, edges)
    if isinstance(profile, BroachProfile):
        return build_broach_links(edges, nodes, profile, signal_wait_inventory=signals)
    if isinstance(profile, WalkBroachProfile):
        return build_walk_broach_links(edges, profile, signal_wait_inventory=signals)
    if isinstance(profile, BicycleProfile):
        return build_bicycle_links(edges, profile, signal_wait_inventory=signals)
    assert isinstance(profile, PedestrianProfile)
    return build_pedestrian_links(edges, profile, signal_wait_inventory=signals)
