"""Turn a tagged street table into the core's physical edges and nodes.

The input is one row per street segment with OpenStreetMap-style columns:
``highway``, ``lanes``, ``speed_mph``, ``bike_facility`` (``none``, ``lane``,
``track`` or ``path``) and ``sidewalk`` (``both``, ``left``, ``right``,
``separate``, ``none``, ``path``, ``unknown`` or ``local``, meaning a local
sidewalk layer shows one), plus ``u`` and ``v`` node ids.
Optional ``bike_allowed``, ``ped_allowed`` and ``car_allowed`` columns carry
access rules, and ``oneway`` (``yes``, ``-1`` or ``no``) and
``oneway_bicycle`` the one-way rules; without them every street is two-way
and the road class decides who may use it.
Optional ``movement_type`` (``along`` or ``across``), ``crossing`` and
``crossing_road`` mark signalised crossings and the road class they cross.
Nodes carry a ``control`` of ``signal``, ``stop`` or ``none``.  The demo town
is in this form, and :mod:`walkbike_routes.network.osm_streets` writes it.

Traffic volume is rarely known, so each segment gets a volume band inferred
from its road class, lanes and speed, and is marked as inferred.  An
optional ``traffic_aadt`` column carries a counted daily volume (see
:mod:`walkbike_routes.network.traffic`): it settles the one volume test in
the LTS criteria (see ``counted_traffic_band``), and presets that use
volumes directly read it first.

Each edge gets two traffic stress levels: ``lts_best`` by the simplified rules (``classify_lts``)
and ``lts_furth`` by Furth's criteria tables (see
:mod:`walkbike_routes.network.furth`), with ``lts_furth_defaults`` naming
what was assumed.  Optional columns ``lane_markings``, ``lanes_both_ways``, ``lanes_per_direction``, ``shoulder_width_m``,
``parking_sides``, ``bike_lane_parking``, ``bike_lane_reach_m``,
``bike_lane_width_m`` and ``width_m`` feed the latter.

Optional ``footway``, ``service``, ``surface``, ``crossed_road`` and
``crossing_marking`` columns feed Broach's walking model, which also reads
``beside_road`` and ``beside_name`` (the street a separately mapped sidewalk
runs beside, from :mod:`walkbike_routes.network.sidewalks`) and
``crossing_share``, both added here.
"""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd

from walkbike_routes.adapters import canonical_physical_edges
from walkbike_routes.attributes import NO_CARS, NO_CYCLING, NO_WALKING_OR_CYCLING
from walkbike_routes.network.bicycle import (
    classify_lts,
    counted_traffic_band,
    infer_traffic_band,
    parse_number,
)
from walkbike_routes.network.furth import classify_furth
from walkbike_routes.network.grade import link_grades
from walkbike_routes.network.pedestrian import pedestrian_status
from walkbike_routes.network.sidewalks import match_sidewalks, streets_with_separate_sidewalks

SIDEWALK_PRESENT = {"both", "left", "right", "separate", "yes"}
FACILITIES = {
    "none": "none",
    "lane": "lane",
    "track": "track",
    "path": "separated path",
    "boulevard": "bike boulevard",
}
CONTROLS = {"signal": "traffic_signals", "stop": "stop", "none": ""}


