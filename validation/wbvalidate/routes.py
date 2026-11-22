"""Do the cycling presets choose the streets riders actually used?

Counts mix route choice with demand: a street can be busy because the model
sends riders there, or because many trips start near it.  Recorded trips
take demand out.  For each trip (:mod:`wbvalidate.traces`),
its first and last points are attached to the network as homes are (to the
nearest street in the main connected piece, however far: how far each end
moved is kept, :data:`FAR_M`), and each preset's cheapest route between them
is compared with the trace:

- **observed covered**: the share of the trace's length within the buffer
  of the modelled route;
- **modelled covered**: the share of the modelled route's length within the
  buffer of the trace;
- **overlap**: the mean of the two, 1 when the routes are the same.

The buffer (:data:`BUFFER_M`, with :data:`BUFFERS_M` as a sensitivity)
allows for traces drawn a little off the street centre line while staying
narrower than a city block, so a parallel street does not count.

Scores are over the trips every preset routed, so presets are compared on
the same trips.  Intervals are a bootstrap over riders, not trips: riders
are drawn with replacement and all their trips come with them, since one
keen rider's many trips are not independent evidence.  The difference from
the shortest-distance preset is computed on the same draws.  Nothing is
fitted to the trips.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from walkbike_routes import config as settings
from walkbike_routes import study as study_runs
from walkbike_routes.adapters import canonical_origins
from walkbike_routes.costs import presets
from walkbike_routes.network import prepare
from walkbike_routes.routing.routes import measure_path
from walkbike_routes.routing.turns import sink
from wbvalidate.compare import DRAWS, SEED, interval

BUFFER_M = 20.0
BUFFERS_M = (10.0, 20.0, 30.0)
BASELINE = "shortest"
CLOSE = 0.8  # an overlap at least this high counts as the same route
FAR_M = 50.0  # an end moved farther than this to reach the network is reported
ROUTED = "routed"
NOT_JOINED = "an end did not join the network"
NO_ROUTE = "no route between the ends"
SAME_NODE = "both ends at one node"
START, END = "start", "end"


@dataclass(frozen=True)
class RouteScore:
    """One preset's agreement with the recorded routes."""

    preset: str
    trips: int
    riders: int
    mean: float
    low: float
    high: float
    close: float  # share of trips with overlap of at least CLOSE
    close_low: float
    close_high: float
    rider_mean: float  # each rider's mean overlap, averaged over riders
    difference: float = math.nan  # mean minus the baseline preset's, on the same trips
    difference_low: float = math.nan
    difference_high: float = math.nan


def column(buffer_m: float) -> str:
    """The overlap column for a buffer width."""
    return f"overlap_{buffer_m:g}m"


def trip_ends(lines: gpd.GeoSeries, trip_ids: pd.Series) -> gpd.GeoDataFrame:
    """Each trip's first and last points as origins (``<trip>|start``, ``<trip>|end``)."""
    ids, points = [], []
    for trip, line in zip(trip_ids.astype(str), lines, strict=True):
        ids += [f"{trip}|{START}", f"{trip}|{END}"]
        points += [Point(line.coords[0]), Point(line.coords[-1])]
    frame = gpd.GeoDataFrame({"origin_id": ids, "trips": 1.0}, geometry=points, crs=lines.crs)
    return canonical_origins(frame, weight_field="trips", weight_unit="trip ends")


def context(
    study: settings.Study,
    links: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    ends: gpd.GeoDataFrame,
    out: Path,
) -> study_runs.Context:
    """A study context whose origins are the trip ends."""
    return study_runs.Context(
        study=study,
        edges=prepare.physical_edges(links, nodes),
        nodes=prepare.nodes(nodes),
        origins=ends,
        destinations=ends.iloc[:0],
        gates=ends.iloc[:0],
        out=out,
    )


def route(
    network: study_runs.ModeNetwork, objective: str, start: str, end: str
) -> tuple[str, ...] | None:
    """The physical edges of the cheapest route from node ``start`` to ``end``, or None.

    On a movement graph (a preset with turn costs) the route ends at the
    end node's sink, so it may arrive from any direction.
    """
    target = end if network.search is network.graph else sink(end)
    try:
        _, path = nx.bidirectional_dijkstra(network.search, start, target, weight=objective)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    return measure_path(network.search, path).physical_edge_ids


def overlap(trace: BaseGeometry, modelled: BaseGeometry, buffer_m: float) -> tuple[float, float]:
    """(observed covered, modelled covered): each line's share within ``buffer_m`` of the other."""
    if trace.length == 0 or modelled.length == 0:
        return math.nan, math.nan
    observed = _covered(trace, modelled.buffer(buffer_m)) / trace.length
    covered = _covered(modelled, trace.buffer(buffer_m)) / modelled.length
    return min(observed, 1.0), min(covered, 1.0)


def _covered(line: BaseGeometry, area: BaseGeometry) -> float:
    """The length of ``line`` inside ``area``, segment by segment.

    Intersecting the whole line would merge stretches ridden twice (a rider
    doubling back) and count them once; each segment is counted on its own.
    """
    parts = list(line.geoms) if hasattr(line, "geoms") else [line]
    segments = [
        shapely.linestrings([a, b])
        for part in parts
        for a, b in itertools.pairwise(part.coords)
        if a != b
    ]
    if not segments:
        return 0.0
    shapely.prepare(area)
    return float(shapely.length(shapely.intersection(np.array(segments), area)).sum())


