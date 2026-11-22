"""Hand corrections to the street network, reviewed and kept in one file.

OpenStreetMap is sometimes wrong or out of date in ways that matter for one
study: a school's rear path open to cyclists but tagged as a footway, a
missing link to a gate, a bike lane not yet mapped.  A study's ``overrides``
file lists such corrections, each with an ``id``, a ``reason`` and a
``source`` (a document, a site visit), and one action:

``change``
    Set attributes on the selected links, for example
    ``{bike_allowed: true, bike_facility: path}``.
``remove``
    Drop the selected links.
``add``
    A new link along ``line`` (lon/lat points), whose two ends attach to the
    nearest street nodes within ``attach_m`` (default 15 m).
``signal``
    Describe a signal, on every signal node within ``near_m`` of ``near``:
    ``right_on_red`` (true or false), and ``extra_wait_s``, how much longer
    than an ordinary signal this one holds riders (a slow light).  Selecting
    no signal is an error.

Links are selected by OSM ``ways`` (every piece of each way), or by
``links`` (link ids such as ``w123-0``), optionally narrowed to those within
``near_m`` of a ``near`` point.  An override that selects nothing, or an
added link whose end has no node near enough, is an error: OSM has changed
since the override was written, and it needs another look.

Overrides are applied after local layers, so they have the last word.  Every
link they touch or add carries the override ids in ``override_id``, comma
separated.  A change whose values the selected links already have is logged
as a warning: OSM may have been fixed, and the override can be retired.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely.ops
import yaml
from shapely.geometry import LineString, Point

from walkbike_routes import config_values as checks
from walkbike_routes.config_values import ConfigError
from walkbike_routes.local_layers import SOURCES as LAYER_SOURCES

# The column that records where each street value came from; a correction names itself there.
SOURCE_COLUMNS = {**LAYER_SOURCES, "traffic_aadt": "traffic_aadt_source"}

ACTIONS = ("change", "remove", "add", "signal")
SIGNAL_SETTABLE = ("right_on_red", "extra_wait_s")
SETTABLE: dict[str, tuple[str, ...] | type] = {
    "name": str,
    "highway": str,
    "lanes": float,
    "speed_mph": float,
    "traffic_aadt": float,
    "bike_facility": ("none", "lane", "track", "path", "boulevard"),
    "sidewalk": ("both", "left", "right", "separate", "none", "path", "unknown", "local"),
    "oneway": ("no", "yes", "-1"),  # as in OSM: -1 is one way against the drawn direction
    "oneway_bicycle": ("no", "yes", "-1"),
    "bike_allowed": bool,
    "ped_allowed": bool,
    "car_allowed": bool,
}
log = logging.getLogger(__name__)
NEAR_M = 30.0
ATTACH_M = 15.0


@dataclass(frozen=True)
class Override:
    """One reviewed correction; see the module notes."""

    id: str
    reason: str
    source: str
    action: str
    ways: tuple[int, ...] = ()
    links: tuple[str, ...] = ()
    near: tuple[float, float] | None = None
    near_m: float = NEAR_M
    line: tuple[tuple[float, float], ...] = ()
    attach_m: float = ATTACH_M
    values: dict[str, Any] = field(default_factory=dict)


def load(path: Path) -> tuple[Override, ...]:
    """Read and check an overrides file."""
    if not path.is_file():
        raise ConfigError(f"overrides: no such file {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as err:
        raise ConfigError(f"overrides: not valid YAML: {err}") from err
    if not isinstance(raw, list):
        raise ConfigError("overrides: expected a list of corrections")
    found = tuple(_one(item, index) for index, item in enumerate(raw, start=1))
    ids = [o.id for o in found]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ConfigError(f"overrides: id {repeated[0]!r} is used twice")
    return found


def _one(raw: Any, index: int) -> Override:
    where = f"overrides[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a mapping")
    known = {"id", "reason", "source", *ACTIONS}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"{where}: unknown setting {unknown[0]!r}")
    for key in ("id", "reason", "source"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ConfigError(f"{where}: {key} is needed, as text")
    where = f"override {raw['id']!r}"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", raw["id"]):
        raise ConfigError(f"{where}: an id may use only letters, digits, _ and -")
    actions = [a for a in ACTIONS if a in raw]
    if len(actions) != 1:
        raise ConfigError(f"{where}: give exactly one of change, remove, add or signal")
    action = actions[0]
    body = raw[action]
    if not isinstance(body, dict):
        raise ConfigError(f"{where}: {action} expects a mapping")
    allowed = {
        "add": {"line", "attach_m", "set"},
        "remove": {"ways", "links", "near", "near_m"},
        "signal": {"near", "near_m", "set"},
    }
    keys = allowed.get(action, {"ways", "links", "near", "near_m", "set"})
    unknown = sorted(set(body) - keys)
    if unknown:
        raise ConfigError(f"{where}: {action} has no setting {unknown[0]!r}")
    common: dict[str, Any] = {
        "id": raw["id"], "reason": raw["reason"], "source": raw["source"], "action": action,
    }  # fmt: skip
    if action == "add":
        line = body.get("line")
        if not isinstance(line, list) or len(line) < 2:
            raise ConfigError(f"{where}: add needs a line of two or more [lon, lat] points")
        return Override(
            **common,
            line=tuple(checks.lonlat(p, where) for p in line),
            attach_m=checks.positive(body.get("attach_m", ATTACH_M), f"{where} attach_m"),
            values=_values(body.get("set", {}), where),
        )
    if action == "signal":
        return _signal(body, common, where)
    ways = body.get("ways", [])
    links = body.get("links", [])
    if not isinstance(ways, list) or not all(_whole(w) for w in ways):
        raise ConfigError(f"{where}: ways must be a list of OSM way numbers")
    if not isinstance(links, list) or not all(isinstance(x, str) for x in links):
        raise ConfigError(f"{where}: links must be a list of link ids")
    if not ways and not links:
        raise ConfigError(f"{where}: name the ways or links to {action}")
    values = _values(body.get("set"), where) if action == "change" else {}
    if action == "change" and not values:
        raise ConfigError(f"{where}: change needs set, with at least one value")
    return Override(
        **common,
        ways=tuple(int(w) for w in ways),
        links=tuple(links),
        near=None if body.get("near") is None else checks.lonlat(body["near"], where),
        near_m=checks.positive(body.get("near_m", NEAR_M), f"{where} near_m"),
        values=values,
    )


def _signal(body: dict[str, Any], common: dict[str, Any], where: str) -> Override:
    if body.get("near") is None:
        raise ConfigError(f"{where}: signal needs near, a [lon, lat] point")
    raw = body.get("set")
    if not isinstance(raw, Mapping) or not raw:
        raise ConfigError(f"{where}: signal needs set, with right_on_red or extra_wait_s")
    unknown = sorted(set(raw) - set(SIGNAL_SETTABLE))
    if unknown:
        raise ConfigError(
            f"{where}: cannot set {unknown[0]!r} on a signal; settable: "
            + ", ".join(SIGNAL_SETTABLE)
        )
    values: dict[str, Any] = {}
    if "right_on_red" in raw:
        if not isinstance(raw["right_on_red"], bool):
            raise ConfigError(f"{where}: right_on_red must be true or false")
        values["right_on_red"] = "yes" if raw["right_on_red"] else "no"
    if "extra_wait_s" in raw:
        values["extra_wait_s"] = checks.positive(raw["extra_wait_s"], f"{where} extra_wait_s")
    return Override(
        **common,
        near=checks.lonlat(body["near"], where),
        near_m=checks.positive(body.get("near_m", NEAR_M), f"{where} near_m"),
        values=values,
    )


def _values(raw: Any, where: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where}: set expects a mapping of street attributes")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        kind = SETTABLE.get(key)
        if key in {"oneway", "oneway_bicycle"} and not isinstance(value, str):
            value = {True: "yes", False: "no", -1: "-1"}.get(value, value)
        if kind is None:
            raise ConfigError(f"{where}: cannot set {key!r}; settable: {', '.join(SETTABLE)}")
        if isinstance(kind, tuple):
            if value not in kind:
                raise ConfigError(f"{where}: {key} {value!r} is not one of {', '.join(kind)}")
        elif kind is bool:
            if not isinstance(value, bool):
                raise ConfigError(f"{where}: {key} must be true or false")
        elif kind is float:
            value = checks.positive(value, f"{where} {key}")
        elif not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{where}: {key} must be text")
        values[key] = value
    return values


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def apply(
    links: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame, overrides: tuple[Override, ...]
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """The streets with every override applied, in order; nodes no link uses are dropped."""
    links, nodes = links.copy(), nodes.copy()
    if "override_id" not in links:
        links["override_id"] = pd.Series(None, index=links.index, dtype=object)
    for override in overrides:
        if override.action == "add":
            links, nodes = _add(links, nodes, override)
            continue
        if override.action == "signal":
            nodes = _set_signal(nodes, override)
            continue
        chosen = _select(links, override)
        if override.action == "remove":
            links = links.loc[~chosen]
            continue
        already = all(
            key in links and links.loc[chosen, key].eq(value).all()
            for key, value in override.values.items()
        )
        if already:
            log.warning("override %r changes nothing: OSM may be fixed; retire it", override.id)
        for key, value in override.values.items():
            if key not in links:
                links[key] = pd.Series(None, index=links.index, dtype=object)
            links[key] = links[key].astype(object)
            links.loc[chosen, key] = value
        links = _sources(links, chosen, override)
        links.loc[chosen, "override_id"] = _with_id(links.loc[chosen, "override_id"], override.id)
    used = set(links.u.astype(str)) | set(links.v.astype(str))
    return links, nodes[nodes.node_id.astype(str).isin(used)].copy()


def _set_signal(nodes: gpd.GeoDataFrame, override: Override) -> gpd.GeoDataFrame:
    """The nodes with ``override``'s values on each signal near its point."""
    assert override.near is not None  # checked on loading
    point = _project([override.near], nodes.crs)[0]
    chosen = nodes.control.astype(str).eq("signal") & (
        nodes.geometry.distance(point) <= override.near_m
    )
    if not chosen.any():
        raise ConfigError(
            f"override {override.id!r}: no signal lies within {override.near_m:g} m of near"
        )
    nodes = nodes.copy()
    for key in SIGNAL_SETTABLE:
        if key not in nodes:
            nodes[key] = pd.Series("" if key == "right_on_red" else np.nan, index=nodes.index)
        if key == "extra_wait_s":
            nodes[key] = pd.to_numeric(nodes[key], errors="coerce").astype(float)
        else:
            nodes[key] = nodes[key].fillna("").astype(str)
        if key in override.values:  # a value not given is left as it was
            nodes.loc[chosen, key] = override.values[key]
    return nodes


