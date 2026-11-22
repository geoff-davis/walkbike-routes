"""Destination-neutral counterfactual intervention scoring."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

import networkx as nx
import numpy as np
import pandas as pd

InterventionCost = Callable[[Mapping[str, Any]], float]
EdgeIdentity = Callable[[str], str]
TransitionCost = Callable[[str, str, str], float]


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"nan", "none", "<na>"} else result


def _number(value: object) -> float:
    return float(str(value))


def _identity(value: str) -> str:
    return value


def _edge_key(attrs: Mapping[str, Any]) -> str:
    """What an intervention names a graph edge by: its ``intervention_key`` if it
    has one (a junction's movements), else its physical edge (``edge_id`` in
    older graphs)."""
    return (
        _text(attrs.get("intervention_key"))
        or _text(attrs.get("physical_edge_id"))
        or _text(attrs.get("edge_id"))
    )


def _graph_edge_ids(graph: nx.Graph[str], edge_identity: EdgeIdentity) -> frozenset[str]:
    return frozenset(edge_identity(_edge_key(attrs)) for _, _, attrs in graph.edges(data=True))


def _require_known(intervention_id: str, edge_ids: frozenset[str], known: frozenset[str]) -> None:
    """Refuse an intervention none of whose edges are in the graph: it would save nothing."""
    if edge_ids and not edge_ids & known:
        raise ValueError(f"intervention {intervention_id!r} names no edge in the graph")


def _toward_destinations(graph: nx.Graph[str]) -> nx.Graph[str]:
    if isinstance(graph, nx.DiGraph):
        return cast("nx.Graph[str]", graph.reverse(copy=False))
    return graph


def _dijkstra(
    graph: nx.Graph[str],
    sources: list[str],
    weight: Callable[[str, str, Mapping[str, Any]], float | None],
) -> tuple[dict[str, float], dict[str, list[str]]]:
    return cast(
        "tuple[dict[str, float], dict[str, list[str]]]",
        nx.multi_source_dijkstra(graph, sources, weight=weight),
    )


def _benefit_rows(
    intervention_id: str,
    eligible: pd.DataFrame,
    baseline_costs: np.ndarray,
    counterfactual_costs: np.ndarray,
    baseline_physical_times: np.ndarray,
    limits: tuple[float | None, ...],
    minute_thresholds: tuple[float, ...],
    cost_units_per_minute: float,
) -> list[dict[str, object]]:
    """Summarize one counterfactual with the common intervention metrics."""
    savings = np.maximum(0.0, baseline_costs - counterfactual_costs) / cost_units_per_minute
    all_weights = eligible.weight.to_numpy(float)
    rows: list[dict[str, object]] = []
    for limit in limits:
        cohort = (
            np.ones(len(eligible), dtype=bool)
            if limit is None
            else baseline_physical_times <= limit * 60.0 + 1e-9
        )
        weights = all_weights[cohort]
        cohort_savings = savings[cohort]
        affected = cohort_savings > 1e-9
        analyzed_weight = float(weights.sum())
        affected_weight = float(weights[affected].sum())
        weighted_saved = float(np.dot(weights, cohort_savings))
        row: dict[str, object] = {
            "intervention_id": intervention_id,
            "physical_time_limit_minutes": math.nan if limit is None else limit,
            "analyzed_weight": analyzed_weight,
            "benefiting_weight": affected_weight,
            "weighted_generalized_minutes_saved": weighted_saved,
            "mean_generalized_minutes_saved_all": (
                weighted_saved / analyzed_weight if analyzed_weight else 0.0
            ),
            "mean_generalized_minutes_saved_benefiting": (
                weighted_saved / affected_weight if affected_weight else 0.0
            ),
            "maximum_generalized_minutes_saved": (
                float(cohort_savings.max()) if len(cohort_savings) else 0.0
            ),
        }
        for threshold in minute_thresholds:
            token = str(threshold).replace(".", "p")
            row[f"weight_saving_at_least_{token}_minutes"] = float(
                weights[cohort_savings >= threshold - 1e-12].sum()
            )
        rows.append(row)
    return rows


def generalized_cost_reduction_screen(
    graph: nx.Graph[str],
    destination_nodes: Iterable[str],
    origins: pd.DataFrame,
    intervention_edges: Mapping[str, Iterable[str]],
    improved_cost: InterventionCost,
    *,
    cost_field: str = "route_cost_m",
    edge_identity: EdgeIdentity = _identity,
    cost_units_per_minute: float,
    minute_thresholds: tuple[float, ...] = (0.5, 1.0, 2.0),
    physical_time_field: str | None = None,
    origin_access_time_field: str | None = None,
    physical_time_limits_minutes: tuple[float, ...] = (),
    baseline_available_field: str | None = None,
) -> pd.DataFrame:
    """Measure demand-weighted generalized-cost reductions for interventions.

    Each intervention lowers the cost of its named physical edges according to
    ``improved_cost``. The full network is rerouted, so the benefit can come
    from making an existing route less burdensome or from making a different
    route preferable. ``origins`` must contain ``node_id`` and ``weight``.
    When ``baseline_available_field`` is supplied, links whose field is false
    are hidden from the baseline but become available when their physical edge
    belongs to the intervention. This permits a candidate sidewalk, crossing,
    or path to add a currently unroutable link without maintaining a second
    graph implementation.

    The returned minutes are generalized-cost equivalents, not necessarily
    elapsed clock minutes. Origin weights may be fractional demand proxies.
    When physical-time limits are supplied, each result is calculated over a
    fixed cohort whose baseline generalized-cost-optimal route (including its
    origin access time) fits within that elapsed-time limit. This holds the
    denominator constant across intervention candidates.
    """
    if cost_units_per_minute <= 0 or not math.isfinite(cost_units_per_minute):
        raise ValueError("cost units per minute must be positive and finite")
    if any(value <= 0 or not math.isfinite(value) for value in minute_thresholds):
        raise ValueError("minute thresholds must be positive and finite")
    if any(value <= 0 or not math.isfinite(value) for value in physical_time_limits_minutes):
        raise ValueError("physical-time limits must be positive and finite")
    if physical_time_limits_minutes and (
        physical_time_field is None or origin_access_time_field is None
    ):
        raise ValueError("physical-time caps require edge and origin time fields")
    required = {"node_id", "weight"}
    if origin_access_time_field is not None:
        required.add(origin_access_time_field)
    missing = required - set(origins.columns)
    if missing:
        raise ValueError(f"origins missing required columns: {sorted(missing)}")

    destinations = [str(node) for node in destination_nodes if str(node) in graph]
    if not destinations:
        raise ValueError("no destination node is present in the graph")
    search = _toward_destinations(graph)

    def baseline_weight(_u: str, _v: str, attrs: Mapping[str, Any]) -> float | None:
        if baseline_available_field is not None and not bool(
            attrs.get(baseline_available_field, False)
        ):
            return None
        value = float(attrs[cost_field])
        if value < 0 or not math.isfinite(value):
            raise ValueError(f"{cost_field} must be nonnegative and finite")
        return value

    baseline, baseline_paths = _dijkstra(search, destinations, baseline_weight)
    eligible = origins.copy()
    eligible["node_id"] = eligible.node_id.map(_text)
    eligible["weight"] = eligible.weight.map(_number)
    eligible = eligible[eligible.node_id.isin(baseline)].copy()
    baseline_costs = eligible.node_id.map(baseline).to_numpy(float)
    baseline_physical_times = np.zeros(len(eligible), dtype=float)
    if physical_time_limits_minutes:
        assert physical_time_field is not None
        assert origin_access_time_field is not None

        def path_physical_time(node: str) -> float:
            path = baseline_paths[node]
            total = 0.0
            for u, v in itertools.pairwise(path):
                value = float(search[u][v][physical_time_field])
                if value < 0 or not math.isfinite(value):
                    raise ValueError(f"{physical_time_field} must be nonnegative and finite")
                total += value
            return total

        access_times = eligible[origin_access_time_field].map(_number).to_numpy(float)
        if np.any(~np.isfinite(access_times)) or np.any(access_times < 0):
            raise ValueError("origin access times must be nonnegative and finite")
        baseline_physical_times = np.array(
            [path_physical_time(str(node)) for node in eligible.node_id],
            dtype=float,
        )
        baseline_physical_times += access_times

    limits: tuple[float | None, ...] = (
        tuple(sorted(set(physical_time_limits_minutes)))
        if physical_time_limits_minutes
        else (None,)
    )

    rows: list[dict[str, object]] = []
    known = _graph_edge_ids(graph, edge_identity)
    for intervention_id, raw_edge_ids in intervention_edges.items():
        edge_ids = frozenset(map(str, raw_edge_ids))
        _require_known(str(intervention_id), edge_ids, known)

        def counterfactual_weight(
            _u: str,
            _v: str,
            attrs: Mapping[str, Any],
            edge_ids: frozenset[str] = edge_ids,
        ) -> float | None:
            edge_id = edge_identity(_edge_key(attrs))
            baseline_value = baseline_weight(_u, _v, attrs)
            if edge_id not in edge_ids:
                return baseline_value
            value = float(improved_cost(attrs))
            if value < 0 or not math.isfinite(value):
                raise ValueError("improved intervention cost must be nonnegative and finite")
            if baseline_value is not None and value > baseline_value + 1e-9:
                raise ValueError("an intervention cannot increase generalized edge cost")
            return value if baseline_value is None else min(value, baseline_value)

        counterfactual = cast(
            "dict[str, float]",
            nx.multi_source_dijkstra_path_length(
                search,
                destinations,
                weight=counterfactual_weight,
            ),
        )
        new_costs = eligible.node_id.map(counterfactual).to_numpy(float)
        rows.extend(
            _benefit_rows(
                str(intervention_id),
                eligible,
                baseline_costs,
                new_costs,
                baseline_physical_times,
                limits,
                minute_thresholds,
                cost_units_per_minute,
            )
        )
    return pd.DataFrame(rows)
