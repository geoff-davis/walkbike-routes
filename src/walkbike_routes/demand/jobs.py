"""Jobs as destinations: clusters of workplaces, and gravity shares of each home's trips.

For flows across a whole network rather than to one destination, the
places people go are stood in for by where the jobs are.  The job points
(one per Census block with jobs, from LODES) are grouped into clusters by
job-weighted k-means, and each cluster becomes a destination whose pull is
its number of jobs.

Each home's trips are then shared across the clusters it can reach, in
proportion to jobs times the distance-decay factor of its route there (a
production-constrained gravity model)::

    trips[h, d] = people[h] * jobs[d] * decay[h, d] / sum over d' of jobs[d'] * decay[h, d']

so every home within reach of at least one cluster sends all its people
somewhere, and nearer and bigger clusters get more of them.  Jobs stand in
for every kind of trip end (shops, schools and services cluster where jobs
are); this is an assumption, not an estimate.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from walkbike_routes.exceptions import AccessibilityError

SEED = 0
ITERATIONS = 100


def cluster_jobs(
    jobs: gpd.GeoDataFrame,
    count: int,
    *,
    seed: int = SEED,
    iterations: int = ITERATIONS,
    placeable: pd.Series | None = None,
) -> gpd.GeoDataFrame:
    """Group job points into at most ``count`` clusters by job-weighted k-means.

    ``jobs`` has ``block_id``, ``jobs`` and point geometry in a projected CRS.
    Each cluster is placed at its member point nearest its job-weighted
    centre, so it sits on a real block near streets.  ``placeable`` (by row
    of ``jobs``) marks the points a cluster may be placed at, such as those
    near a walkable street; a cluster with none of them uses any member.  Returns
    ``destination_id`` (``jobs_001`` for the cluster with most jobs, and so
    on), ``name``, ``jobs``, ``blocks`` and the point.  The result depends
    only on the input and ``seed``.
    """
    if count < 1:
        raise AccessibilityError("job clusters: ask for at least one")
    if jobs.crs is None or not jobs.crs.is_projected:
        raise AccessibilityError("job clusters need points in a projected CRS")
    kept = jobs.assign(
        _placeable=True if placeable is None else placeable.reindex(jobs.index).fillna(False)
    )
    kept = kept[pd.to_numeric(kept.jobs, errors="coerce").fillna(0).gt(0)]
    kept = kept.sort_values("block_id").reset_index(drop=True)
    if kept.empty:
        raise AccessibilityError("job clusters: no jobs in the study area")
    xy = np.column_stack([kept.geometry.x.to_numpy(), kept.geometry.y.to_numpy()])
    weight = kept.jobs.to_numpy(dtype=float)
    ok = kept._placeable.to_numpy(dtype=bool)
    labels = _kmeans(xy, weight, min(count, len(kept)), seed=seed, iterations=iterations)
    rows = []
    for label in np.unique(labels):
        member = np.flatnonzero(labels == label)
        centre = np.average(xy[member], axis=0, weights=weight[member])
        spots = member[ok[member]] if ok[member].any() else member
        nearest = spots[int(np.argmin(((xy[spots] - centre) ** 2).sum(axis=1)))]
        rows.append(
            {
                "jobs": round(float(weight[member].sum())),
                "blocks": len(member),
                "x": float(xy[nearest, 0]),
                "y": float(xy[nearest, 1]),
            }
        )
    table = pd.DataFrame(rows).sort_values(["jobs", "x", "y"], ascending=[False, True, True])
    table = table.reset_index(drop=True)
    width = max(3, len(str(len(table))))
    table["destination_id"] = [f"jobs_{i + 1:0{width}d}" for i in range(len(table))]
    table["name"] = [f"Jobs cluster {i + 1} ({j:,} jobs)" for i, j in enumerate(table.jobs)]
    return gpd.GeoDataFrame(
        table[["destination_id", "name", "jobs", "blocks"]],
        geometry=gpd.points_from_xy(table.x, table.y),
        crs=jobs.crs,
    )


def _kmeans(
    xy: np.ndarray, weight: np.ndarray, count: int, *, seed: int, iterations: int
) -> np.ndarray:
    """Cluster labels from weighted Lloyd iterations after weighted k-means++ seeding."""
    rng = np.random.default_rng(seed)
    centres = np.empty((count, 2))
    centres[0] = xy[rng.choice(len(xy), p=weight / weight.sum())]
    nearest = ((xy - centres[0]) ** 2).sum(axis=1)
    for k in range(1, count):
        score = weight * nearest
        # Every point already sits on a centre (duplicates): repeat the heaviest
        # point, so fewer clusters than asked for come back.
        pick = (
            int(rng.choice(len(xy), p=score / score.sum()))
            if score.sum() > 0
            else int(np.argmax(weight))
        )
        centres[k] = xy[pick]
        nearest = np.minimum(nearest, ((xy - centres[k]) ** 2).sum(axis=1))
    labels = np.full(len(xy), -1)
    for _ in range(iterations):
        distance = ((xy[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        new = distance.argmin(axis=1)
        if np.array_equal(new, labels):
            break
        labels = new
        for k in range(count):
            member = labels == k
            if member.any():  # an empty cluster keeps its centre
                centres[k] = np.average(xy[member], axis=0, weights=weight[member])
    return labels


def gravity_trips(access: pd.DataFrame) -> pd.DataFrame:
    """Each home's people shared across the destinations it reaches.

    ``access`` has one row per home and destination within reach, with
    ``origin_id``, ``destination_id``, ``people`` (the home's weight, the
    same on each of its rows), ``attraction`` (the destination's jobs) and
    ``decay_factor``.  Returns those columns plus ``trips``, which sum to
    ``people`` over a home's rows, unless all its rows have zero attraction
    times decay, when they are all zero.
    """
    needed = {"origin_id", "destination_id", "people", "attraction", "decay_factor"}
    missing = sorted(needed - set(access))
    if missing:
        raise AccessibilityError(f"gravity trips: missing columns {missing}")
    out = access.copy()
    for name in ("people", "attraction", "decay_factor"):
        values = pd.to_numeric(out[name], errors="coerce")
        if values.isna().any() or not np.isfinite(values).all() or (values < 0).any():
            raise AccessibilityError(f"gravity trips: {name} must be finite and nonnegative")
        out[name] = values.astype(float)
    pull = out.attraction * out.decay_factor
    total = pull.groupby(out.origin_id).transform("sum")
    out["trips"] = np.where(total > 0, out.people * pull / total.where(total > 0, 1.0), 0.0)
    return out
