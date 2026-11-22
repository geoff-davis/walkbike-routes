"""Where car traffic and walking or cycling to the same place use the same streets.

This is a screen for where to look, not a measure of risk: it says nothing
about speeds, sight lines or crashes, and the flows are potential, not
counted.  Each mode's positive flows are ranked within the study (a
percentile, zero where the mode has no flow), and the index is

    sqrt(car percentile * max(walk percentile, bike percentile))

a hand-set index, not from a published source.  Tiers mark the top 1%, 5% and 10%
of edges that carry both car and walking or cycling flow.
"""

from __future__ import annotations

from collections.abc import Mapping

import geopandas as gpd
import numpy as np
import pandas as pd

TIERS = ((0.99, "top 1%"), (0.95, "top 5%"), (0.90, "top 10%"))


def positive_percentile(values: pd.Series) -> pd.Series:
    """Rank positive values from just above 0 to 1, leaving zeros at zero."""
    numeric = values.fillna(0.0).astype(float).clip(lower=0.0)
    result = pd.Series(0.0, index=values.index, dtype=float)
    positive = numeric.gt(0)
    result[positive] = numeric[positive].rank(method="average", pct=True)
    return result


def overlap(
    edges: gpd.GeoDataFrame,
    car_flow: pd.Series,
    active_flows: Mapping[str, pd.Series],
) -> gpd.GeoDataFrame:
    """The overlap index on each physical edge.

    Flows are Series indexed by ``physical_edge_id``; ``active_flows`` maps
    ``walk`` and/or ``bike`` to theirs.
    """
    if not active_flows:
        raise ValueError("overlap needs a walking or cycling flow")
    base = edges[["physical_edge_id", "name", "highway", "length_m", "geometry"]].copy()
    ids = base.physical_edge_id.astype(str)
    base["car_flow"] = ids.map(car_flow).fillna(0.0).to_numpy()
    base["car_percentile"] = positive_percentile(base.car_flow)
    active_percentiles = []
    for mode, flow in sorted(active_flows.items()):
        base[f"{mode}_flow"] = ids.map(flow).fillna(0.0).to_numpy()
        base[f"{mode}_percentile"] = positive_percentile(base[f"{mode}_flow"])
        active_percentiles.append(base[f"{mode}_percentile"])
    base["active_percentile"] = pd.concat(active_percentiles, axis=1).max(axis=1)
    base["overlap_index"] = np.sqrt(base.car_percentile * base.active_percentile)
    shared = base.car_flow.gt(0) & base.active_percentile.gt(0)
    base["overlap_percentile"] = 0.0
    base.loc[shared, "overlap_percentile"] = base.loc[shared, "overlap_index"].rank(
        method="average", pct=True
    )
    base["overlap_tier"] = ""
    for threshold, label in reversed(TIERS):
        base.loc[shared & base.overlap_percentile.ge(threshold), "overlap_tier"] = label
    base["not_observed_flow_or_risk"] = True
    return gpd.GeoDataFrame(base, geometry="geometry", crs=edges.crs)
