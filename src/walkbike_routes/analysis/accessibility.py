"""Origin and node accessibility calculations, independent of flow assignment."""

from __future__ import annotations

import math
from dataclasses import dataclass

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

from walkbike_routes.destinations import access_point_node_map, select_access_points
from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.models import Mode, TravelDirection
from walkbike_routes.routing import PathTree, measure_path, multi_source_paths, trace_routes

SUMMARY_KEYS = (
    "study_id",
    "destination_id",
    "profile_id",
    "scenario_id",
    "mode",
    "direction",
    "weight_unit",
)


@dataclass(frozen=True)
class AccessibilityResult:
    """Pure in-memory outputs for one destination/profile/scenario run."""

    routes: pd.DataFrame
    node_accessibility: pd.DataFrame
    summary: pd.DataFrame
    access_point_usage: pd.DataFrame
    unreachable_origins: pd.DataFrame


def prepare_accessibility_origins(origins: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Supply transparent equal weights when a study has no demand model."""
    result = origins.copy()
    if "demand_weight" not in result:
        result["demand_weight"] = 1.0
        result["weight_basis"] = "equal origin count; no demand model"
    else:
        result["demand_weight"] = pd.to_numeric(result.demand_weight, errors="raise")
        result["weight_basis"] = result.get("weight_basis", "supplied demand weight")
    if "weight_unit" not in result:
        result["weight_unit"] = "origin_count"
    if (
        result.demand_weight.isna().any()
        or (~np.isfinite(result.demand_weight)).any()
        or result.demand_weight.lt(0).any()
    ):
        raise AccessibilityError("origin demand weights must be finite and nonnegative")
    return gpd.GeoDataFrame(result, geometry="geometry", crs=origins.crs)


def _thresholds(thresholds_min: tuple[float, ...]) -> tuple[float, ...]:
    values = tuple(sorted({float(value) for value in thresholds_min}))
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        raise AccessibilityError("accessibility thresholds must be positive")
    return values


def _time_band(seconds: float, thresholds_min: tuple[float, ...]) -> str:
    for threshold in thresholds_min:
        if seconds <= threshold * 60.0 + 1e-9:
            return f"0-{threshold:g} min"
    return f">{thresholds_min[-1]:g} min"


def node_accessibility(
    graph: nx.Graph[str],
    tree: PathTree,
    *,
    thresholds_min: tuple[float, ...],
) -> pd.DataFrame:
    """Measure travel time and selected access point for every graph node."""
    thresholds = _thresholds(thresholds_min)
    rows: list[dict[str, object]] = []
    for node_id in sorted(graph, key=str):
        node = str(node_id)
        if graph.nodes[node_id].get("virtual"):  # a movement graph's states and sinks
            continue
        path = tree.path_by_node.get(node)
        if path is None:
            rows.append(
                {
                    "node_id": node,
                    "route_reachable": False,
                    "access_point_id": pd.NA,
                    "physical_length_m": float("nan"),
                    "travel_time_s": float("nan"),
                    "generalized_cost_s": float("nan"),
                    "travel_time_band": "unreachable",
                }
            )
            continue
        measures = measure_path(graph, path)
        rows.append(
            {
                "node_id": node,
                "route_reachable": True,
                "access_point_id": tree.access_point_by_node[node],
                "physical_length_m": measures.physical_length_m,
                "travel_time_s": measures.travel_time_s,
                "generalized_cost_s": measures.generalized_cost_s,
                "travel_time_band": _time_band(measures.travel_time_s, thresholds),
            }
        )
    return pd.DataFrame(rows)


def summarize_accessibility(
    routes: pd.DataFrame,
    *,
    thresholds_min: tuple[float, ...],
) -> pd.DataFrame:
    """Calculate count- and weight-based reachable totals at arbitrary thresholds."""
    thresholds = _thresholds(thresholds_min)
    missing = sorted(
        {*SUMMARY_KEYS, "origin_id", "demand_weight", "total_travel_time_s"} - set(routes)
    )
    if missing:
        raise AccessibilityError(f"routes missing accessibility fields: {missing}")
    rows: list[dict[str, object]] = []
    for keys, group in routes.groupby(list(SUMMARY_KEYS), dropna=False, sort=True):
        identifiers = dict(zip(SUMMARY_KEYS, keys, strict=True))
        total_count = int(group.origin_id.nunique())
        total_weight = float(group.demand_weight.sum())
        for threshold in thresholds:
            reachable = group[
                group.route_reachable.astype(bool)
                & group.total_travel_time_s.le(threshold * 60.0 + 1e-9)
            ]
            reachable_count = int(reachable.origin_id.nunique())
            reachable_weight = float(reachable.demand_weight.sum())
            rows.append(
                {
                    **identifiers,
                    "threshold_min": threshold,
                    "total_origin_count": total_count,
                    "reachable_origin_count": reachable_count,
                    "reachable_origin_share": reachable_count / total_count if total_count else 0.0,
                    "total_demand_weight": total_weight,
                    "reachable_demand_weight": reachable_weight,
                    "reachable_weight_share": reachable_weight / total_weight
                    if total_weight
                    else 0.0,
                    "not_observed_travel": True,
                }
            )
    return pd.DataFrame(rows)


def summarize_access_point_usage(routes: pd.DataFrame) -> pd.DataFrame:
    """Report which access threshold each reachable origin selects."""
    reachable = routes[routes.route_reachable.astype(bool) & routes.access_point_id.notna()].copy()
    keys = [*SUMMARY_KEYS, "access_point_id"]
    if reachable.empty:
        return pd.DataFrame(
            columns=[*keys, "selected_origin_count", "selected_demand_weight", "selection_share"]
        )
    grouped = (
        reachable.groupby(keys, dropna=False, sort=True)
        .agg(
            selected_origin_count=("origin_id", "nunique"),
            selected_demand_weight=("demand_weight", "sum"),
        )
        .reset_index()
    )
    totals = grouped.groupby(list(SUMMARY_KEYS)).selected_origin_count.transform("sum")
    grouped["selection_share"] = grouped.selected_origin_count / totals
    return grouped


def calculate_accessibility(
    graph: nx.Graph[str],
    origins: gpd.GeoDataFrame,
    attachments: pd.DataFrame,
    access_points: gpd.GeoDataFrame,
    *,
    study_id: str,
    destination_id: str,
    profile_id: str,
    scenario_id: str,
    mode: Mode,
    direction: TravelDirection,
    route_weight: str,
    thresholds_min: tuple[float, ...],
    time_limit_min: float | None = None,
    snap_speed_mps: float,
) -> AccessibilityResult:
    """Route origins and summarize accessibility without assigning potential flow."""
    prepared_origins = prepare_accessibility_origins(origins)
    selected_access = select_access_points(
        access_points,
        destination_id=destination_id,
        mode=mode,
        direction=direction,
    )
    tree = multi_source_paths(
        graph,
        access_point_node_map(selected_access),
        weight=route_weight,
        direction=direction,
    )
    routes = trace_routes(
        graph,
        tree,
        prepared_origins,
        attachments,
        study_id=study_id,
        destination_id=destination_id,
        profile_id=profile_id,
        scenario_id=scenario_id,
        mode=mode,
        time_limit_min=time_limit_min,
        snap_speed_mps=snap_speed_mps,
    )
    thresholds = _thresholds(thresholds_min)
    routes["travel_time_band"] = [
        _time_band(float(value), thresholds) if bool(reachable) else "unreachable"
        for value, reachable in zip(
            routes.total_travel_time_s,
            routes.route_reachable,
            strict=True,
        )
    ]
    summary = summarize_accessibility(routes, thresholds_min=thresholds)
    usage = summarize_access_point_usage(routes)
    unreachable = routes[~routes.route_reachable.astype(bool)].copy()
    return AccessibilityResult(
        routes=routes,
        node_accessibility=node_accessibility(graph, tree, thresholds_min=thresholds),
        summary=summary,
        access_point_usage=usage,
        unreachable_origins=unreachable,
    )
