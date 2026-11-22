"""What ridden and modelled routes are made of, and how many trips use each street.

Route overlap (:mod:`wbvalidate.routes`) asks whether a preset picks the
very streets ridden.  In a grid, many routes tie on length and the choice
among them is close to arbitrary, so overlap can be low for a preset that
is right about what riders want.  Two checks look past the ties:

- **Makeup.**  For each route, ridden or modelled, the share of its length
  on separated paths and tracks, on bike lanes, on low-stress
  (LTS 1–2) and high-stress (LTS 3–4) streets and against a one-way, and
  its turns and busy-street crossings per km.  Totals are summed over
  trips, so long trips weigh more, and compared with the ridden routes.
- **Trips per street.**  Each link's count of trips whose ridden route uses
  it, against the count whose modelled route does: Spearman's rank
  correlation over links any route used.  Summing many trips cancels the
  arbitrary choice between tied routes; what remains is whether a preset
  sends riders to the streets they use.

Ridden traces and modelled routes are matched to the network's links the
same way, so matching errors fall on both alike.  A link counts as used
when at least :data:`MATCH_SHARE` of its length lies within
:data:`~wbvalidate.routes.BUFFER_M` of the line; a street crossed, not
ridden along, has only the buffer's width near the line and is not used.
Its direction is the order in which the line passes its two ends.  On a
divided street, mapped as two one-way carriageways, a line is near both: the
carriageway it would ride the wrong way is dropped when the other covers the
same stretch (so a rider truly on the wrong carriageway is counted on the
right one).

Turns are counted on the line simplified by :data:`SIMPLIFY_M`, as heading
changes of more than :data:`TURN_DEG`.  A busy-street crossing is a place
within :data:`NEAR_M` of the line where a busy street (primary, secondary
or trunk) meets it without being ridden: places within
:data:`SAME_STREET_M` of a used link of the same street are turns onto or
off it, and places closer than :data:`CLUSTER_M` count once.

Intervals are the same bootstrap over riders as the overlap scores.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy import stats
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from walkbike_routes.attributes import bicycle_oneway, junction_tag, oneway_allows
from wbvalidate.compare import DRAWS, SEED, interval
from wbvalidate.routes import BUFFER_M, rider_draws

MATCH_SHARE = 0.6
SIMPLIFY_M = 10.0
TURN_DEG = 45.0
NEAR_M = 5.0
SAME_STREET_M = 30.0
CLUSTER_M = 25.0
BUSY = frozenset({"trunk", "primary", "secondary"})
# Bike facilities apart from traffic; prepared edges say "separated path" for OSM's "path".
SEPARATED = frozenset({"path", "separated path", "track"})
PARALLEL_DEG = 30.0  # an unnamed busy link this close in heading to a used one continues it
RIDDEN = "ridden"
LTS_FIELD = "lts_furth"
CHUNK = 250  # bootstrap draws per block of the street-total matrix

# (column, label): metres of each kind, shared over matched metres.
SHARES = (
    ("separated_m", "Separated path or track"),
    ("lane_m", "Bike lane"),
    ("low_m", "Low stress (LTS 1–2)"),
    ("high_m", "High stress (LTS 3–4)"),
    ("against_m", "Against a one-way"),
)
# (column, label): counts per km of route.
RATES = (
    ("turns", "Turns per km"),
    ("crossings", "Busy-street crossings per km"),
)


@dataclass(frozen=True)
class Links:
    """The network's links, ready for matching lines against."""

    ids: np.ndarray  # physical edge ids
    geoms: np.ndarray
    length: np.ndarray
    names: np.ndarray  # street name, or "" when unnamed
    separated: np.ndarray
    lane: np.ndarray
    low: np.ndarray
    high: np.ndarray
    forward_ok: np.ndarray  # bikes may ride u to v
    reverse_ok: np.ndarray
    busy: np.ndarray
    tree: STRtree = field(repr=False)
    # Busy streets, rideable or not, for crossings.
    busy_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=object))
    busy_geoms: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=object))
    busy_names: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=object))
    busy_tree: STRtree = field(default_factory=lambda: STRtree([]), repr=False)


