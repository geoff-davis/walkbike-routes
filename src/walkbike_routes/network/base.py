"""Construction of NetworkX graphs from canonical routable links."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

from walkbike_routes.models import LinkDirection
from walkbike_routes.routing.costs import CostComponents

LINK_COLUMNS: tuple[str, ...] = (
    "routable_link_id",
    "physical_edge_id",
    "u",
    "v",
    "mode",
    "direction",
    "link_kind",
    "physical_length_m",
    *CostComponents().as_link_attributes().keys(),
    "evidence_source",
    "evidence_confidence",
    "geometry",
)


def routable_links_frame(rows: list[dict[str, object]], crs: Any) -> gpd.GeoDataFrame:
    """Build a routable-link layer, returning a typed empty layer when no rows qualify."""
    if rows:
        return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    columns = {
        column: pd.Series([], dtype="object") for column in LINK_COLUMNS if column != "geometry"
    }
    return gpd.GeoDataFrame(columns, geometry=gpd.GeoSeries([], crs=crs), crs=crs)


def routable_links_to_graph(
    links: gpd.GeoDataFrame,
    *,
    directed: bool,
    preferred_cost: str = "generalized_cost_s",
) -> nx.Graph[str]:
    """Create a graph, expanding canonical bidirectional rows when needed."""
    graph: nx.Graph[str] = nx.DiGraph() if directed else nx.Graph()
    for raw_row in links.itertuples(index=False):
        row: Any = raw_row
        attrs: dict[str, Any] = dict(row._asdict())
        attrs.pop("u")
        attrs.pop("v")
        source = str(row.u)
        target = str(row.v)
        direction = str(row.direction)
        pairs = [(source, target)]
        if direction == LinkDirection.REVERSE.value:
            pairs = [(target, source)]
        elif direction == LinkDirection.BOTH.value and directed:
            pairs.append((target, source))
        for u, v in pairs:
            candidate_cost = float(str(attrs[preferred_cost]))
            if graph.has_edge(u, v) and float(graph[u][v][preferred_cost]) <= candidate_cost:
                continue
            graph.add_edge(u, v, **attrs)
    return graph


def costs_are_valid(links: pd.DataFrame) -> bool:
    """Finite costs and lengths, no cost below zero, and none at zero on a link with length.

    OpenStreetMap has occasional zero-length pieces (two nodes at one point);
    they cost nothing to cross, which a shortest-path search allows.
    """
    cost = links.generalized_cost_s.to_numpy(float)
    length = links.physical_length_m.to_numpy(float)
    finite = np.isfinite(cost) & np.isfinite(length) & (length >= 0)
    return bool(np.all(finite & ((cost > 0) | ((cost == 0) & (length == 0)))))
