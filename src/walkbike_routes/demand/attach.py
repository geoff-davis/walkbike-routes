"""Destination-neutral origin attachment to mode-network nodes."""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from walkbike_routes.exceptions import AccessibilityError
from walkbike_routes.models import Mode
from walkbike_routes.validation import require_metric_projected_crs

ATTACHMENT_COLUMNS = [
    "origin_id", "mode", "attached_node_id", "snap_distance_m", "network_attached",
    "attachment_policy", "attached_edge_id", "edge_distance_m", "edge_u", "edge_v",
    "to_u_m", "to_v_m",
]  # fmt: skip


def attach_origins_to_edges(
    origins: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    *,
    mode: Mode,
    maximum_snap_distance_m: float | None,
    attachment_policy: str,
) -> pd.DataFrame:
    """Attach each origin to the nearer end of its nearest usable street or path.

    The nearest node can lie on another street: a home 40 m from its own
    street, whose corners are 130 m away, would otherwise join a driveway
    or service road 110 m off that leads somewhere else.  Here the home
    joins the nearest edge in ``edges`` (``physical_edge_id``, ``u``, ``v``,
    line geometry), then follows it to whichever end is nearer along it.
    ``snap_distance_m`` is the distance to the edge plus that stretch along
    it; ``maximum_snap_distance_m`` limits only the distance to the edge, and
    ``None`` is no limit: every origin joins its nearest edge, however far.
    Ties go to the lower edge id, then the lower node id.

    ``attached_node_id`` and ``snap_distance_m`` give the nearer end.  Both
    are given too: the edge's ends (``edge_u``, ``edge_v``), the distance to
    the edge (``edge_distance_m``) and the distances along it to each end
    (``to_u_m``, ``to_v_m``; for a loop, back to its start and on round to
    its end), so a route search can start a home mid-block from whichever
    end is cheaper for where it is going, and allowed
    (:func:`walkbike_routes.routing.routes.best_end`).  Before routing, join
    on the street's cost per metre each way, ``forward_cost_per_m`` (from
    ``edge_u``) and ``backward_cost_per_m``, NaN where the mode may not go;
    the route search refuses street attachments without them.

    A street drawn in pieces that do not meet is measured in a straight line
    from where the home meets it to each end.
    """
    if maximum_snap_distance_m is not None and (
        not math.isfinite(maximum_snap_distance_m) or maximum_snap_distance_m < 0
    ):
        raise AccessibilityError("maximum_snap_distance_m must be nonnegative")
    frames = {"origins": origins, "edges": edges, "nodes": nodes}
    if any(f.crs is None or f.crs != origins.crs for f in frames.values()):
        raise AccessibilityError("origin and edge attachments require one shared declared CRS")
    for name, frame in frames.items():
        require_metric_projected_crs(frame, name)
    required = {
        "origins": {"origin_id", "geometry"},
        "edges": {"physical_edge_id", "u", "v", "geometry"},
        "nodes": {"node_id", "geometry"},
    }
    for name, fields in required.items():
        if missing := sorted(fields - set(frames[name].columns)):
            raise AccessibilityError(f"{name} are missing attachment fields: {missing}")
    if origins.origin_id.astype(str).duplicated().any():
        raise AccessibilityError("origin IDs must be unique before network attachment")
    where = dict(zip(nodes.node_id.astype(str), nodes.geometry, strict=True))
    usable = edges[edges.u.astype(str).isin(where) & edges.v.astype(str).isin(where)]
    edge_frame = gpd.GeoDataFrame(
        {
            "physical_edge_id": usable.physical_edge_id.astype(str).to_numpy(),
            "u": usable.u.astype(str).to_numpy(),
            "v": usable.v.astype(str).to_numpy(),
        },
        geometry=usable.geometry.to_numpy(),
        crs=usable.crs,
    )
    joined = gpd.sjoin_nearest(
        origins[["origin_id", "geometry"]].assign(origin_id=origins.origin_id.astype(str)),
        edge_frame,
        how="inner",
        max_distance=maximum_snap_distance_m or None,
        distance_col="edge_distance_m",
    )
    if maximum_snap_distance_m == 0:
        joined = joined[joined.edge_distance_m.le(0)]
    joined = joined.sort_values(["origin_id", "edge_distance_m", "physical_edge_id"])
    joined = joined.drop_duplicates("origin_id", keep="first")
    # A street drawn in pieces is joined up first, so distances along it run
    # end to end; one that still will not join is measured in a straight line.
    # A loop's pieces are joined only where they run the same way, so which
    # way round it is drawn (forward, for a one-way loop) is kept.
    drawn = edge_frame.geometry.to_numpy()[joined.index_right.to_numpy()]
    # A loop is any street whose two ends are at one point, one node or two.
    loop = np.array(
        [where[u].distance(where[v]) == 0 for u, v in zip(joined.u, joined.v, strict=True)],
        dtype=bool,
    )
    lines = shapely.line_merge(drawn)
    lines[loop] = shapely.line_merge(drawn[loop], directed=True)
    home = shapely.line_locate_point(lines, joined.geometry.to_numpy())
    at_u = shapely.line_locate_point(lines, [where[n] for n in joined.u])
    length = shapely.length(lines)
    single = shapely.get_type_id(lines) == shapely.GeometryType.LINESTRING
    landing = shapely.get_point(shapely.shortest_line(lines, joined.geometry.to_numpy()), 0)
    lookup: dict[str, tuple[str, str, str, float, float, float]] = {}
    for origin_id, pid, u, v, away, h, a, size, joined_up, land, line, ring in zip(
        joined.origin_id, joined.physical_edge_id, joined.u, joined.v,
        joined.edge_distance_m, home, at_u, length, single, landing, lines, loop, strict=True,
    ):  # fmt: skip
        if not size > 0:  # a zero-length piece: both ends are where it is
            to_u = to_v = 0.0
        elif not joined_up:
            to_u, to_v = float(land.distance(where[u])), float(land.distance(where[v]))
        elif ring:  # a loop: back to its start, or on round to its end, from the node
            to_u = (float(h) - float(a)) % float(size)
            to_v = float(size) - to_u if to_u > 0 else 0.0
        else:
            # Measured from the line's own ends; u is the end nearer its start.
            start = shapely.get_point(line, 0)
            u_first = start.distance(where[u]) <= start.distance(where[v])
            to_start, to_end = float(h), float(size) - float(h)
            to_u, to_v = (to_start, to_end) if u_first else (to_end, to_start)
        lookup[origin_id] = (pid, u, v, float(away), to_u, to_v)
    rows: list[dict[str, object]] = []
    for origin_id in origins.origin_id.astype(str):
        found = lookup.get(origin_id)
        if found is None:
            rows.append(
                {
                    "origin_id": origin_id, "mode": mode.value, "attached_node_id": None,
                    "snap_distance_m": math.nan, "network_attached": False,
                    "attachment_policy": attachment_policy, "attached_edge_id": None,
                    "edge_distance_m": math.nan, "edge_u": None, "edge_v": None,
                    "to_u_m": math.nan, "to_v_m": math.nan,
                }
            )  # fmt: skip
            continue
        pid, u, v, away, to_u, to_v = found
        near, along = (u, to_u) if (to_u, u) <= (to_v, v) else (v, to_v)
        rows.append(
            {
                "origin_id": origin_id,
                "mode": mode.value,
                "attached_node_id": near,
                "snap_distance_m": away + along,
                "network_attached": True,
                "attachment_policy": attachment_policy,
                "attached_edge_id": pid,
                "edge_distance_m": away,
                "edge_u": u,
                "edge_v": v,
                "to_u_m": to_u,
                "to_v_m": to_v,
            }
        )
    return pd.DataFrame(rows, columns=ATTACHMENT_COLUMNS)