def prepare_links(edges: gpd.GeoDataFrame, roads: gpd.GeoDataFrame | None = None) -> Links:
    """Matching arrays for ``edges`` (physical edges with ``physical_edge_id``, in metres).

    ``edges`` are the links a route can ride; ``roads`` (default ``edges``)
    are searched for busy streets crossed, since a route can cross a busy
    road that bikes may not ride along.
    """
    roads = (edges if roads is None else roads).reset_index(drop=True)
    is_busy = _text(roads, "highway").str.removesuffix("_link").isin(BUSY).to_numpy()
    busy_geoms = np.asarray(roads.geometry.to_numpy(), dtype=object)[is_busy]
    edges = edges.reset_index(drop=True)
    geoms = np.asarray(edges.geometry.to_numpy(), dtype=object)
    facility = _text(edges, "bike_facility")
    highway = _text(edges, "highway").str.removesuffix("_link")
    lts = pd.to_numeric(edges.get(LTS_FIELD, pd.Series(np.nan, index=edges.index)),
                        errors="coerce").to_numpy(dtype=float)  # fmt: skip
    rows = list(edges.itertuples(index=False))
    return Links(
        ids=edges.physical_edge_id.astype(str).to_numpy(),
        geoms=geoms,
        length=shapely.length(geoms),
        names=_text(edges, "name").str.strip().to_numpy(),
        separated=facility.isin(SEPARATED).to_numpy(),
        lane=facility.eq("lane").to_numpy(),
        low=lts <= 2,
        high=lts >= 3,
        forward_ok=np.array(
            [oneway_allows(bicycle_oneway(r), junction_tag(r), True) for r in rows], dtype=bool
        ),  # fmt: skip
        reverse_ok=np.array(
            [oneway_allows(bicycle_oneway(r), junction_tag(r), False) for r in rows], dtype=bool
        ),  # fmt: skip
        busy=highway.isin(BUSY).to_numpy(),
        tree=STRtree(geoms),
        busy_ids=roads.physical_edge_id.astype(str).to_numpy()[is_busy],
        busy_geoms=busy_geoms,
        busy_names=_text(roads, "name").str.strip().to_numpy()[is_busy],
        busy_tree=STRtree(busy_geoms),
    )


def _text(edges: pd.DataFrame, column: str) -> pd.Series:
    if column not in edges:
        return pd.Series("", index=edges.index)
    return edges[column].fillna("").astype(str)


def match(
    line: BaseGeometry, links: Links, buffer_m: float = BUFFER_M, share: float = MATCH_SHARE
) -> tuple[np.ndarray, np.ndarray]:
    """(link positions, ridden u to v) for the links ``line`` rides along."""
    area = line.buffer(buffer_m)
    near = links.tree.query(area, predicate="intersects")
    if len(near) == 0:
        return near, np.zeros(0, dtype=bool)
    shapely.prepare(area)
    inside = shapely.length(shapely.intersection(links.geoms[near], area))
    used = near[inside >= share * links.length[near]]
    starts = shapely.line_locate_point(line, shapely.get_point(links.geoms[used], 0))  # type: ignore[call-overload]
    ends = shapely.line_locate_point(line, shapely.get_point(links.geoms[used], -1))  # type: ignore[call-overload]
    forward = starts <= ends
    keep = ~_far_carriageway(used, forward, np.minimum(starts, ends), np.maximum(starts, ends),
                             links)  # fmt: skip
    return used[keep], forward[keep]


def _far_carriageway(
    used: np.ndarray, forward: np.ndarray, low: np.ndarray, high: np.ndarray, links: Links
) -> np.ndarray:
    """Links that are the other carriageway of a divided street the line rides.

    A divided street is two one-way links side by side; a line along it is
    near both.  A link ridden the wrong way is dropped when a link of the
    same street, one-way and ridden the right way, covers at least half of the same
    stretch of the line (``low`` to ``high``, distances along it): the line
    rode that one.  Blocks of one street end to end share no stretch.
    """
    allowed = np.where(forward, links.forward_ok[used], links.reverse_ok[used])
    one_way = links.forward_ok[used] != links.reverse_ok[used]
    far = np.zeros(len(used), dtype=bool)
    names = links.names[used]
    for k in np.flatnonzero(~allowed):
        if not names[k]:
            continue
        twins = np.flatnonzero(allowed & one_way & (names == names[k]))
        shared = np.minimum(high[k], high[twins]) - np.maximum(low[k], low[twins])
        far[k] = bool((shared >= 0.5 * (high[k] - low[k])).any()) and high[k] > low[k]
    return far


