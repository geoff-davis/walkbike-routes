"""Near-best routes and a path-size logit split of each origin's trips.

People do not all take the single cheapest route.  For each origin the search
keeps a few near-best loopless routes to the destination, drops any that
mostly repeat a cheaper one, and shares the origin's weight among the rest:

    P(i) = PS_i^beta * exp(-(C_i - C_min) / theta) / sum_j (...)

or, for a model with log cost (``log_cost_exponent`` = lambda),

    P(i) = PS_i^beta * (C_i / C_min)^-lambda / sum_j (...)

where ``C`` is the cost the routes were found by, in seconds (generalized
cost, or length at the travel speed), ``theta`` the logit scale and
``PS_i`` the path size: each of route ``i``'s links counts for its share of
the route's length, divided by the number of routes using it (Ben-Akiva and
Bierlaire 1999), so a route sharing nothing has size 1.  Routes that
overlap heavily split one route's worth of probability between them, rather
than each counting in full.  With one route this is all-or-nothing.

The defaults, set by hand for a Los Altos downtown study: up to 3 routes from a
12-route search, within 60 s and 10% of the best, a 60 s logit scale and a
path-size exponent of 1.  Published route-choice models estimated with a
path-size logit (Broach, Dill and Gliebe 2012) supply their own values.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import networkx as nx
import pandas as pd

from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.models import TravelDirection
from walkbike_routes.routing.paths import multi_source_k_paths
from walkbike_routes.routing.routes import measure_path


@dataclass(frozen=True)
class ChoiceSettings:
    """How many routes each trip may split across, and how sharply."""

    routes: int = 3
    search_paths: int = 12
    maximum_cost_gap_s: float = 60.0
    maximum_cost_ratio: float = 1.10
    maximum_shared_length_ratio: float = 0.90
    logit_scale_s: float = 60.0
    path_size_exponent: float = 1.0
    # When set, routes compete on the ratio of their costs (a model with log
    # distance, such as Broach, Gliebe and Dill), not the difference.
    log_cost_exponent: float | None = None

    def __post_init__(self) -> None:
        if self.routes < 1 or self.search_paths < self.routes:
            raise AccessibilityError("route choice needs 1 <= routes <= search_paths")
        if self.maximum_cost_gap_s < 0 or self.maximum_cost_ratio < 1:
            raise AccessibilityError("route choice cost bounds must allow the best route")
        if not 0 < self.maximum_shared_length_ratio <= 1:
            raise AccessibilityError("maximum_shared_length_ratio must be in (0, 1]")
        if self.logit_scale_s <= 0 or self.path_size_exponent < 0:
            raise AccessibilityError("logit scale must be positive, path-size exponent >= 0")
        if self.log_cost_exponent is not None and not (
            math.isfinite(self.log_cost_exponent) and self.log_cost_exponent > 0
        ):
            raise AccessibilityError("log_cost_exponent must be finite and positive")


@dataclass(frozen=True)
class Alternative:
    """One measured route from a node to the destination."""

    access_point_id: str
    physical_edge_ids: tuple[str, ...]
    physical_length_m: float
    travel_time_s: float
    generalized_cost_s: float
    # What routes compete on, in seconds, when that is not the generalized
    # cost: a preset that routes by length competes on length.
    choice_cost_s: float | None = None

    @property
    def cost_s(self) -> float:
        """The cost this route competes on."""
        return self.generalized_cost_s if self.choice_cost_s is None else self.choice_cost_s


def shared_length_ratio(
    first: Alternative, second: Alternative, edge_lengths: Mapping[str, float]
) -> float:
    """Length the two routes share, over the shorter route's length."""
    shared = sum(
        edge_lengths.get(e, 0.0)
        for e in set(first.physical_edge_ids) & set(second.physical_edge_ids)
    )
    shorter = min(first.physical_length_m, second.physical_length_m)
    return shared / shorter if shorter > 0 else 1.0


def select_diverse(
    alternatives: tuple[Alternative, ...],
    *,
    routes: int,
    maximum_shared_length_ratio: float,
    edge_lengths: Mapping[str, float],
) -> tuple[Alternative, ...]:
    """Cheapest first, skipping any route that mostly repeats one already kept."""
    kept: list[Alternative] = []
    for alternative in sorted(alternatives, key=lambda a: a.cost_s):
        if any(
            shared_length_ratio(alternative, other, edge_lengths) > maximum_shared_length_ratio
            for other in kept
        ):
            continue
        kept.append(alternative)
        if len(kept) >= routes:
            break
    return tuple(kept)


