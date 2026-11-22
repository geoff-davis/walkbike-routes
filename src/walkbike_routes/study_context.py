"""What every run in a study shares: the streets and homes, the output folder, and each run's summary row."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd

from walkbike_routes import config as settings
from walkbike_routes.models import Mode
from walkbike_routes.network import build_signal_wait_inventory
from walkbike_routes.network.signals import SignalWaitInventory

log = logging.getLogger(__name__)

MPS_PER_MPH = 0.44704


SNAP_M = 200.0  # entrances and job clusters farther than this from the network are left out


def connected(edges: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The edges of the largest piece of a network, for homes to join.

    Every home is on a street that leads somewhere.  A map's loose ends (a
    parking aisle whose driveway is not drawn, a street cut at the edge of
    the area) would strand the homes nearest them, so homes join the nearest
    street of the main piece instead, however far it is.  Only edges with
    both ends in ``nodes`` (the nodes routes can use) count: a street the mode
    cannot travel either way along is no part of any piece.
    """
    known = set(nodes.node_id.astype(str))
    edges = edges[edges.u.astype(str).isin(known) & edges.v.astype(str).isin(known)]
    if edges.empty:
        return edges
    graph: nx.Graph[str] = nx.Graph()
    graph.add_edges_from(zip(edges.u.astype(str), edges.v.astype(str), strict=True))
    main = max(nx.connected_components(graph), key=lambda piece: (len(piece), min(piece)))
    return edges[edges.u.astype(str).isin(main)]


CAR_SNAP_SPEED_MPH = 10.0


CORE_MODES = {"walk": Mode.PEDESTRIAN, "bike": Mode.BICYCLE, "car": Mode.CAR}


@dataclass(frozen=True)
class Context:
    """What every run in a study shares."""

    study: settings.Study
    edges: gpd.GeoDataFrame
    nodes: gpd.GeoDataFrame
    origins: gpd.GeoDataFrame
    destinations: gpd.GeoDataFrame
    gates: gpd.GeoDataFrame
    out: Path
    # The streets' signals, found once for the study; None: found when first needed.
    signals: SignalWaitInventory | None = None


def signal_inventory(context: Context) -> SignalWaitInventory:
    """The signals on the study's streets: the study's own when it holds them."""
    return context.signals or build_signal_wait_inventory(context.nodes, context.edges)


def write(folder: Path, **frames: pd.DataFrame) -> list[Path]:
    paths = []
    for name, frame in frames.items():
        path = folder / f"{name}.parquet"
        frame.to_parquet(path)
        paths.append(path)
    return paths


def summary(
    context: Context,
    destination: settings.Destination,
    mode: str,
    preset: str,
    routes: pd.DataFrame,
    shares: pd.DataFrame | None,
    cap: float | None,
) -> dict[str, object]:
    """One row of headline figures for a destination, mode and preset."""
    weight = routes.demand_weight.astype(float)
    total = float(weight.sum())
    reachable = routes.route_reachable.astype(bool)
    minutes = routes.total_travel_time_s.astype(float) / 60.0
    if "within_time_limit" in routes:
        within = routes.within_time_limit.astype(bool)
    else:  # routes without the flag: work it out from the cap
        within = reachable & (minutes.le(cap) if cap is not None else True)
    row: dict[str, object] = {
        "destination_id": destination.id,
        "mode": mode,
        "preset": preset,
        "weight_unit": str(context.origins.weight_unit.iloc[0]) if len(context.origins) else "",
        "total_weight": total,
        "reachable_weight": float(weight[reachable].sum()),
        "time_cap_min": cap if cap is not None else math.nan,
        "within_cap_weight": float(weight[within].sum()),
        # Every home that can get there at all, however long the trip.
        "median_minutes": _weighted_median(minutes[reachable], weight[reachable]),
        # Only homes within the time limit: the figure printed beside the limit.
        "median_minutes_within_cap": _weighted_median(
            minutes[reachable & within], weight[reachable & within]
        ),
    }
    for band in context.study.time_bands_min:
        row[f"within_{band:g}_min_weight"] = float(weight[reachable & minutes.le(band)].sum())
    if shares is not None and not shares.empty:
        row["mean_routes_per_origin"] = float(shares.groupby("origin_id").size().mean())
    return row


def _weighted_median(values: pd.Series, weights: pd.Series) -> float:
    if values.empty or float(weights.sum()) <= 0:
        return math.nan
    order = values.sort_values().index
    cumulative = weights[order].cumsum()
    return float(values[order][cumulative.ge(cumulative.iloc[-1] / 2)].iloc[0])