def describe(
    line: BaseGeometry, links: Links, buffer_m: float = BUFFER_M
) -> tuple[dict[str, float], np.ndarray]:
    """One route's length, matched metres of each kind, turns and busy-street crossings.

    Also returns the positions of the links it uses.
    """
    used, forward = match(line, links, buffer_m)
    metres = links.length[used]
    allowed = np.where(forward, links.forward_ok[used], links.reverse_ok[used])
    row = {
        "length_m": float(line.length),
        "matched_m": float(metres.sum()),
        "separated_m": float(metres[links.separated[used]].sum()),
        "lane_m": float(metres[links.lane[used]].sum()),
        "low_m": float(metres[links.low[used]].sum()),
        "high_m": float(metres[links.high[used]].sum()),
        "against_m": float(metres[~allowed].sum()),
        "turns": float(turns(line)),
        "crossings": float(crossings(line, links, used)),
    }
    return row, used


def turns(line: BaseGeometry, simplify_m: float = SIMPLIFY_M, turn_deg: float = TURN_DEG) -> int:
    """Heading changes of more than ``turn_deg`` along ``line`` simplified by ``simplify_m``."""
    merged = shapely.line_merge(line) if line.geom_type == "MultiLineString" else line
    found = 0
    for part in getattr(merged, "geoms", [merged]):
        xy = np.asarray(part.simplify(simplify_m).coords)[:, :2]
        step = np.diff(xy, axis=0)
        step = step[np.hypot(step[:, 0], step[:, 1]) > 0]
        if len(step) < 2:
            continue
        heading = np.degrees(np.arctan2(step[:, 1], step[:, 0]))
        change = (np.diff(heading) + 180) % 360 - 180
        found += int((np.abs(change) > turn_deg).sum())
    return found


def crossings(line: BaseGeometry, links: Links, used: np.ndarray) -> int:
    """Places where a busy street meets ``line`` without being ridden along.

    Each point where a busy link meets the line counts; a link near the line
    but not touching it (a trace drawn off the centre line) counts at its
    closest point, and stretches where it runs along the line are ridden,
    not crossed.  Meeting a used link of the same street (or, unnamed, a used
    busy link heading the same way) is a turn onto or off it.
    """
    near = links.busy_tree.query(line, predicate="dwithin", distance=NEAR_M)
    near = near[~np.isin(links.busy_ids[near], links.ids[used])]
    if len(near) == 0:
        return 0
    named = links.names[used]
    keep = []
    for position in near:
        geom = links.busy_geoms[position]
        meet = shapely.intersection(geom, line)
        parts = [] if meet.is_empty else list(shapely.get_parts(meet))
        points = [p for p in parts if p.geom_type == "Point"]  # stretches along it are ridden
        if not parts:
            points = [shapely.get_point(shapely.shortest_line(geom, line), 0)]
        name = links.busy_names[position]
        if name:
            same = used[named == name]
        else:  # unnamed: a used unnamed busy link heading the same way continues it
            same = used[links.busy[used] & (named == "")]
            same = same[[_parallel(geom, links.geoms[k]) for k in same]]
        for point in points:
            if len(same) and shapely.distance(point, links.geoms[same]).min() <= SAME_STREET_M:
                continue  # turning onto or off it
            keep.append(point)
    if not keep:
        return 0
    merged = shapely.union_all(shapely.buffer(np.array(keep), CLUSTER_M / 2))
    return len(getattr(merged, "geoms", [merged]))


def _parallel(a: BaseGeometry, b: BaseGeometry) -> bool:
    """Whether two links' end-to-end headings differ by under PARALLEL_DEG, either way round."""
    (ax, ay), (bx, by) = (np.subtract(g.coords[-1][:2], g.coords[0][:2]) for g in (a, b))
    angle = abs(math.degrees(math.atan2(ay, ax) - math.atan2(by, bx))) % 180
    return min(angle, 180 - angle) < PARALLEL_DEG


