"""Exposure along streets: each link's risk points, and the same-street runs that carry flow."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

from walkbike_routes.analysis.exposure_lists import (
    STREET_COLUMNS,
    by_edge,
    factors_of,
    has_count,
    number_of,
    ranked,
    shares_text,
    text_of,
    traffic_words,
)
from walkbike_routes.analysis.exposure_rules import (
    FixFirst,
    RiskPoints,
    busiest_edge,
    percent,
)
from walkbike_routes.analysis.places import (
    UnionFind,
    crossing_ways,
    edge_names,
    section_label,
)
from walkbike_routes.network.broach import traffic_band

STRESS_WORDS = {
    1: "low-stress street for cycling",
    2: "some traffic stress for cycling (level 2 of 4)",
    3: "high traffic stress for cycling (level 3 of 4)",
    4: "very high traffic stress for cycling (level 4 of 4)",
}


def street_risk(
    edges: gpd.GeoDataFrame,
    mode: str,
    car_flow: pd.Series | None = None,
    points: RiskPoints | None = None,
    lts_column: str = "lts_best",
) -> pd.DataFrame:
    """Risk points and their factors for travelling along each link.

    ``mode`` is ``walk`` or ``bike``; ``edges`` are the prepared streets
    (``network.prepare``), with ``pedestrian_status`` and the stress levels
    the run's preset reads (``lts_column``).
    """
    if mode not in {"walk", "bike"}:
        raise ValueError(f"no exposure points for {mode}")
    points = points or RiskPoints()
    ids = edges.physical_edge_id.astype(str)
    drop_off = pd.Series(False, index=edges.index)
    if car_flow is not None:
        flow = ids.map(by_edge(car_flow)).fillna(0.0).astype(float)
        # The busiest share by count, ties at the edge included, as for the busiest places.
        positive = flow[flow.gt(0)]
        edge = busiest_edge(positive, points.drop_off_share or None)
        drop_off = flow.ge(edge) & flow.gt(0)
    across = crossing_ways(edges)
    rows = []
    for record, is_across, dropping in zip(
        edges.drop(columns="geometry").to_dict("records"), across, drop_off, strict=True
    ):
        parts: list[tuple[str, float]] = []
        car = bool(record.get("car_allowed", False))
        if not is_across and car:
            band = max(traffic_band(record.get("highway"), record.get("lanes"),
                                    record.get("traffic_aadt")), 0)  # fmt: skip
            if mode == "bike":
                level = number_of(record.get(lts_column))
                lts = int(level) if math.isfinite(level) and level >= 1 else 1
                parts.append((STRESS_WORDS.get(lts, f"traffic stress {lts}"),
                              points.bike_lts.get(lts, 0.0)))  # fmt: skip
            elif text_of(record.get("pedestrian_status")) != "supported":
                parts.append(
                    ("no known sidewalk, on a street with "
                     + traffic_words(band, has_count(record.get("traffic_aadt"))),
                     points.walk_no_sidewalk[min(band, 4)])
                )  # fmt: skip
                if number_of(record.get("speed_mph")) >= points.fast_mph:
                    parts.append((f"{points.fast_mph:.12g} mph or faster", points.fast))
            if dropping and sum(p for _, p in parts) > 0:
                parts.append(
                    (f"among the busiest {percent(points.drop_off_share)} of streets for car "
                     "trips here (drop-off)", points.drop_off)
                )  # fmt: skip
        total, text = factors_of(parts)
        rows.append({"risk_points": total, "risk_factors": text})
    return pd.DataFrame(rows, index=ids.to_numpy())


def exposed_streets(
    edges: gpd.GeoDataFrame,
    flow: pd.Series,
    risk: pd.DataFrame,
    counted: float,
    by_source: Mapping[str, pd.Series] | None = None,
    rule: FixFirst | None = None,
    flow_noun: str = "people",
) -> gpd.GeoDataFrame:
    """Same-street runs of risky links that carry flow, ranked by flow × points.

    Neighbouring links join when they share a name and their points.  When
    ``flow`` adds up several destinations' flows, ``by_source`` holds each
    one's, and each place says how many of its people come from each, at
    the place's busiest link.
    """
    sources = {d: {str(k): float(v) for k, v in f.items()} for d, f in (by_source or {}).items()}
    names = edge_names(edges)
    lines = dict(zip(edges.physical_edge_id.astype(str), edges.geometry, strict=True))
    lengths = dict(zip(edges.physical_edge_id.astype(str), edges.length_m.astype(float),
                       strict=True))  # fmt: skip
    ends = dict(zip(edges.physical_edge_id.astype(str),
                    zip(edges.u.astype(str), edges.v.astype(str), strict=True), strict=True))  # fmt: skip
    flows = {str(k): float(v) for k, v in flow.items() if float(v) > 0}
    used = [p for p in flows if p in risk.index and float(risk.risk_points[p]) > 0]
    by_node: defaultdict[str, list[str]] = defaultdict(list)
    for pid in used:
        for node in set(ends[pid]):
            by_node[node].append(pid)
    union = UnionFind()
    for pid in used:
        union.add(pid)
    # Joined links read as one place, so they must say the same thing.
    key = {p: (names[p], float(risk.risk_points[p]), str(risk.risk_factors[p])) for p in used}
    for pids in by_node.values():
        for i, a in enumerate(pids):
            for b in pids[i + 1 :]:
                if key[a] == key[b]:
                    union.union(a, b)
    groups: defaultdict[str, list[str]] = defaultdict(list)
    for pid in used:
        groups[union.find(pid)].append(pid)
    named_at: defaultdict[str, set[str]] = defaultdict(set)
    for pid, (u, v) in ends.items():
        if names[pid] and not names[pid].startswith("unnamed"):
            named_at[u].add(names[pid])
            named_at[v].add(names[pid])
    rows = []
    for root, pids in groups.items():
        name, points, _ = key[pids[0]]
        degree: defaultdict[str, int] = defaultdict(int)
        for pid in pids:
            for node in set(ends[pid]):
                degree[node] += 1
        cross: list[str] = []
        for node in sorted(n for n, d in degree.items() if d == 1):
            other = sorted(named_at[node] - {name})
            if other and other[0] not in cross:
                cross.append(other[0])
        person_km = sum(flows[p] * lengths[p] for p in pids) / 1000.0
        busiest = max(sorted(pids), key=lambda p: flows[p])
        people = flows[busiest]
        rows.append(
            {
                "place_id": "E" + root, "label": section_label(name, cross), "people": people,
                "person_km": person_km, "risk_points": points,
                "risk_factors": str(risk.risk_factors[pids[0]]),
                "exposure": people * points,  # length is shown, not ranked on
                "people_by_destination": shares_text(
                    {d: f.get(busiest, 0.0) for d, f in sources.items()}
                ),
                "physical_edge_ids": ";".join(sorted(pids)),
                "geometry": unary_union([lines[p] for p in pids]),
            }
        )  # fmt: skip
    return ranked(pd.DataFrame(rows, columns=STREET_COLUMNS[1:]), STREET_COLUMNS, edges.crs,
                   counted, rule or FixFirst(), flow_noun)  # fmt: skip
