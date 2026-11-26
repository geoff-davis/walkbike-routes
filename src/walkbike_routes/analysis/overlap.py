"""Where car traffic and walking or cycling to the same place use the same streets.

This is a screen for where to look, not a measure of risk: it says nothing
about speeds, sight lines or crashes, and the flows are potential, not
counted.  Each mode's positive flows are ranked within the study (a
percentile, zero where the mode has no flow), and the index is

    sqrt(car percentile * max(walk percentile, bike percentile))

a hand-set index, not from a published source.  Tiers mark the top 1%, 5% and 10%
of edges that carry both car and walking or cycling flow.

The map shows each mode's own overlap: on the walking view, the index from the
walk percentile alone, and on the cycling view from the bike percentile alone
(:func:`mode_tiers`), so the band follows the trips drawn.  The combined tier,
``overlap_tier``, is what the driving view shows.
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


def _tiers(car: pd.Series, active: pd.Series) -> tuple[pd.Series, pd.Series]:
    """The overlap percentile and tier from a car and an active percentile.

    Only edges with both (each percentile above zero) are ranked; the rest get
    0 and no tier.
    """
    index = (car * active).pow(0.5)
    shared = car.gt(0) & active.gt(0)
    percentile = pd.Series(0.0, index=car.index, dtype=float)
    percentile[shared] = index[shared].rank(method="average", pct=True)
    tier = pd.Series("", index=car.index, dtype=str)
    for threshold, label in reversed(TIERS):
        tier[shared & percentile.ge(threshold)] = label
    return percentile, tier


def mode_tiers(layer: pd.DataFrame, mode: str) -> pd.Series:
    """Each edge's overlap tier as one mode's view shows it.

    Walking and cycling use their own percentile against the car percentile,
    re-ranked among the edges carrying both; a mode the layer lacks has no
    tiers.  Any other mode, such as driving, gets the combined ``overlap_tier``.
    """
    if mode not in ("walk", "bike"):
        return layer.overlap_tier.fillna("").astype(str)
    column = f"{mode}_percentile"
    if column not in layer:
        return pd.Series("", index=layer.index, dtype=str)
    return _tiers(layer.car_percentile.astype(float), layer[column].astype(float))[1]


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
    base["overlap_percentile"], base["overlap_tier"] = _tiers(
        base.car_percentile, base.active_percentile
    )
    base["not_observed_flow_or_risk"] = True
    return gpd.GeoDataFrame(base, geometry="geometry", crs=edges.crs)
