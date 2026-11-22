"""Canonical physical-edge frame utilities shared by adapters and routing."""

from __future__ import annotations

import geopandas as gpd

from walkbike_routes.exceptions import AccessibilityError


def drop_duplicate_physical_edges(
    edges: gpd.GeoDataFrame,
    *,
    id_field: str = "physical_edge_id",
) -> gpd.GeoDataFrame:
    """Drop exact duplicate edge rows and reject conflicting rows that share an ID.

    Source networks occasionally repeat a way, which yields identical rows with
    the same identifier. Routing is unaffected because graph construction keys
    on node pairs, but flow assignment maps weight onto every row, so repeated
    rows silently inflate segment totals. Rows that share an identifier while
    differing in any attribute or geometry are an upstream defect and raise.
    """
    result = edges.reset_index(drop=True)
    ids = result[id_field].astype(str)
    duplicated = ids.duplicated(keep=False)
    if not duplicated.any():
        return result
    comparable = result.loc[duplicated].copy()
    comparable["_geometry_wkb"] = comparable.geometry.to_wkb()
    attribute_columns = [column for column in comparable.columns if column != "geometry"]
    exact_repeat = comparable.duplicated(subset=attribute_columns, keep="first")
    remaining = comparable.loc[~exact_repeat]
    remaining_ids = remaining[id_field].astype(str)
    conflicting = sorted(set(remaining_ids[remaining_ids.duplicated(keep=False)]))
    if conflicting:
        raise AccessibilityError(
            f"physical edges contain conflicting rows for the same identifier: {conflicting[:10]}"
        )
    keep = ~duplicated | result.index.isin(remaining.index)
    return gpd.GeoDataFrame(
        result.loc[keep].reset_index(drop=True),
        geometry="geometry",
        crs=edges.crs,
    )