def _sources(links: gpd.GeoDataFrame, chosen: Any, override: Override) -> gpd.GeoDataFrame:
    """Credit the correction for each value it sets that has a source column."""
    for key in override.values:
        if (column := SOURCE_COLUMNS.get(key)) is None:
            continue
        if column not in links:
            links[column] = pd.Series(None, index=links.index, dtype=object)
        links[column] = links[column].astype(object)
        links.loc[chosen, column] = f"override {override.id}"
    return links


def _with_id(current: pd.Series, new: str) -> pd.Series:
    """Each link's override ids, comma separated, with ``new`` added."""
    return current.map(lambda old: new if pd.isna(old) or old == "" else f"{old},{new}")


def _select(links: gpd.GeoDataFrame, override: Override) -> pd.Series:
    where = f"override {override.id!r}"
    by_way = links.osm_way_id.isin(override.ways) if "osm_way_id" in links else False
    chosen = pd.Series(by_way, index=links.index) | links.link_id.astype(str).isin(override.links)
    missing: list[str] = [str(w) for w in override.ways if not (links.osm_way_id == w).any()]
    missing += [x for x in override.links if not links.link_id.astype(str).eq(x).any()]
    if missing:
        raise ConfigError(f"{where}: {missing[0]} is not in the street network; check OSM")
    if override.near is not None:
        point = _project([override.near], links.crs)[0]
        chosen &= links.geometry.distance(point) <= override.near_m
        if not chosen.any():
            raise ConfigError(
                f"{where}: nothing selected lies within {override.near_m:g} m of near"
            )
    return chosen


