"""Where people walking or cycling to a place are exposed to traffic today.

The improvement screen asks where a change would save the most; it finds
barrier roads that few use today.  This asks the other question, after the
systemic safety approach (FHWA-SA-13-019; NCHRP Report 893) and Safe Routes
to School practice: where do routes run today, and how risky is each place?

Each place gets risk points from features those guides and the level of
traffic stress (Mekuria, Furth & Nixon 2012) treat as risk factors.  The
points themselves are ours, hand-set, and listed below so they can be read
and argued with.  Two lists come out, ranked separately:

- streets: the flow along each same-street run × its points, summed over
  its length (person-kilometres × points);
- crossings: the people whose routes cross a road at each place × its points.

The flows are modelled potential, not counts, and the points are not crash
rates.  It is a screen for where to look, not a risk estimate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import geopandas as gpd
import pandas as pd

from walkbike_routes.analysis.exposure_crossings import crossing_events, exposed_crossings
from walkbike_routes.analysis.exposure_lists import (
    ALL_DESTINATIONS,
    CROSSING_COLUMNS,
    STREET_COLUMNS,
    by_edge,
    counted_weight,
)
from walkbike_routes.analysis.exposure_rules import (
    ExposureRules,
)
from walkbike_routes.analysis.exposure_streets import exposed_streets, street_risk


def exposure(
    edges: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    flow: pd.Series,
    shares: pd.DataFrame,
    mode: str,
    car_flow: pd.Series | None = None,
    signal_legs: frozenset[str] = frozenset(),
    signal_nodes: frozenset[str] = frozenset(),
    rules: ExposureRules | None = None,
    lts_column: str = "lts_best",
    flow_noun: str = "people",
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """The ranked streets and crossings for one walking or cycling run.

    ``flow_noun`` is what the flows count, for each place's sentence: "people",
    the study's own unit, or "expected trips" when homes are weighted by distance.
    """
    rules = rules or ExposureRules()
    counted = counted_weight(shares)
    risk = street_risk(edges, mode, car_flow, rules.points, lts_column)
    streets = exposed_streets(edges, flow, risk, counted, rule=rules.fix_first,
                              flow_noun=flow_noun)  # fmt: skip
    events = crossing_events(shares, edges, nodes)
    crossings = exposed_crossings(events, edges, nodes, mode, counted, signal_legs, signal_nodes,
                                  rules, flow_noun)  # fmt: skip
    return streets, crossings


@dataclass(frozen=True)
class RunFlows:
    """One destination's main walking or cycling run, as :func:`combined_exposure` takes it."""

    flow: pd.Series
    shares: pd.DataFrame
    car_flow: pd.Series | None = None
    flow_noun: str = "people"  # what the flow counts: the unit, or "expected trips"


def combined_exposure(
    edges: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    runs: Mapping[str, RunFlows],
    mode: str,
    signal_legs: frozenset[str] = frozenset(),
    signal_nodes: frozenset[str] = frozenset(),
    rules: ExposureRules | None = None,
    lts_column: str = "lts_best",
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """The ranked streets and crossings for several destinations together.

    Each place's people are the sum over the destinations (``runs``, keyed by
    destination id), its tier is judged on that sum against all those
    counted, and ``people_by_destination`` says how many come from each.
    Drop-off traffic is the destinations' car flows added up.
    """
    if not runs:
        return exposure(edges, nodes, pd.Series(dtype=float), pd.DataFrame(columns=[
            "physical_edge_ids", "assigned_weight"]), mode, rules=rules, lts_column=lts_column)  # fmt: skip
    flows = {d: by_edge(r.flow) for d, r in runs.items()}
    total = pd.concat(list(flows.values()), axis=1).fillna(0.0).sum(axis=1)
    cars = [by_edge(r.car_flow) for r in runs.values() if r.car_flow is not None]
    car = pd.concat(cars, axis=1).fillna(0.0).sum(axis=1) if cars else None
    shares = pd.concat([r.shares.assign(source=d) for d, r in runs.items()], ignore_index=True)
    rules = rules or ExposureRules()
    counted = counted_weight(shares)
    # One word for the sum: the destinations' own when they agree, else "people".
    nouns = {r.flow_noun for r in runs.values()}
    flow_noun = nouns.pop() if len(nouns) == 1 else "people"
    risk = street_risk(edges, mode, car, rules.points, lts_column)
    streets = exposed_streets(edges, total, risk, counted, flows, rules.fix_first, flow_noun)
    events = crossing_events(shares, edges, nodes)
    # A route is its row in ``shares``, so its destination is that row's.
    events["source"] = shares.source.to_numpy()[events.route.to_numpy(dtype=int)]
    crossings = exposed_crossings(events, edges, nodes, mode, counted, signal_legs, signal_nodes,
                                  rules, flow_noun)  # fmt: skip
    return streets, crossings


__all__ = [
    "ALL_DESTINATIONS",
    "CROSSING_COLUMNS",
    "STREET_COLUMNS",
    "RunFlows",
    "combined_exposure",
    "counted_weight",
    "crossing_events",
    "exposed_crossings",
    "exposed_streets",
    "exposure",
    "street_risk",
]
