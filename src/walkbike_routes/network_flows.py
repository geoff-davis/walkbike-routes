"""Flows across a whole network: every home's trips shared among clusters of jobs.

A study's destinations give flows to one place each.  A street counter sees
trips to everywhere, so comparing with counts needs flows to many places at
once.  With ``network_flows`` in the config, a run also

1. groups the area's jobs (LODES, by block) into clusters and makes each a
   point destination (:func:`walkbike_routes.demand.jobs.cluster_jobs`);
2. for each walking and cycling preset, builds the network once and routes
   every home to every cluster exactly as for a study destination: the best
   route within the time cap, the decay factor at its length, and the split
   across near-best routes;
3. shares each home's people among the clusters it reaches, in proportion to
   jobs times decay (:func:`walkbike_routes.demand.jobs.gravity_trips`);
4. scales each route's share by those trips and adds them up on the streets.

The outputs go to ``<out>/network/``: ``destinations.parquet`` (the
clusters), ``<mode>-<preset>/flows.parquet`` and ``summary.csv``.  Like
every flow here, these show where trips would go, not how many happen.
"""

from __future__ import annotations

import logging
import multiprocessing
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import geopandas as gpd
import pandas as pd

from walkbike_routes import config as settings
from walkbike_routes import study as study_runs
from walkbike_routes.adapters import canonical_origins
from walkbike_routes.costs import presets
from walkbike_routes.demand.jobs import cluster_jobs, gravity_trips
from walkbike_routes.demo import Town
from walkbike_routes.network import prepare
from walkbike_routes.routing.flows import FlowTotals

log = logging.getLogger(__name__)

FOLDER = "network"
ACTIVE = ("walk", "bike")


@dataclass(frozen=True)
class _Job:
    """What a worker needs to route one cluster; the big frames come from :data:`_SHARED`."""

    destination: settings.Destination
    mode: settings.Mode
    preset: str
    folder: Path


# The context and network every cluster is routed on, set once per process.
_SHARED: dict[str, object] = {}


def analyze(study: settings.Study, town: Town, out: Path) -> list[Path]:
    """Route every home to every job cluster and write the network flows."""
    flows_settings = study.network_flows
    if flows_settings is None:
        return []
    if town.jobs is None or town.jobs.empty:
        raise settings.ConfigError("network_flows: the study area has no jobs")
    folder = out / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    jobs = town.jobs.to_crs(str(town.links.crs)).reset_index(drop=True)
    clusters = cluster_jobs(jobs, flows_settings.destinations, placeable=_near_streets(jobs, town))
    clusters["kind"] = "point"
    log.info("network flows: %d jobs in %d clusters", int(clusters.jobs.sum()), len(clusters))
    destinations = [
        settings.Destination(
            id=str(row.destination_id),
            name=str(row.name),
            kind="point",
            purpose=flows_settings.purpose,
        )
        for row in clusters.itertuples(index=False)
    ]
    homes = town.homes.rename(columns={"home_id": "origin_id"})
    column, unit = study_runs.weight_column(study.demand)
    if column not in homes:
        raise settings.ConfigError(f"network_flows: the homes have no {column!r} column")
    context = study_runs.Context(
        study=study,
        edges=prepare.physical_edges(town.links, town.nodes),
        nodes=prepare.nodes(town.nodes),
        origins=canonical_origins(homes, weight_field=column, weight_unit=unit),
        destinations=clusters,
        gates=town.access_points.iloc[:0],
        out=out,
    )
    written = [folder / "destinations.parquet"]
    clusters.to_parquet(written[0])
    attraction = dict(zip(clusters.destination_id.astype(str), clusters.jobs, strict=True))
    rows = []
    for mode in (m for m in study.modes if m in ACTIVE):
        for name in study.presets[mode]:
            run = folder / f"{mode}-{name}"
            path, row = _combine(context, destinations, attraction, mode, name, run)
            written.append(path)
            rows.append(row)
    summary = folder / "summary.csv"
    pd.DataFrame(rows).to_csv(summary, index=False)
    written.append(summary)
    return written