def route_trips(
    built: study_runs.ModeNetwork,
    preset: presets.Preset,
    trips: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    buffers_m: tuple[float, ...] = BUFFERS_M,
) -> pd.DataFrame:
    """One row per trip: the route status, how far each end moved to reach the
    network, the modelled length and edges, and the overlaps.

    ``trips`` has ``trip_id`` and the joined trace as its geometry, in the
    network's CRS; ``built`` is the preset's network with the trip ends
    attached (:func:`trip_ends`).
    """
    attached = built.attachments[built.attachments.network_attached.astype(bool)]
    node = dict(zip(attached.origin_id.astype(str), attached.attached_node_id.astype(str),
                    strict=True))  # fmt: skip
    moved = dict(zip(attached.origin_id.astype(str), attached.snap_distance_m.astype(float),
                     strict=True))  # fmt: skip
    shapes = dict(zip(edges.physical_edge_id.astype(str), edges.geometry, strict=True))
    rows = []
    for trip, trace in zip(trips.trip_id.astype(str), trips.geometry, strict=True):
        row: dict[str, object] = {"trip_id": trip, "preset": preset.name, "route_m": math.nan,
                                  "edges": ()}  # fmt: skip
        start, end = node.get(f"{trip}|{START}"), node.get(f"{trip}|{END}")
        # How far each end moved along and to its street to reach the network.
        row["start_moved_m"] = moved.get(f"{trip}|{START}", math.nan)
        row["end_moved_m"] = moved.get(f"{trip}|{END}", math.nan)
        found = None
        if start is None or end is None:  # only on a network with no usable street
            row["route_status"] = NOT_JOINED
        elif start == end:
            row["route_status"] = SAME_NODE
        else:
            found = route(built, preset.objective, start, end)
            row["route_status"] = ROUTED if found else NO_ROUTE
        modelled = modelled_line(found, shapes) if found else None
        if modelled is not None:
            row["route_m"] = modelled.length
            row["edges"] = found
        for width in buffers_m:
            observed, covered = (
                overlap(trace, modelled, width) if modelled is not None else (math.nan, math.nan)
            )
            row[f"observed_{width:g}m"] = observed
            row[f"modelled_{width:g}m"] = covered
            row[column(width)] = (observed + covered) / 2
        rows.append(row)
    return pd.DataFrame(rows)


def modelled_line(
    edge_ids: tuple[str, ...], shapes: dict[str, BaseGeometry]
) -> BaseGeometry | None:
    """The route's edges as one line where they join, else a multi-line."""
    parts = [shapes[e] for e in edge_ids if e in shapes]
    if not parts:
        return None
    merged = shapely.line_merge(shapely.union_all(parts))
    return None if merged.is_empty else merged


def rider_draws(
    user_ids: pd.Series | pd.Index, *, seed: int = SEED, draws: int = DRAWS
) -> tuple[np.ndarray, np.ndarray]:
    """(each row's rider index, draws × riders: how often each rider is drawn).

    Riders are drawn with replacement, as many as there are; a statistic
    over the rows is recomputed on each draw by weighting each rider's rows
    by their count.
    """
    riders, rider = np.unique(np.asarray(user_ids).astype(str), return_inverse=True)
    rng = np.random.default_rng(seed)
    picks = np.array([np.bincount(rng.integers(0, len(riders), len(riders)),
                                  minlength=len(riders)) for _ in range(draws)], dtype=float)  # fmt: skip
    return rider, picks


def route_scores(
    rows: pd.DataFrame,
    *,
    buffer_m: float = BUFFER_M,
    baseline: str = BASELINE,
    seed: int = SEED,
    draws: int = DRAWS,
) -> list[RouteScore]:
    """Each preset's scores over the trips every preset routed.

    ``rows`` has ``trip_id``, ``user_id``, ``preset`` and the overlap column
    for ``buffer_m``; rows of trips not compared should already be dropped.
    """
    wide = rows.pivot_table(index=["trip_id", "user_id"], columns="preset",
                            values=column(buffer_m), aggfunc="first").dropna()  # fmt: skip
    if wide.empty:
        return []
    rider, picks = rider_draws(wide.index.get_level_values("user_id"), seed=seed, draws=draws)
    riders = np.arange(picks.shape[1])
    trips_per = np.bincount(rider, minlength=len(riders)).astype(float)
    weights = picks @ trips_per

    def boot(values: np.ndarray) -> np.ndarray:
        sums = np.bincount(rider, weights=values, minlength=len(riders))
        return np.asarray((picks @ sums) / weights)

    base = wide[baseline].to_numpy(dtype=float) if baseline in wide else None
    found = []
    for name in wide.columns:
        values = wide[name].to_numpy(dtype=float)
        close = (values >= CLOSE).astype(float)
        means = np.bincount(rider, weights=values, minlength=len(riders)) / trips_per
        low, high = interval(boot(values))
        close_low, close_high = interval(boot(close))
        diff = diff_low = diff_high = math.nan
        if base is not None and name != baseline:
            diff = float(values.mean() - base.mean())
            diff_low, diff_high = interval(boot(values - base))
        found.append(
            RouteScore(str(name), len(values), len(riders), float(values.mean()), low, high,
                       float(close.mean()), close_low, close_high, float(means.mean()), diff,
                       diff_low, diff_high)
        )  # fmt: skip
    return found