def physical_edges(
    links: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame | None = None
) -> gpd.GeoDataFrame:
    """The core's physical edges, with traffic stress and walking evidence.

    With ``nodes`` carrying ``elevation_m``, each edge gets a ``grade`` from
    ``u`` to ``v`` (see :mod:`walkbike_routes.network.grade`).
    """
    if links.crs is None or not links.crs.is_projected:
        raise ValueError("links must be in a projected CRS: lengths are read in its units")
    edges = links.copy()
    edges["edge_id"] = edges.link_id.astype(str)
    edges["length_m"] = edges.geometry.length
    if nodes is not None and "elevation_m" in nodes:
        heights = pd.Series(
            nodes.elevation_m.astype(float).to_numpy(), index=nodes.node_id.astype(str)
        )
        edges["grade"] = link_grades(
            edges.u, edges.v, edges.length_m, edges.get("structure"), heights
        )
    highway = edges.highway.astype(str)
    # Access tags, where the table has them, decide; otherwise the road class does.
    if "bike_allowed" not in edges:
        edges["bike_allowed"] = ~highway.isin(NO_CYCLING)
    if "ped_allowed" not in edges:
        edges["ped_allowed"] = ~highway.isin(NO_WALKING_OR_CYCLING)
    if "car_allowed" not in edges:
        edges["car_allowed"] = ~highway.isin(NO_CARS)
    edges["bike_allowed"] = edges.bike_allowed.astype(bool)
    edges["ped_allowed"] = edges.ped_allowed.astype(bool)
    edges["car_allowed"] = edges.car_allowed.astype(bool)
    if "oneway" not in edges:
        edges["oneway"] = "no"
    # Crossing columns are optional; links added by overrides or local layers lack them.
    for column, default in (("movement_type", "along"), ("crossing", ""), ("crossing_road", "")):
        if column in edges:
            edges[column] = edges[column].fillna(default).replace("", default).astype(str)
    edges["speed_mph"] = [parse_number(value) for value in edges.speed_mph]
    edges["lanes"] = [parse_number(value) for value in edges.lanes]
    if "traffic_aadt" in edges:
        edges["traffic_aadt"] = pd.to_numeric(edges.traffic_aadt, errors="coerce").astype(float)
    unknown_facilities = sorted(set(edges.bike_facility.astype(str)) - set(FACILITIES))
    if unknown_facilities:
        raise ValueError(f"unknown bike_facility {unknown_facilities[0]!r}")
    edges["bike_facility"] = edges.bike_facility.astype(str).map(FACILITIES)
    _infer_separate_sidewalks(edges)
    edges["sidewalk_state"] = edges.sidewalk.astype(str).map(_sidewalk_state)
    bands = [
        infer_traffic_band(h, parse_number(lanes), parse_number(speed))[0]
        for h, lanes, speed in zip(highway, edges.lanes, edges.speed_mph, strict=True)
    ]
    types = [None if band == "no_motor_traffic" else "inferred traffic band" for band in bands]
    if "traffic_aadt" in edges:
        # The criteria's volume test is for mixed traffic: bike-lane streets keep their band.
        mixed = edges.bike_facility.isin({"none", "bike boulevard"}).tolist()
        for i, count in enumerate(edges.traffic_aadt):
            usable = pd.notna(count) and math.isfinite(float(count)) and float(count) >= 0
            if usable and mixed[i] and bands[i] != "no_motor_traffic":
                bands[i] = counted_traffic_band(bands[i], float(count))
                types[i] = "counted AADT"
    edges["traffic_band"] = bands
    edges["traffic_volume_type"] = types
    rows = [
        {str(k): v for k, v in row.items()}
        for row in edges.drop(columns="geometry").to_dict("records")
    ]
    edges["lts_best"] = [classify_lts(row).lts for row in rows]
    furth = [classify_furth(row) for row in rows]
    edges["lts_furth"] = [result.lts for result in furth]
    edges["lts_furth_defaults"] = [", ".join(result.defaults) for result in furth]
    edges["pedestrian_status"] = [pedestrian_status(row)[0] for row in rows]
    _walk_model_columns(edges)
    return canonical_physical_edges(edges)


def _infer_separate_sidewalks(edges: gpd.GeoDataFrame) -> None:
    """Read a street lined by separately mapped sidewalks as ``sidewalk=separate``.

    OpenStreetMap often draws a street's sidewalks as their own footways and
    leaves the street's line untagged, or tagged ``sidewalk=no`` as was once
    the practice.  Without this, walking along such a street would count as
    walking without a sidewalk.  Tags from a local layer are left alone.
    ``sidewalk_inferred`` marks the streets changed.
    """
    edges["sidewalk_inferred"] = False
    if "footway" not in edges or edges.crs is None or not edges.crs.is_projected:
        return
    lined = streets_with_separate_sidewalks(edges)
    edges["sidewalk"] = edges.sidewalk.astype(object)  # a categorical one could not take "separate"
    open_to_change = (
        edges.sidewalk.fillna("unknown").astype(str).isin({"unknown", "none", "no", ""})
    )
    change = lined & open_to_change
    edges.loc[change, "sidewalk"] = "separate"
    edges.loc[change, "sidewalk_inferred"] = True


