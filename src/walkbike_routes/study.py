"""Run a study's analyses: catchments, route shares, flows and overlap.

For every destination, mode and preset in the study this

1. builds the mode's network from the town's streets with the preset's costs;
2. attaches each home to its nearest node on that network;
3. finds the destination's entrances (configured gates, the nearest node to
   a point, or where links cross into an area);
4. walks and bikes: finds each home's best route within the time cap,
   weights the home by a distance-decay curve at its route length (unless the
   study turns decay off), then splits its trips across near-best routes with
   a path-size logit;
   cars: finds the fastest turn-aware route there and back, with no cap;
5. adds the trips on each street into a flow.

Walking and cycling flows count each home at its decay share: the share of
trips for that purpose, nationally, that are at least as long as the home's
route.  With decay off, every person within the time cap counts in full.
Car flows always count everyone.  Either way flows show where trips would go,
not how many happen.
With car and walking or cycling in one study, the overlap index marks where
their flows meet.
"""

from __future__ import annotations

import logging
import multiprocessing
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import geopandas as gpd
import pandas as pd

from walkbike_routes import config as settings
from walkbike_routes import layers
from walkbike_routes.adapters import canonical_origins
from walkbike_routes.analysis import exposure
from walkbike_routes.analysis.overlap import overlap
from walkbike_routes.costs import presets
from walkbike_routes.demo import Town
from walkbike_routes.network import (
    build_signal_wait_inventory,
    cars,
    prepare,
)
from walkbike_routes.network.motor import MotorPenaltyProfile
from walkbike_routes.report import text
from walkbike_routes.study_active import active_run
from walkbike_routes.study_car import car_network, car_run
from walkbike_routes.study_context import SNAP_M, Context, limit_folder, write
from walkbike_routes.study_nearest import nearest_origins
from walkbike_routes.study_networks import ModeNetwork, mode_network

log = logging.getLogger(__name__)


def weight_column(demand: settings.Demand) -> tuple[str, str]:
    """The homes column holding the demand weight, and its unit."""
    if demand.weight == "households":
        return "households", "households"
    if demand.min_age is None and demand.max_age is None:
        return "residents", "residents"
    low = demand.min_age or 0
    high = "plus" if demand.max_age is None else str(demand.max_age)
    unit = f"residents aged {low}+" if demand.max_age is None else f"residents aged {low}-{high}"
    return f"residents_{low}_{high}", unit


