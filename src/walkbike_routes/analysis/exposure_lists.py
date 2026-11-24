"""What the two exposure lists share: their columns, the wording of factors, and ranking."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

import geopandas as gpd
import pandas as pd

from walkbike_routes.analysis.exposure_rules import (
    BAND_WORDS,
    FixFirst,
    busiest_edge,
    tidy,
    tier_of,
    tier_reason,
)

# Folder, beside each destination's, of the lists for all destinations added up.
ALL_DESTINATIONS = "all-destinations"


STREET_COLUMNS = [
    "rank", "place_id", "label", "people", "person_km", "risk_points", "risk_factors",
    "exposure", "tier", "tier_rank", "tier_reason", "people_by_destination", "physical_edge_ids", "geometry",
]  # fmt: skip


CROSSING_COLUMNS = [
    "rank", "place_id", "label", "people", "risk_points", "risk_factors",
    "exposure", "tier", "tier_rank", "tier_reason", "people_by_destination", "physical_edge_ids", "geometry",
]  # fmt: skip


# Plain words for each factor, so a reader can see why a place has its points.
TRAFFIC_WORDS = BAND_WORDS


def has_count(aadt: object) -> bool:
    """Whether a traffic count is usable, as ``traffic_band`` decides."""
    count = number_of(aadt)
    return math.isfinite(count) and count >= 0


def traffic_words(band: int, counted: bool) -> str:
    """The traffic band in words, saying when it is estimated from the road type."""
    words = TRAFFIC_WORDS[min(max(band, 0), 4)]
    return words if counted else f"{words} (estimated from the road type)"


def text_of(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def number_of(value: object) -> float:
    try:
        number = float(str(value)) if value is not None else math.nan
    except ValueError:
        return math.nan
    return number


def factors_of(parts: Iterable[tuple[str, float]]) -> tuple[float, str]:
    kept = [(name, pts) for name, pts in parts if pts]
    return tidy(sum(p for _, p in kept)), "; ".join(f"{n} (+{p:.12g})" for n, p in kept)


def ranked(
    table: pd.DataFrame,
    columns: list[str],
    crs: Any,
    counted: float,
    rule: FixFirst,
    flow: str = "people",
) -> gpd.GeoDataFrame:
    """Places with points and people, each with its tier and why, ranked: fix-first
    places first, then the rest, each by exposure, so fix-first places are numbered
    1, 2, 3 ... without gaps."""
    table = table[table.risk_points.gt(0) & table.people.gt(0)].reset_index(drop=True)
    # Compared at the precision of the bars: route weights 0.1 + 0.7 are 0.8 people.
    scored = [(tidy(p), tidy(n)) for p, n in zip(table.risk_points, table.people, strict=True)]
    busiest = busiest_edge((n for _, n in scored), rule.busiest_share)
    table["tier"] = [tier_of(p, n, counted, rule, busiest) for p, n in scored]
    table["tier_reason"] = [tier_reason(p, n, counted, rule, busiest, flow) for p, n in scored]
    table["_later"] = ~table.tier.astype(str).eq("fix first")
    table = table.sort_values(["_later", "exposure", "place_id"], ascending=[True, False, True])
    table = table.reset_index(drop=True)
    table["rank"] = range(1, len(table) + 1)
    table["tier_rank"] = table.groupby("_later").cumcount() + 1  # 1, 2 ... within its tier
    return gpd.GeoDataFrame(table.reindex(columns=columns), geometry="geometry", crs=crs)


def shares_text(people: Mapping[str, float]) -> str:
    """Each destination's people at a place, busiest first, as JSON; "" for one destination."""
    shown = sorted(((d, v) for d, v in people.items() if v > 0), key=lambda dv: (-dv[1], dv[0]))
    return json.dumps(dict(shown)) if shown else ""


def by_source(events: pd.DataFrame) -> str:
    if "source" not in events:
        return ""
    return shares_text({str(d): float(w) for d, w in events.groupby("source").weight.sum().items()})


def by_edge(flow: pd.Series) -> pd.Series:
    """A flow keyed by edge id as text, so edge 1 and edge "1" add up."""
    flow = flow.astype(float)
    return flow.groupby(flow.index.astype(str)).sum()


def counted_weight(shares: pd.DataFrame) -> float:
    """The people the lists' shares are of: the run's assigned weight."""
    return float(shares.assigned_weight.astype(float).sum()) if len(shares) else 0.0
