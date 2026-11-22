"""Improvement screens on the demo town's bike network."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import networkx as nx
import pandas as pd
import pytest

from walkbike_routes import demo
from walkbike_routes.analysis import (
    generalized_cost_reduction_screen,
)
from walkbike_routes.network import build_bicycle_links, prepare, routable_links_to_graph
from walkbike_routes.network.bicycle import BicycleProfile

SCHOOL = demo.node_id(9, 9)


@pytest.fixture(scope="module")
def setup() -> tuple[nx.Graph[str], pd.DataFrame, dict[str, tuple[str, ...]]]:
    town = demo.build()
    edges = prepare.physical_edges(town.links)
    links = build_bicycle_links(edges, BicycleProfile("cyclist"))
    graph = routable_links_to_graph(links, directed=True)
    nodes = sorted(set(graph.nodes) - {SCHOOL})
    origins = pd.DataFrame({"node_id": nodes, "weight": 1.0, "access_time_s": 0.0})
    candidates = {
        "main street": tuple(edges[edges.name.eq("Main Street")].physical_edge_id),
        "central avenue": tuple(edges[edges.name.eq("Central Avenue")].physical_edge_id),
    }
    return graph, origins, candidates


def _halved(attrs: Mapping[str, Any]) -> float:
    """A hypothetical treatment that halves the generalized cost."""
    return 0.5 * float(attrs["generalized_cost_s"])


def _screen(setup: Any, **kwargs: Any) -> pd.DataFrame:
    graph, origins, candidates = setup
    return generalized_cost_reduction_screen(
        graph,
        [SCHOOL],
        origins,
        candidates,
        _halved,
        cost_field="generalized_cost_s",
        cost_units_per_minute=60.0,
        **kwargs,
    )


def test_halving_a_street_s_cost_saves_minutes(setup: Any) -> None:
    assert _screen(setup).weighted_generalized_minutes_saved.gt(0).all()


def test_time_caps_hold_each_cohort_fixed(setup: Any) -> None:
    caps = {
        "physical_time_field": "travel_time_s",
        "origin_access_time_field": "access_time_s",
        "physical_time_limits_minutes": (2.0, 60.0),
    }
    result = _screen(setup, **caps)
    weight = result.groupby("physical_time_limit_minutes").analyzed_weight.first()
    _, origins, _ = setup
    assert weight[60.0] == len(origins) and 0 < weight[2.0] < weight[60.0]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"cost_units_per_minute": 0.0}, "cost units per minute"),
        ({"minute_thresholds": (0.0,)}, "minute thresholds"),
        ({"physical_time_limits_minutes": (-1.0,)}, "physical-time limits"),
        ({"physical_time_limits_minutes": (5.0,)}, "require edge and origin time fields"),
        ({"origin_access_time_field": "walk_s"}, "origins missing required columns"),
        ({"destination_nodes": ["nowhere"]}, "no destination node"),
    ],
)
def test_bad_screen_settings_are_refused(setup: Any, change: dict[str, Any], message: str) -> None:
    graph, origins, candidates = setup
    settings: dict[str, Any] = {
        "cost_field": "generalized_cost_s",
        "cost_units_per_minute": 60.0,
        **change,
    }
    destinations = settings.pop("destination_nodes", [SCHOOL])
    with pytest.raises(ValueError, match=message):
        generalized_cost_reduction_screen(
            graph, destinations, origins, candidates, _halved, **settings
        )


def test_a_treatment_may_not_raise_costs(setup: Any) -> None:
    graph, origins, candidates = setup
    with pytest.raises(ValueError, match="cannot increase"):
        generalized_cost_reduction_screen(
            graph,
            [SCHOOL],
            origins,
            candidates,
            lambda attrs: 2 * float(attrs["generalized_cost_s"]),
            cost_field="generalized_cost_s",
            cost_units_per_minute=60.0,
        )


def test_a_link_with_no_physical_edge_falls_back_to_its_edge_id() -> None:
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_edge("o", "d", edge_id="old", physical_edge_id=None, cost=60.0)
    origins = pd.DataFrame({"node_id": ["o"], "weight": [1.0]})
    result = generalized_cost_reduction_screen(
        graph,
        ["d"],
        origins,
        {"fix": ("old",)},
        lambda attrs: 0.0,
        cost_field="cost",
        cost_units_per_minute=60.0,
    )
    assert result.weighted_generalized_minutes_saved.tolist() == [1.0]