def path_size_probabilities(
    alternatives: tuple[Alternative, ...],
    *,
    edge_lengths: Mapping[str, float],
    logit_scale_s: float,
    path_size_exponent: float,
    log_cost_exponent: float | None = None,
    access_cost_s: float = 0.0,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Each route's share of the trips, and its path size.

    ``access_cost_s``, the cost of reaching the network, is common to every
    route; it cancels in the difference form but not in the ratio form.
    """
    if not alternatives:
        return (), ()
    usage = Counter(e for a in alternatives for e in set(a.physical_edge_ids))
    sizes: list[float] = []
    for alternative in alternatives:
        unknown = sorted(set(alternative.physical_edge_ids) - edge_lengths.keys())
        if unknown:
            raise AccessibilityError(f"route uses a link with no known length: {unknown[0]!r}")
        if alternative.physical_length_m <= 0:
            sizes.append(1.0)
            continue
        size = sum(
            edge_lengths[e] / alternative.physical_length_m / usage[e]
            for e in set(alternative.physical_edge_ids)
        )
        sizes.append(min(max(size, 1e-9), 1.0))
    if not math.isfinite(access_cost_s):
        raise AccessibilityError("the cost of reaching the network must be finite")
    best = min(a.cost_s for a in alternatives)
    if log_cost_exponent is not None:
        base = best + access_cost_s
        # A cost of zero (the home at the entrance) is infinitely better than
        # any other in the ratio form: such routes share the trips.
        raw = [
            size**path_size_exponent
            * (((a.cost_s + access_cost_s) / base) ** -log_cost_exponent
               if base > 0 else float(a.cost_s + access_cost_s <= 0))
            for a, size in zip(alternatives, sizes, strict=True)
        ]  # fmt: skip
    else:
        raw = [
            size**path_size_exponent * math.exp(-(a.cost_s - best) / logit_scale_s)
            for a, size in zip(alternatives, sizes, strict=True)
        ]
    total = sum(raw)
    return tuple(value / total for value in raw), tuple(sizes)


def near_best_alternatives(
    graph: nx.Graph[str],
    access_points: Mapping[str, str],
    *,
    weight: str,
    settings: ChoiceSettings,
    nodes: set[str],
    weight_per_second: float = 1.0,
    place: Callable[[str], str] | None = None,
) -> dict[str, tuple[Alternative, ...]]:
    """The near-best routes from each of ``nodes`` to the access points, cheapest first.

    The cost gap in ``settings`` is in seconds; ``weight_per_second`` converts
    it to the search weight's units (a speed in m/s when routing by length).
    Routes found by length also compete on length, at that speed.
    Overlapping routes are thinned later, in :func:`split_routes`, once each
    origin's time cap has removed the routes it cannot use.  ``place`` is as
    for :func:`~walkbike_routes.routing.paths.multi_source_k_paths`.
    """
    by_length = weight == "physical_length_m"
    tree = multi_source_k_paths(
        graph,
        access_points,
        weight=weight,
        direction=TravelDirection.TO_DESTINATION,
        maximum_paths=settings.search_paths,
        maximum_cost_gap=settings.maximum_cost_gap_s * weight_per_second,
        maximum_cost_ratio=settings.maximum_cost_ratio,
        place=place,
    )
    result: dict[str, tuple[Alternative, ...]] = {}
    for node in sorted(nodes):
        measured = []
        for found in tree.alternatives_by_node.get(node, ()):
            path = list(found.path)
            if len(path) == 1:  # the origin is at the destination
                measured.append(Alternative(found.access_point_id, (), 0.0, 0.0, 0.0))
                continue
            m = measure_path(graph, path)
            measured.append(
                Alternative(
                    found.access_point_id,
                    m.physical_edge_ids,
                    m.physical_length_m,
                    m.travel_time_s,
                    m.generalized_cost_s,
                    m.physical_length_m / weight_per_second if by_length else None,
                )
            )
        result[node] = tuple(measured)
    return result


def bound_to_best(
    alternatives: Mapping[str, tuple[Alternative, ...]],
    best_cost_s: Mapping[str, float],
    *,
    settings: ChoiceSettings,
) -> dict[str, tuple[Alternative, ...]]:
    """The alternatives within the cost bounds of each node's best route.

    ``best_cost_s`` is in the cost the routes compete on: generalized seconds,
    or length at the travel speed for routes found by length.

    Needed when the best route can be one the near-best search cannot find:
    on a movement graph, the best route may go round a block, and the
    near-best search does not pass a node twice.  Nodes without a best cost
    keep their alternatives.
    """
    out: dict[str, tuple[Alternative, ...]] = {}
    for node, found in alternatives.items():
        best = best_cost_s.get(node)
        if best is None:
            out[node] = found
            continue
        limit = min(best + settings.maximum_cost_gap_s, best * settings.maximum_cost_ratio)
        out[node] = tuple(a for a in found if a.cost_s <= limit + 1e-9)
    return out


def split_routes(
    routes: pd.DataFrame,
    alternatives: Mapping[str, tuple[Alternative, ...]],
    *,
    settings: ChoiceSettings,
    edge_lengths: Mapping[str, float],
    length_speed_mps: float | None = None,
) -> pd.DataFrame:
    """One row per origin and route, with the share of its weight on that route.

    ``routes`` are the single-best routes from
    :func:`~walkbike_routes.analysis.calculate_accessibility`; an origin takes
    part if its best route is reachable within the time limit, and its other
    routes must fit the same limit.  With ``settings.routes == 1`` each origin
    keeps its single best route.  ``length_speed_mps`` is given when the
    routes were found by length: they then compete on length at that speed,
    as the alternatives from :func:`near_best_alternatives` do.
    """
    rows: list[dict[str, object]] = []
    eligible = routes[routes.route_reachable.astype(bool) & routes.within_time_limit.astype(bool)]
    for origin in eligible.itertuples(index=False):
        snap_s = float(str(origin.snap_travel_time_s))
        limit = origin.time_limit_min
        cap_s = math.inf if pd.isna(cast(Any, limit)) else float(str(limit)) * 60.0 + 1e-9
        best = Alternative(
            str(origin.access_point_id),
            tuple(e for e in str(origin.physical_edge_ids).split(";") if e),
            float(str(origin.physical_length_m)),
            float(str(origin.network_travel_time_s)),
            float(str(origin.generalized_cost_s)),
            None
            if length_speed_mps is None
            else float(str(origin.physical_length_m)) / length_speed_mps,
        )
        seen = {best.physical_edge_ids}
        others = []
        for a in alternatives.get(str(origin.attached_node_id), ()):
            if a.travel_time_s + snap_s > cap_s or a.physical_edge_ids in seen:
                continue  # over the limit, or the same streets again
            seen.add(a.physical_edge_ids)
            others.append(a)
        chosen = select_diverse(
            (best, *others),
            routes=settings.routes,
            maximum_shared_length_ratio=settings.maximum_shared_length_ratio,
            edge_lengths=edge_lengths,
        )
        if chosen[0] is not best:  # the search found the same cost by another path
            chosen = (best, *(a for a in chosen if a is not best))[: settings.routes]
        shares, sizes = path_size_probabilities(
            chosen,
            edge_lengths=edge_lengths,
            logit_scale_s=settings.logit_scale_s,
            path_size_exponent=settings.path_size_exponent,
            log_cost_exponent=settings.log_cost_exponent,
            access_cost_s=snap_s,
        )
        weight = float(str(origin.demand_weight))
        for rank, (alternative, share, size) in enumerate(
            zip(chosen, shares, sizes, strict=True), start=1
        ):
            rows.append(
                {
                    "origin_id": str(origin.origin_id),
                    "route_rank": rank,
                    "access_point_id": alternative.access_point_id,
                    "route_share": share,
                    "path_size": size,
                    "demand_weight": weight,
                    "assigned_weight": weight * share,
                    "physical_length_m": alternative.physical_length_m,
                    "total_travel_time_s": alternative.travel_time_s + snap_s,
                    "generalized_cost_s": alternative.generalized_cost_s,
                    "physical_edge_ids": ";".join(alternative.physical_edge_ids),
                }
            )
    columns = [
        "origin_id", "route_rank", "access_point_id", "route_share", "path_size",
        "demand_weight", "assigned_weight", "physical_length_m", "total_travel_time_s",
        "generalized_cost_s", "physical_edge_ids",
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=columns)