def _add(
    links: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame, override: Override
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    where = f"override {override.id!r}"
    points = _project(list(override.line), links.crs)
    ends = []
    for point in (points[0], points[-1]):
        distance = nodes.geometry.distance(point)
        if distance.empty or distance.min() > override.attach_m:
            raise ConfigError(
                f"{where}: no street node within {override.attach_m:g} m of the line's end"
            )
        ends.append(nodes.iloc[int(np.argmin(distance.to_numpy()))])
    if ends[0].node_id == ends[1].node_id:
        raise ConfigError(f"{where}: both ends attach to the same node")
    line = LineString([(p.x, p.y) for p in (ends[0].geometry, *points[1:-1], ends[1].geometry)])
    pair = {str(ends[0].node_id), str(ends[1].node_id)}
    parallel = any({str(u), str(v)} == pair for u, v in zip(links.u, links.v, strict=True))
    if parallel:
        # The routing graph keeps one link per pair of nodes, so a second link
        # between the same two is split at its middle, as OSM ways are.
        middle_id = f"override-{override.id}#middle"
        middle = line.interpolate(0.5, normalized=True)
        halves = [
            shapely.ops.substring(line, 0.0, 0.5, normalized=True),
            shapely.ops.substring(line, 0.5, 1.0, normalized=True),
        ]
        stops = [(ends[0].node_id, middle_id), (middle_id, ends[1].node_id)]
        extra = gpd.GeoDataFrame(
            {"node_id": [middle_id], "control": ["none"]}, geometry=[middle], crs=nodes.crs
        )
        nodes = gpd.GeoDataFrame(pd.concat([nodes, extra], ignore_index=True), crs=nodes.crs)
    else:
        halves, stops = [line], [(ends[0].node_id, ends[1].node_id)]
    rows = [
        _row(override, f"override-{override.id}" + (f"#{k}" if parallel else ""), u, v)
        for k, (u, v) in enumerate(stops)
    ]
    added = gpd.GeoDataFrame(rows, geometry=halves, crs=links.crs)
    return gpd.GeoDataFrame(pd.concat([links, added], ignore_index=True), crs=links.crs), nodes


def _row(override: Override, link_id: str, u: str, v: str) -> dict[str, Any]:
    return {
        "link_id": link_id,
        "u": u,
        "v": v,
        "osm_way_id": pd.NA,
        "highway": "path",
        "bike_facility": "none",
        "sidewalk": "unknown",
        "oneway": "no",
        "bike_allowed": True,
        "ped_allowed": True,
        "car_allowed": False,
        **override.values,
        **{
            column: f"override {override.id}"
            for key, column in SOURCE_COLUMNS.items()
            if key in override.values
        },
        "override_id": override.id,
    }


def _project(lonlat: list[tuple[float, float]], crs: Any) -> list[Point]:
    series = gpd.GeoSeries([Point(p) for p in lonlat], crs=4326).to_crs(crs)
    return list(np.asarray(series.to_numpy()))
