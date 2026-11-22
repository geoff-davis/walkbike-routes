"""Long-form demand assignment to canonical physical edges."""

from __future__ import annotations

from collections import defaultdict

import geopandas as gpd
import numpy as np
import pandas as pd

from walkbike_routes.edges import drop_duplicate_physical_edges
from walkbike_routes.exceptions import AccessibilityError

FLOW_KEYS = (
    "study_id",
    "destination_id",
    "profile_id",
    "scenario_id",
    "mode",
    "direction",
    "weight_unit",
)


def shared_route_flows(
    assignments: pd.DataFrame,
    physical_edges: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Potential flow on each physical edge from routes carrying shares of a weight.

    ``assignments`` has one row per origin and route with ``assigned_weight``
    (the origin's weight times the route's share) and ``physical_edge_ids``,
    as :func:`walkbike_routes.choice.split_routes` writes them.  An edge used
    by several of one origin's routes receives each share once.
    """
    totals = FlowTotals()
    totals.add(assignments)
    return totals.layer(physical_edges)


class FlowTotals:
    """Flows added up over several sets of route shares, then laid on the edges.

    Each :meth:`add` takes assignments as :func:`shared_route_flows` does;
    an origin counts once among an edge's contributors however many sets
    it appears in.
    """

    def __init__(self) -> None:
        self.flow: defaultdict[str, float] = defaultdict(float)
        self.contributors: defaultdict[str, set[str]] = defaultdict(set)

    def add(self, assignments: pd.DataFrame) -> None:
        missing = sorted({"origin_id", "assigned_weight", "physical_edge_ids"} - set(assignments))
        if missing:
            raise AccessibilityError(f"route shares missing fields: {missing}")
        weights = pd.to_numeric(assignments.assigned_weight, errors="coerce")
        if weights.isna().any() or not np.isfinite(weights).all() or (weights < 0).any():
            raise AccessibilityError("assigned route weight must be finite and nonnegative")
        for origin_id, edge_ids, weight in zip(
            assignments.origin_id.astype(str),
            assignments.physical_edge_ids.fillna("").astype(str),
            weights.astype(float),
            strict=True,
        ):
            for edge_id in {value for value in edge_ids.split(";") if value}:
                self.flow[edge_id] += weight
                self.contributors[edge_id].add(origin_id)

    def layer(self, physical_edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        base = drop_duplicate_physical_edges(physical_edges.copy())
        base["physical_edge_id"] = base.physical_edge_id.astype(str)
        unknown = sorted(set(self.flow) - set(base.physical_edge_id))
        if unknown:
            raise AccessibilityError(f"routes reference missing physical edges: {unknown[:20]}")
        base["potential_flow"] = base.physical_edge_id.map(self.flow).fillna(0.0)
        base["contributing_origins"] = base.physical_edge_id.map(
            lambda e: len(self.contributors.get(e, ()))
        ).astype(int)
        base["not_observed_use"] = True
        keep = ["physical_edge_id", "potential_flow", "contributing_origins", "not_observed_use"]
        return gpd.GeoDataFrame(
            base[[*keep, "geometry"]], geometry="geometry", crs=physical_edges.crs
        )