def _walk_model_columns(edges: gpd.GeoDataFrame) -> None:
    """``beside_road``, ``beside_name`` and ``crossing_share``, for Broach's
    walking model (:mod:`walkbike_routes.network.broach_walk`).

    A crossing drawn in pieces charges once: each piece carries its share of
    the crossing's length in ``crossing_share``.  Pieces of one OSM way, or
    meeting at a node on a street, are one crossing.  A signalised crossing
    link that names only ``crossing_road`` is a crossing of that road.
    """
    for column in ("footway", "service", "surface", "crossed_road", "crossing_marking"):
        if column in edges:
            edges[column] = edges[column].fillna("").astype(str)
    if "crossing_road" in edges:
        known = edges.crossed_road if "crossed_road" in edges else pd.Series("", index=edges.index)
        edges["crossed_road"] = known.where(
            known.ne(""), edges.crossing_road.fillna("").astype(str)
        )
    if "footway" in edges and edges.crs is not None and edges.crs.is_projected:
        beside = match_sidewalks(edges)
        edges["beside_road"] = beside.beside_road
        edges["beside_name"] = beside.beside_name
    if "crossed_road" in edges:
        edges["crossing_share"], roots = _crossing_shares(edges)
        # Which crossing each piece is part of, so a signal on one piece covers all.
        edges["crossing_group"] = ""
        edges.loc[roots.index, "crossing_group"] = roots.astype(str)
        if "crossing_marking" in edges and not roots.empty:
            # One crossing has one paint: unmarked if any piece says so, else marked if any does.
            marking = edges.crossing_marking[roots.index]
            unmarked = marking.eq("unmarked").groupby(roots).transform("any")
            marked = marking.eq("marked").groupby(roots).transform("any")
            edges.loc[roots.index, "crossing_marking"] = np.where(
                unmarked, "unmarked", np.where(marked, "marked", "")
            )


def _crossing_shares(edges: gpd.GeoDataFrame) -> tuple[pd.Series, pd.Series]:
    """Each piece's share of its crossing's length, and which crossing it is part of."""
    crossing = edges.crossed_road.ne("")
    pieces = list(edges.index[crossing])
    parent = {i: i for i in pieces}

    def find(i: object) -> object:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(group: list[object]) -> None:
        for other in group[1:]:
            parent[find(other)] = find(group[0])

    # The road classes meeting each node, so pieces join only on the road they cross.
    roads_at: dict[str, set[str]] = {}
    for u, v, highway, car in zip(
        edges.u.astype(str), edges.v.astype(str), edges.highway.astype(str),
        edges.car_allowed.astype(bool), strict=True,
    ):  # fmt: skip
        if car:
            roads_at.setdefault(u, set()).add(highway)
            roads_at.setdefault(v, set()).add(highway)
    by_key: dict[object, list[object]] = {}
    for i in pieces:
        for node in (str(edges.u[i]), str(edges.v[i])):
            if edges.crossed_road[i] in roads_at.get(node, set()):
                by_key.setdefault(("node", node, edges.crossed_road[i]), []).append(i)
        if "osm_way_id" in edges and pd.notna(edges.osm_way_id[i]):
            by_key.setdefault(("way", edges.osm_way_id[i]), []).append(i)
    for group in by_key.values():
        join(group)
    share = pd.Series(0.0, index=edges.index)
    roots = pd.Series({i: find(i) for i in pieces}, dtype=object)
    if roots.empty:
        return share, roots
    lengths = edges.length_m[roots.index]
    # A repeated row (dropped later as a duplicate) must not count twice.
    pieces_frame = pd.DataFrame(
        {"root": roots, "edge": edges.edge_id[roots.index].astype(str), "length": lengths}
    )
    totals = pieces_frame.drop_duplicates("edge").groupby("root").length.sum()
    total = roots.map(totals).astype(float)
    share[roots.index] = (lengths / total.where(total > 0)).fillna(1.0)
    return share, roots


# Node columns kept when present: a crosswalk's paint, whether a right turn
# on red is allowed at a signal, and a slow signal's extra wait.
NODE_OPTIONAL = ("crossing_marking", "right_on_red", "extra_wait_s")


def nodes(points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The core's nodes: signals are tagged the way OpenStreetMap tags them."""
    unknown = sorted(set(points.control.astype(str)) - set(CONTROLS))
    if unknown:
        raise ValueError(f"unknown control {unknown[0]!r}")
    optional = [c for c in NODE_OPTIONAL if c in points]
    result = points[["node_id", "control", *optional, "geometry"]].copy()
    for column in ("crossing_marking", "right_on_red"):
        if column in result:
            result[column] = result[column].fillna("").astype(str)
    result["node_id"] = result.node_id.astype(str)
    result["highway"] = result.control.astype(str).map(CONTROLS)
    return gpd.GeoDataFrame(result, geometry="geometry", crs=points.crs)


def _sidewalk_state(value: str) -> str | None:
    if value == "local":  # from a local sidewalk layer: see walkbike_routes.local_layers
        return "present_local"
    if value in SIDEWALK_PRESENT:
        return "present_osm"
    if value in {"none", "no"}:
        return "absent_osm_explicit"
    return None