def oriented_line(
    edge_ids: tuple[str, ...], shapes: Mapping[str, BaseGeometry], start: Point
) -> BaseGeometry | None:
    """A route's edges, in route order, joined head to tail as ridden from ``start``.

    Edges are stored in their drawn direction, not the direction ridden, so
    each is turned to begin where the one before ended; the first is turned
    to lead into the second (or, alone, to begin nearer ``start``).  Pieces
    that do not meet start a new part.
    """
    parts = [np.asarray(shapes[e].coords)[:, :2] for e in edge_ids if e in shapes]
    if not parts:
        return None
    first = parts[0]
    if len(parts) > 1:  # end where the second edge begins
        turn = _gap(first[0], parts[1][[0, -1]]) < _gap(first[-1], parts[1][[0, -1]])
    else:  # begin at the start
        here = np.asarray(start.coords)[:, :2]
        turn = _gap(first[-1], here) < _gap(first[0], here)
    if turn:
        first = first[::-1]
    lines, coords = [], [first]
    for part in parts[1:]:
        tail = coords[-1][-1]
        if np.hypot(*(part[-1] - tail)) < np.hypot(*(part[0] - tail)):
            part = part[::-1]
        if np.hypot(*(part[0] - tail)) > 1.0:
            lines.append(np.concatenate(coords))
            coords = [part]
        else:
            coords.append(part[1:])
    lines.append(np.concatenate(coords))
    shaped = [LineString(c) for c in lines if len(c) >= 2]
    if not shaped:
        return None
    return shaped[0] if len(shaped) == 1 else MultiLineString(shaped)


def _gap(point: np.ndarray, others: np.ndarray) -> float:
    return float(np.hypot(*(others - point).T).min())


@dataclass(frozen=True)
class Makeup:
    """One source's (ridden, or a preset's) route makeup over the compared trips."""

    source: str
    values: dict[str, tuple[float, float, float]]  # column -> (value, low, high)
    differences: dict[str, tuple[float, float, float]]  # minus the ridden routes'


def makeup(rows: pd.DataFrame, *, seed: int = SEED, draws: int = DRAWS) -> list[Makeup]:
    """Each source's shares and rates, with rider-bootstrap intervals and differences.

    ``rows`` has ``trip_id``, ``user_id``, ``source`` and the columns of
    :func:`describe`, one row per trip and source; trips some source lacks
    are dropped, so every source covers the same trips.
    """
    every = rows.groupby("trip_id").source.nunique()
    rows = rows[rows.trip_id.isin(every[every.eq(rows.source.nunique())].index)]
    if rows.empty:
        return []
    rows = rows.sort_values(["source", "trip_id"])  # each source's trips in one order
    sources = list(dict.fromkeys([RIDDEN, *rows.source.unique()]))
    sources = [s for s in sources if s in set(rows.source)]
    first = rows[rows.source.eq(sources[0])]
    rider, picks = rider_draws(first.user_id, seed=seed, draws=draws)
    riders = picks.shape[1]

    def boot(top: np.ndarray, bottom: np.ndarray) -> tuple[float, np.ndarray]:
        sums = np.bincount(rider, weights=top, minlength=riders)
        under = np.bincount(rider, weights=bottom, minlength=riders)
        with np.errstate(invalid="ignore", divide="ignore"):
            return float(top.sum() / bottom.sum()), (picks @ sums) / (picks @ under)

    def ratios(part: pd.DataFrame) -> dict[str, tuple[float, np.ndarray]]:
        matched = part.matched_m.to_numpy(dtype=float)
        km = part.length_m.to_numpy(dtype=float) / 1000
        found = {c: boot(part[c].to_numpy(dtype=float), matched) for c, _ in SHARES}
        found |= {c: boot(part[c].to_numpy(dtype=float), km) for c, _ in RATES}
        return found

    by_source = {s: ratios(rows[rows.source.eq(s)]) for s in sources}
    ridden = by_source.get(RIDDEN)
    out = []
    for source in sources:
        values, differences = {}, {}
        for column, (value, draws_) in by_source[source].items():
            values[column] = (value, *interval(draws_))
            if ridden is not None and source != RIDDEN:
                base, base_draws = ridden[column]
                differences[column] = (value - base, *interval(draws_ - base_draws))
        out.append(Makeup(source, values, differences))
    return out


@dataclass(frozen=True)
class StreetScore:
    """How well one preset's trips per link rank the ridden trips per link."""

    preset: str
    links: int
    rho: float
    low: float
    high: float
    difference: float = math.nan  # rho minus the baseline preset's, on the same draws
    difference_low: float = math.nan
    difference_high: float = math.nan


def street_totals(used: pd.DataFrame, links: Links) -> pd.DataFrame:
    """Trips per link for each source, over links any source used.

    ``used`` has ``trip_id``, ``source`` and ``link`` (a position in
    ``links``), one row per link a trip's route uses.
    """
    counts = used.drop_duplicates().groupby(["link", "source"]).size().unstack(fill_value=0)
    counts.index = links.ids[counts.index.to_numpy()]
    counts.index.name = "physical_edge_id"
    counts.columns.name = None
    return counts