def origins_for(
    study: settings.Study, destination: settings.Destination, homes: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """The homes that make trips to ``destination``, weighted by its ages, within its area."""
    column, unit = weight_column(settings.demand_for(study, destination))
    if column not in homes:
        names = [str(c) for c in homes.columns]
        counted = ", ".join(sorted(c for c in names if c.startswith(("residents", "households"))))
        raise settings.ConfigError(
            f"{destination.id}: the homes have no count of {unit} (they have: {counted})"
            + ("; the demo town counts only ages 5 to 11" if study.area.source == "demo" else "")
        )
    limit = destination.outline is not None and destination.outline.limits
    if destination.serves is not None or limit:
        area = (
            layers.outline_shape(destination, str(homes.crs))
            if limit
            else layers.outline(cast(Path, destination.serves), str(homes.crs))
        )
        what = "its outline" if limit else "its serves area"
        homes = homes[homes.geometry.covered_by(area)]
        if homes.empty:
            raise settings.ConfigError(
                f"{destination.id}: no homes lie inside {what}; "
                "check the outline file and its coordinate system"
            )
        log.info("%s: %d homes inside %s", destination.id, len(homes), what)
    return canonical_origins(homes, weight_field=column, weight_unit=unit)


def analyze(study: settings.Study, town: Town, out: Path, *, workers: int = 1) -> list[Path]:
    """Run every destination, mode and preset; return the files written.

    With ``workers`` above 1, each preset's network is built once and its
    destinations are routed in that many forked processes, which share it.
    The results are the same as with one.
    """
    homes = town.homes.rename(columns={"home_id": "origin_id"})
    edges = prepare.physical_edges(town.links, town.nodes)
    shared = Context(
        study=study,
        edges=edges,
        nodes=prepare.nodes(town.nodes),
        origins=homes.iloc[:0],
        destinations=town.destinations,
        gates=town.access_points,
        out=out,
    )
    inventory = build_signal_wait_inventory(shared.nodes, shared.edges)
    shared = replace(shared, signals=inventory)
    signal_legs = frozenset(inventory.crossing_leg_edges)
    signal_nodes = frozenset(inventory.cluster_by_node)
    origins = nearest_origins(
        shared, study.destinations, [origins_for(study, d, homes) for d in study.destinations]
    )
    contexts = [replace(shared, origins=o) for o in origins]
    # Each preset's network is built once and used for every destination, then let go
    # before the next is built, so only one is held at a time.
    ran: dict[tuple[str, str, str], tuple[list[Path], list[dict[str, object]], pd.Series]] = {}
    for mode in study.modes:
        for index, name in enumerate(study.presets[mode]):
            network: Network | None = None
            pairs = list(zip(contexts, study.destinations, strict=True))
            # The first preset is the mode's main case: it feeds overlap and screens.
            # The first destination builds the network the rest reuse.
            first, rest = pairs[:1], pairs[1:]
            for context, destination in first:
                files, rows, flow, network = _run(
                    context, destination, mode, name, network, main=index == 0
                )
                ran[destination.id, mode, name] = (files, rows, flow)
            for (_, destination), result in zip(
                rest, _each(rest, mode, name, network, index == 0, workers), strict=True
            ):
                ran[destination.id, mode, name] = result
    written: list[Path] = []
    summary: list[dict[str, object]] = []
    # Keyed by mode and time limit (None for the run's own).
    together: dict[tuple[str, float | None], dict[str, exposure.RunFlows]] = defaultdict(dict)
    for context, destination in zip(contexts, study.destinations, strict=True):
        flows: dict[str, pd.Series] = {}
        for mode in study.modes:
            for index, name in enumerate(study.presets[mode]):
                files, rows, flow = ran[destination.id, mode, name]
                written.extend(files)
                summary.extend(rows)
                if index == 0:
                    flows[mode] = flow
        if "car" in flows and ({"walk", "bike"} & set(flows)):
            active = {m: f for m, f in flows.items() if m != "car"}
            layer = overlap(edges, flows["car"], active)
            path = out / destination.id / "overlap.parquet"
            layer.to_parquet(path)
            written.append(path)
            # Again at each shorter limit, from the walking and cycling trips cut there.
            for limit in study.shorter_limits_min:
                cut = {
                    m: pd.read_parquet(
                        limit_folder(
                            out
                            / destination.id
                            / f"{m}-{study.presets[cast(settings.Mode, m)][0]}",
                            limit,
                        )
                        / "flows.parquet"
                    )
                    .set_index("physical_edge_id")
                    .potential_flow
                    for m in active
                }
                path = limit_folder(out / destination.id, limit) / "overlap.parquet"
                path.parent.mkdir(exist_ok=True)
                overlap(edges, flows["car"], cut).to_parquet(path)
                written.append(path)
        files, runs = _exposure(context, destination, flows, signal_legs, signal_nodes)
        written.extend(files)
        for key, run in runs.items():
            together[key][destination.id] = run
    written.extend(_combined(shared, together, signal_legs, signal_nodes))
    path = out / "summary.csv"
    pd.DataFrame(summary).to_csv(path, index=False)
    written.append(path)
    return written


def _exposure(
    context: Context,
    destination: settings.Destination,
    flows: Mapping[str, pd.Series],
    signal_legs: frozenset[str],
    signal_nodes: frozenset[str],
) -> tuple[list[Path], dict[tuple[str, float | None], exposure.RunFlows]]:
    """Where each main walking and cycling run is exposed to traffic today.

    Also at each shorter time limit, from the routes split again there.
    """
    written: list[Path] = []
    runs: dict[tuple[str, float | None], exposure.RunFlows] = {}
    for mode in ("walk", "bike"):
        if mode not in flows:
            continue
        folder = context.out / destination.id / f"{mode}-{context.study.presets[mode][0]}"
        unit = context.origins.weight_unit.iloc[0] if len(context.origins) else ""
        weighted = bool(settings.decay_for(context.study, destination, mode))
        noun = text.flow_noun(text.unit_words(unit), weighted)
        cap = context.study.time_caps_min[mode]
        limits: list[float | None] = [
            None,
            *(m for m in context.study.shorter_limits_min if m < cap),
        ]
        for limit in limits:
            where = folder if limit is None else limit_folder(folder, limit)
            shares = pd.read_parquet(where / "route_shares.parquet")
            flow = (
                flows[mode]
                if limit is None
                else pd.read_parquet(where / "flows.parquet")
                .set_index("physical_edge_id")
                .potential_flow
            )
            streets, crossings = exposure.exposure(
                context.edges, context.nodes, flow, shares, mode,
                flows.get("car"), signal_legs, signal_nodes, context.study.exposure,
                _lts_column(context.study, mode), noun,
            )  # fmt: skip
            written += write(where, exposure_streets=streets, exposure_crossings=crossings)
            runs[mode, limit] = exposure.RunFlows(flow, shares, flows.get("car"), noun)
    return written, runs


def _lts_column(study: settings.Study, mode: str) -> str:
    """The stress levels the mode's main preset reads, so the lists agree with its routes."""
    if mode != "bike":
        return "lts_best"
    profile = presets.load(mode, study.presets[cast(settings.Mode, mode)][0]).profile()
    return str(getattr(profile, "lts_field", "lts_best"))


def _combined(
    context: Context,
    together: Mapping[tuple[str, float | None], Mapping[str, exposure.RunFlows]],
    signal_legs: frozenset[str],
    signal_nodes: frozenset[str],
) -> list[Path]:
    """The exposure lists with every destination's routes added up, when there are several.

    Written to ``all-destinations/<mode>-<preset>/``, and to its ``limit-<minutes>``
    folder for a shorter limit; each place says how many of its people come
    from each destination.
    """
    if any(d.id == exposure.ALL_DESTINATIONS for d in context.study.destinations):
        log.warning("a destination is called %r: lists for all destinations not written",
                    exposure.ALL_DESTINATIONS)  # fmt: skip
        return []
    written: list[Path] = []
    for (mode, limit), runs in together.items():
        if len(runs) < 2:
            continue
        folder = (
            context.out
            / exposure.ALL_DESTINATIONS
            / f"{mode}-{context.study.presets[cast(settings.Mode, mode)][0]}"
        )
        if limit is not None:
            folder = limit_folder(folder, limit)
        folder.mkdir(parents=True, exist_ok=True)
        streets, crossings = exposure.combined_exposure(
            context.edges, context.nodes, runs, mode, signal_legs, signal_nodes,
            context.study.exposure, _lts_column(context.study, mode),
        )  # fmt: skip
        written += write(folder, exposure_streets=streets, exposure_crossings=crossings)
    return written


type Network = ModeNetwork | cars.CarNetwork


# What forked workers read: set just before the pool starts, so they inherit it.
_SHARED: tuple[list[tuple[Context, settings.Destination]], settings.Mode, str, Network | None,
               bool] | None = None  # fmt: skip


def _each(
    pairs: list[tuple[Context, settings.Destination]],
    mode: settings.Mode,
    name: str,
    network: Network | None,
    main: bool,
    workers: int,
) -> list[tuple[list[Path], list[dict[str, object]], pd.Series]]:
    """Each destination's run on one network, in order: in ``workers`` forked processes if > 1."""
    global _SHARED
    if workers <= 1 or not pairs:
        return [_run(c, d, mode, name, network, main=main)[:3] for c, d in pairs]
    _SHARED = (pairs, mode, name, network, main)
    try:
        with multiprocessing.get_context("fork").Pool(min(workers, len(pairs))) as pool:
            return pool.map(_shared_run, range(len(pairs)), chunksize=1)
    finally:
        _SHARED = None


def _shared_run(i: int) -> tuple[list[Path], list[dict[str, object]], pd.Series]:
    assert _SHARED is not None
    pairs, mode, name, network, main = _SHARED
    context, destination = pairs[i]
    files, rows, flow, _ = _run(context, destination, mode, name, network, main=main)
    return files, rows, flow


def _run(
    context: Context,
    destination: settings.Destination,
    mode: settings.Mode,
    name: str,
    network: Network | None = None,
    *,
    main: bool,
) -> tuple[list[Path], list[dict[str, object]], pd.Series, Network]:
    """One run, and the network it used: pass that back in for the next destination."""
    preset = presets.load(mode, name)
    folder = context.out / destination.id / f"{mode}-{name}"
    folder.mkdir(parents=True, exist_ok=True)
    log.info("%s: %s by %s (%s)", context.study.study, destination.id, mode, name)
    if mode == "car":
        profile = preset.profile()
        assert isinstance(profile, MotorPenaltyProfile)
        assert network is None or isinstance(network, cars.CarNetwork)
        roads = network or car_network(context, profile)
        return *car_run(context, destination, name, profile, folder, roads), roads
    assert network is None or isinstance(network, ModeNetwork)
    built = mode_network(context, mode, preset, like=network)
    return *active_run(
        context, destination, mode, preset, folder, screen=main, network=built
    ), built


__all__ = [
    "SNAP_M", "Context", "ModeNetwork", "active_run", "analyze", "mode_network", "origins_for",
    "weight_column",
]  # fmt: skip