def _combine(
    context: study_runs.Context,
    destinations: list[settings.Destination],
    attraction: dict[str, int],
    mode: settings.Mode,
    name: str,
    run: Path,
) -> tuple[Path, dict[str, object]]:
    """Route one preset to every cluster, share the trips and add up the flows."""
    preset = presets.load(mode, name)
    log.info("network flows: %s (%s), building the network", mode, name)
    network = study_runs.mode_network(context, mode, preset)
    work = run / "by_destination"
    shutil.rmtree(work, ignore_errors=True)
    near = _near_network(context, network, destinations)
    skipped = [d for d in destinations if d.id not in near]
    if skipped:
        log.warning(
            "network flows: %s (%s): %d job clusters are more than %.0f m from the network "
            "and are left out: %s",
            mode, name, len(skipped), study_runs.SNAP_M, ", ".join(d.id for d in skipped),
        )  # fmt: skip
    jobs = [_Job(d, mode, name, work / d.id) for d in destinations if d.id in near]
    reach = _route_all(context, network, jobs, context.study.network_flows)
    access = pd.concat(reach, ignore_index=True) if reach else pd.DataFrame()
    if access.empty:
        raise settings.ConfigError(f"network_flows: no home reaches any job cluster by {mode}")
    access["attraction"] = access.destination_id.map(attraction).astype(float)
    trips = gravity_trips(access)
    totals = FlowTotals()
    for job in jobs:  # in cluster order, so the sums do not depend on which worker finished first
        weights = trips[trips.destination_id.eq(job.destination.id) & trips.trips.gt(0)]
        if weights.empty:
            continue
        shares = pd.read_parquet(
            job.folder / "route_shares.parquet",
            columns=["origin_id", "route_share", "physical_edge_ids"],
        )
        shares = shares.merge(weights[["origin_id", "trips"]], on="origin_id")
        totals.add(shares.assign(assigned_weight=shares.route_share * shares.trips))
    flows = totals.layer(context.edges)
    path = run / "flows.parquet"
    flows.to_parquet(path)
    shutil.rmtree(work, ignore_errors=True)
    people = context.origins.set_index("origin_id").demand_weight.astype(float)
    sent = trips.groupby("origin_id").trips.sum()
    row: dict[str, object] = {
        "mode": mode,
        "preset": name,
        "weight_unit": str(context.origins.weight_unit.iloc[0]) if len(people) else "",
        "destinations": len(destinations),
        "destinations_reached": int(trips[trips.trips.gt(0)].destination_id.nunique()),
        "jobs": int(sum(attraction.values())),
        "destinations_too_far": len(skipped),
        "jobs_too_far": int(sum(attraction[d.id] for d in skipped)),
        "total_weight": float(people.sum()),
        "assigned_weight": float(sent.sum()),
        "mean_destinations_per_origin": float(
            trips[trips.trips.gt(0)].groupby("origin_id").size().mean()
        )
        if trips.trips.gt(0).any()
        else 0.0,
        "time_cap_min": context.study.time_caps_min[mode],
        "decay_curve": _curve(context.study, destinations[0], mode),
    }
    return path, row


def _near_streets(jobs: gpd.GeoDataFrame, town: Town) -> pd.Series:
    """Job points within snapping distance of a node that walkers or cyclists can reach.

    The nodes are the ends of edges open to walking or cycling, with access
    decided as routing decides it (:func:`walkbike_routes.network.prepare.physical_edges`).
    """
    edges = prepare.physical_edges(town.links)
    allowed = edges.ped_allowed.fillna(False).astype(bool) | edges.bike_allowed.fillna(
        False
    ).astype(bool)
    ends = set(edges.u[allowed].astype(str)) | set(edges.v[allowed].astype(str))
    nodes = town.nodes[town.nodes.node_id.astype(str).isin(ends)]
    if nodes.empty:
        return pd.Series(False, index=jobs.index)
    nearest = gpd.sjoin_nearest(jobs[["geometry"]], nodes[["geometry"]], distance_col="distance_m")
    close = nearest.distance_m.groupby(level=0).min().le(study_runs.SNAP_M)
    return close.reindex(jobs.index, fill_value=False).astype(bool)


def _near_network(
    context: study_runs.Context,
    network: study_runs.ModeNetwork,
    destinations: list[settings.Destination],
) -> set[str]:
    """The clusters with a node of the mode's network close enough to end trips at."""
    if network.nodes.empty:
        return set()
    points = context.destinations.set_index(context.destinations.destination_id.astype(str))
    nearest = gpd.sjoin_nearest(
        points[["geometry"]], network.nodes[["geometry"]], distance_col="distance_m"
    )
    close = nearest[nearest.distance_m.le(study_runs.SNAP_M)].index
    return {d.id for d in destinations} & {str(i) for i in close}


def _curve(study: settings.Study, destination: settings.Destination, mode: str) -> str:
    return settings.decay_for(study, destination, mode) or settings.NO_DECAY


def _route_all(
    context: study_runs.Context,
    network: study_runs.ModeNetwork,
    jobs: list[_Job],
    flows_settings: settings.NetworkFlows | None,
) -> list[pd.DataFrame]:
    """Each cluster's homes within reach, routed serially or across worker processes.

    Workers are started fresh ("spawn": forking a process that runs threads
    can deadlock) and each is handed the context and network once.
    """
    workers = min(flows_settings.workers if flows_settings else 1, len(jobs))
    if workers > 1:
        pool = ProcessPoolExecutor(
            workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_share,
            initargs=(context, network),
        )
        with pool:
            return list(pool.map(_route_one, jobs))
    _share(context, network)
    try:
        return [_route_one(job) for job in jobs]
    finally:
        _SHARED.clear()


def _share(context: study_runs.Context, network: study_runs.ModeNetwork) -> None:
    _SHARED.update(context=context, network=network)


def _route_one(job: _Job) -> pd.DataFrame:
    """Route every home to one cluster; return the homes within reach and their decay."""
    context = _SHARED["context"]
    network = _SHARED["network"]
    assert isinstance(context, study_runs.Context)
    assert isinstance(network, study_runs.ModeNetwork)
    job.folder.mkdir(parents=True, exist_ok=True)
    preset = presets.load(job.mode, job.preset)
    study_runs.active_run(
        replace(context, out=job.folder),
        job.destination,
        job.mode,
        preset,
        job.folder,
        screen=False,
        network=network,
    )
    routes = pd.read_parquet(
        job.folder / "routes.parquet",
        columns=["origin_id", "demand_weight", "decay_factor", "route_reachable",
                 "within_time_limit"],
    )  # fmt: skip
    used = routes[routes.route_reachable.astype(bool) & routes.within_time_limit.astype(bool)]
    return pd.DataFrame(
        {
            "origin_id": used.origin_id.astype(str),
            "destination_id": job.destination.id,
            "people": used.demand_weight.astype(float),
            "decay_factor": used.decay_factor.astype(float),
        }
    )