def street_scores(
    used: pd.DataFrame,
    user_of: pd.Series,
    *,
    baseline: str,
    seed: int = SEED,
    draws: int = DRAWS,
) -> list[StreetScore]:
    """Spearman's rho between ridden and modelled trips per link, for each preset.

    ``used`` is as for :func:`street_totals`; ``user_of`` maps each trip to
    its rider.  Links any source used are ranked; intervals redraw riders.
    """
    used = used.drop_duplicates()
    if used.empty or RIDDEN not in set(used.source):
        return []
    trips = used.trip_id.drop_duplicates().sort_values()
    rider, picks = rider_draws(user_of.reindex(trips).astype(str), seed=seed, draws=draws)
    rider_of = pd.Series(rider, index=trips.to_numpy())
    column = {link: i for i, link in enumerate(sorted(used.link.unique()))}
    shape = (picks.shape[1], len(column))

    def matrix(source: str) -> np.ndarray:
        part = used[used.source.eq(source)]
        out = np.zeros(shape)
        np.add.at(out, (rider_of.loc[part.trip_id].to_numpy(), part.link.map(column).to_numpy()), 1)
        return out

    ridden = matrix(RIDDEN)
    presets_ = [s for s in dict.fromkeys(used.source) if s != RIDDEN]
    found = {name: _rho_draws(ridden, matrix(name), picks) for name in presets_}
    out = []
    for name in presets_:
        rho, draws_ = found[name]
        diff = diff_low = diff_high = math.nan
        if baseline in found and name != baseline:
            diff = rho - found[baseline][0]
            diff_low, diff_high = interval(draws_ - found[baseline][1])
        out.append(StreetScore(name, len(column), rho, *interval(draws_), diff, diff_low,
                               diff_high))  # fmt: skip
    return out


def _rho_draws(
    ridden: np.ndarray, modelled: np.ndarray, picks: np.ndarray
) -> tuple[float, np.ndarray]:
    """Rho on all riders, and on each draw of riders."""
    rho = _rho(ridden.sum(axis=0)[None, :], modelled.sum(axis=0)[None, :])[0]
    blocks = [
        _rho(picks[i : i + CHUNK] @ ridden, picks[i : i + CHUNK] @ modelled)
        for i in range(0, len(picks), CHUNK)
    ]
    return float(rho), np.concatenate(blocks)


def _rho(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Row-wise Spearman's rho, ties given their mean rank; NaN for a constant row."""
    rx = stats.rankdata(x, axis=1)
    ry = stats.rankdata(y, axis=1)
    rx -= rx.mean(axis=1, keepdims=True)
    ry -= ry.mean(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.asarray(
            (rx * ry).sum(axis=1) / np.sqrt((rx**2).sum(axis=1) * (ry**2).sum(axis=1))
        )


def street_table(
    totals: pd.DataFrame, length_m: pd.Series, names: pd.Series, rows: int = 12, min_km: float = 20
) -> pd.DataFrame:
    """Trip-km per street for each source: the streets where presets miss riders most.

    ``totals`` is :func:`street_totals`; ``length_m`` and ``names`` are by
    physical edge id.  Streets with under ``min_km`` trip-km on every
    source are left out, so no row describes a handful of trips.
    """
    km = totals.mul(length_m.reindex(totals.index).fillna(0) / 1000, axis=0)
    km = km.groupby(names.reindex(totals.index).fillna("").to_numpy()).sum()
    km = km[(km.index != "") & km.max(axis=1).ge(min_km)]
    if RIDDEN not in km or km.empty:
        return km.iloc[:0]
    gap = km.drop(columns=RIDDEN).sub(km[RIDDEN], axis=0).abs().max(axis=1)
    return km.loc[gap.sort_values(ascending=False).index[:rows]]


def collect(
    trips: Iterable[tuple[str, str, BaseGeometry]], links: Links, buffer_m: float = BUFFER_M
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Describe and match each (trip, source, line): (makeup rows, links used)."""
    described, used = [], []
    for trip, source, line in trips:
        row, positions = describe(line, links, buffer_m)
        described.append({"trip_id": trip, "source": source, **row})
        used.append(pd.DataFrame({"trip_id": trip, "source": source, "link": positions}))
    rows = pd.DataFrame(described)
    links_used = (
        pd.concat(used, ignore_index=True)
        if used
        else pd.DataFrame(columns=["trip_id", "source", "link"])
    )
    return rows, links_used
