"""Read a study's settings from a YAML file.

A study names an area, one or more destinations, the modes to route, the
route-choice presets for each mode and who the trips start from.  Unknown and
missing keys are errors, so a misspelt setting never passes silently.  Files
named in the config are read relative to the config file.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import yaml
from pyproj import CRS
from pyproj.exceptions import CRSError

from walkbike_routes import config_values as checks
from walkbike_routes.analysis.exposure_rules import ExposureRules, rules_from
from walkbike_routes.choice import ChoiceSettings
from walkbike_routes.config_models import (
    AREA_SOURCES,
    BUILDING_SOURCES,
    DESTINATION_KINDS,
    FACILITY_VALUES,
    LAYER_KINDS,
    NO_DECAY,
    PURPOSES,
    RESERVED_IDS,
    TRAFFIC_SOURCES,
    WEIGHTS,
    Area,
    Demand,
    Destination,
    LocalLayer,
    LonLat,
    Mode,
    NetworkFlows,
    Outline,
    Study,
)
from walkbike_routes.config_values import ConfigError
from walkbike_routes.exceptions import AccessibilityError

MODES: tuple[Mode, ...] = ("walk", "bike", "car")


LOCAL_SUFFIX = ".local.yaml"


def local_file(path: Path) -> Path | None:
    """Where a config's private local layers live: ``x.yaml`` -> ``x.local.yaml``.

    That file holds only ``local_layers``, ``outlines`` (destination id to
    an outline drawn on its map), ``crash_forecast`` (a file of forecast
    crashes at intersections) and ``recorded_crashes`` (police crash
    records), the last two drawn as map layers. It is kept out of git, so a city's own data
    can be used on every run without being committed with the config.
    """
    if path.name.endswith(LOCAL_SUFFIX):
        return None
    stem = path.name.removesuffix(".yaml").removesuffix(".yml")
    return path.with_name(stem + LOCAL_SUFFIX)


def load(path: str | Path, local: bool = True) -> Study:
    """Read and check a study config.

    With ``local``, the local layers in its private ``.local.yaml`` file, when
    there is one, are added after any the config lists itself.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError("no such config file")
    raw = _read_yaml(path)
    private = local_file(path) if local else None
    if private is None or not private.is_file():
        return parse(raw, base=path.parent, source=path)
    extra = _read_yaml(private)
    allowed = {"local_layers", "outlines", "crash_forecast", "recorded_crashes"}
    unknown = sorted(set(extra) - allowed, key=str)
    if unknown:
        raise ConfigError(
            f"{private.name}: only local_layers, outlines, crash_forecast and "
            f"recorded_crashes may go here, not {unknown[0]!r}"
        )
    forecast, forecast_years = _forecast(extra.get("crash_forecast"), private)
    recorded = checks.mapping(
        extra.get("recorded_crashes", {}), f"{private.name}: recorded_crashes"
    )
    stray = sorted(set(recorded) - {"crashes", "parties"}, key=str)
    if stray:
        raise ConfigError(f"{private.name}: recorded_crashes: unknown setting {stray[0]!r}")
    crashes = _private_file(recorded.get("crashes"), private, "recorded_crashes.crashes")
    parties = _private_file(recorded.get("parties"), private, "recorded_crashes.parties")
    if "recorded_crashes" in extra and crashes is None:
        raise ConfigError(f"{private.name}: recorded_crashes needs crashes, the crash file")
    layers = [
        *checks.listed(raw, "local_layers", default=[]),
        *checks.listed(extra, "local_layers", default=[]),
    ]
    study = parse({**raw, "local_layers": layers}, base=path.parent, source=path)
    outlines = checks.mapping(extra.get("outlines", {}), f"{private.name}: outlines")
    if outlines and study.area.source == "demo":
        raise ConfigError(f"{private.name}: the demo town takes no outlines")
    known = {d.id: d for d in study.destinations}
    for key, item in outlines.items():
        where = f"{private.name}: outlines.{key}"
        if str(key) not in known:
            raise ConfigError(f"{where}: no destination has this id")
        if known[str(key)].outline is not None:
            raise ConfigError(f"{where}: the config already gives this destination an outline")
        drawn = _outline(checks.mapping(item, where), private.parent, where)
        if drawn.limits and known[str(key)].serves is not None:
            raise ConfigError(f"{where}: give serves or a limiting outline, not both")
        known[str(key)] = replace(known[str(key)], outline=drawn)
    return replace(
        study,
        destinations=tuple(known.values()),
        local_source=private,
        crash_forecast=forecast,
        crash_forecast_years=forecast_years,
        recorded_crashes=crashes,
        crash_parties=parties,
    )


def _forecast(raw: Any, private: Path) -> tuple[Path | None, tuple[int, int] | None]:
    """``crash_forecast``: a file, or ``{file: ..., years: [first, last]}`` giving
    the years its expected crashes cover, so the map can say how many a year."""
    if not isinstance(raw, Mapping):
        return _private_file(raw, private, "crash_forecast"), None
    where = f"{private.name}: crash_forecast"
    stray = sorted(set(raw) - {"file", "years"}, key=str)
    if stray:
        raise ConfigError(f"{where}: unknown setting {stray[0]!r}")
    if raw.get("file") is None:
        raise ConfigError(f"{where}: needs file, the forecast")
    found = _private_file(raw["file"], private, "crash_forecast.file")
    if raw.get("years") is None:
        return found, None
    years = raw["years"]
    if (
        not isinstance(years, list)
        or len(years) != 2
        or not all(isinstance(y, int) and not isinstance(y, bool) for y in years)
        or not 1900 <= years[0] <= years[1] <= 2100
    ):
        raise ConfigError(f"{where}: years should be [first, last], such as [2014, 2026]")
    return found, (years[0], years[1])


def _private_file(raw: Any, private: Path, key: str) -> Path | None:
    """A file the private config names, checked now rather than minutes into a run."""
    found = checks.path(raw, private.parent)
    if found is not None and not found.is_file():
        raise ConfigError(
            f"{private.name}: {key}: {found} not found; fix the path or remove the line"
        )
    return found


class _NoDuplicateKeys(yaml.SafeLoader):
    """YAML keeps the last of two equal keys without a word; a config must not."""


def _unique_mapping(loader: _NoDuplicateKeys, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue  # "<<": the loader merges it below; a key set beside it overrides
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"{key!r} is set twice", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_NoDuplicateKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.load(path.read_text(), Loader=_NoDuplicateKeys)  # noqa: S506 - a SafeLoader
    except yaml.YAMLError as error:
        raise ConfigError(f"{path.name}: not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name}: expected a mapping of settings")
    return raw


def parse(raw: Mapping[str, Any], base: Path = Path("."), source: Path | None = None) -> Study:
    """Check a study config already read from YAML; ``base`` resolves file names."""
    # Crash files come from crash records: only the private file may name them.
    top = checks.keys(
        raw,
        Study,
        "study config",
        skip={
            "source",
            "local_source",
            "crash_forecast",
            "crash_forecast_years",
            "recorded_crashes",
            "crash_parties",
        },
    )
    area = _area(checks.mapping(top["area"], "area"), base)
    modes = _modes(top["modes"])
    demand = _demand(checks.mapping(top["demand"], "demand"))
    destinations = tuple(
        _destination(checks.mapping(d, "destination"), base, area, demand)
        for d in checks.listed(top, "destinations")
    )
    if "network_flows" in top and top["network_flows"] is None:
        raise ConfigError(
            "network_flows: is empty; remove it, or write 'network_flows: {}' for the defaults"
        )
    network = (
        None
        if top.get("network_flows") is None
        else _network_flows(checks.mapping(top["network_flows"], "network_flows"), modes)
    )
    if not destinations and network is None:
        raise ConfigError("destinations: name at least one, or add network_flows")
    if not destinations and area.source == "destinations":
        raise ConfigError("area: source 'destinations' needs at least one destination")
    ids = [d.id for d in destinations]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"destinations: ids must be unique, got {ids}")
    groups = Counter(d.nearest for d in destinations if d.nearest is not None)
    if alone := sorted(g for g, n in groups.items() if n < 2):
        raise ConfigError(f"destinations: nearest group {alone[0]!r} has only one destination")
    if area.source == "demo":
        for key in ("overrides", "local_layers"):
            if top.get(key):
                raise ConfigError(f"{key}: the demo town takes no {key}; it is built in")
    presets = _per_mode(top["presets"], "presets", modes)
    caps = _per_mode(top["time_caps_min"], "time_caps_min", modes, required=("walk", "bike"))
    limits = {m: checks.positive(v, f"time_caps_min.{m}") for m, v in caps.items()}
    return Study(
        study=checks.text(top["study"], "study"),
        area=area,
        destinations=destinations,
        modes=modes,
        presets={m: _presets(m, v) for m, v in presets.items()},
        time_caps_min=limits,
        demand=demand,
        route_choice=_route_choice(top.get("route_choice", {})),
        time_bands_min=_bands(top.get("time_bands_min"), limits),
        shorter_limits_min=_shorter(top.get("shorter_limits_min"), limits),
        screen_candidates=checks.count(top.get("screen_candidates", 25), "screen_candidates"),
        exposure=_exposure(top.get("exposure")),
        overrides=checks.path(top.get("overrides"), base),
        distance_decay=_decay(top.get("distance_decay", {}), modes),
        local_layers=tuple(
            _layer(checks.mapping(item, "local_layers"), base, index)
            for index, item in enumerate(checks.listed(top, "local_layers", default=[]), start=1)
        ),
        crs=None if top.get("crs") is None else _crs(top["crs"]),
        network_flows=network,
        synthetic=checks.flag(top.get("synthetic", False), "synthetic"),
        source=source,
    )


def _area(raw: Mapping[str, Any], base: Path) -> Area:
    top = checks.keys(raw, Area, "area")
    source = checks.choice(top["source"], "area.source", AREA_SOURCES)
    place = top.get("place")
    boundary = checks.path(top.get("boundary_file"), base)
    if source == "place" and not place:
        raise ConfigError("area: source 'place' needs a place name")
    if source == "boundary_file" and boundary is None:
        raise ConfigError("area: source 'boundary_file' needs a boundary_file")
    buffer_m = checks.number(top.get("buffer_m", 0.0), "area.buffer_m")
    if buffer_m < 0:
        raise ConfigError("area.buffer_m: must not be negative")
    # A setting that cannot apply is a mistake, not something to pass over.
    unused = {
        "demo": ("place", "boundary_file", "buffer_m", "osm_file", "overture_release"),
        "place": ("boundary_file",),
        "boundary_file": ("place",),
        "destinations": ("place", "boundary_file"),
    }[source]
    for key in unused:
        if top.get(key) is not None:
            raise ConfigError(f"area.{key}: does not apply when area.source is {source!r}")
    if top.get("buildings") == "none" and top.get("overture_release") is not None:
        raise ConfigError("area.overture_release: does not apply when area.buildings is 'none'")
    return Area(
        source=source,
        place=None if place is None else checks.text(place, "area.place"),
        boundary_file=boundary,
        buffer_m=buffer_m,
        osm_file=checks.path(top.get("osm_file"), base),
        buildings=checks.choice(
            top.get("buildings", "overture"), "area.buildings", BUILDING_SOURCES
        ),
        overture_release=(
            None if top.get("overture_release") is None else _release(top["overture_release"])
        ),
        traffic_counts=checks.choice(
            top.get("traffic_counts", "hpms"), "area.traffic_counts", TRAFFIC_SOURCES
        ),
    )


def _release(raw: Any) -> str:
    text = checks.text(raw, "area.overture_release")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", text):
        raise ConfigError(f"area.overture_release: expected a name like 2026-10-21.0, got {text!r}")
    return text


def _destination(raw: Mapping[str, Any], base: Path, area: Area, demand: Demand) -> Destination:
    top = checks.keys(raw, Destination, "destination")
    where = f"destination {top['id']!r}"
    kind = checks.choice(top["kind"], f"{where} kind", DESTINATION_KINDS)
    location = None if top.get("location") is None else checks.lonlat(top["location"], where)
    boundary = checks.path(top.get("boundary_file"), base)
    if area.source != "demo":
        if kind == "point" and location is None:
            raise ConfigError(f"{where}: a point destination needs a location")
        if kind == "area" and boundary is None:
            raise ConfigError(f"{where}: an area destination needs a boundary_file")
    if kind == "point" and boundary is not None:
        raise ConfigError(f"{where}: boundary_file does not apply to a point destination")
    if kind == "area" and location is not None:
        raise ConfigError(f"{where}: location does not apply to an area destination")
    low, high = (checks.age(top.get(k), f"{where} {k}") for k in ("min_age", "max_age"))
    if (low is not None or high is not None) and demand.weight != "residents":
        raise ConfigError(f"{where}: ages apply to residents, not households")
    if low is not None and high is not None and low > high:
        raise ConfigError(f"{where}: min_age is above max_age")
    serves = checks.path(top.get("serves"), base)
    if serves is not None and area.source == "demo":
        raise ConfigError(f"{where}: the demo town takes no serves area")
    outline = None
    if top.get("outline") is not None:
        if area.source == "demo":
            raise ConfigError(f"{where}: the demo town takes no outline")
        outline = _outline(
            checks.mapping(top["outline"], f"{where} outline"), base, f"{where} outline"
        )
        if outline.limits and serves is not None:
            raise ConfigError(f"{where}: give serves or a limiting outline, not both")
    nearest = (
        None if top.get("nearest") is None else checks.text(top["nearest"], f"{where} nearest")
    )
    return Destination(
        id=_destination_id(top["id"], f"{where} id"),
        name=checks.text(top["name"], f"{where} name"),
        kind=kind,
        location=location,
        boundary_file=boundary,
        access_points=tuple(
            checks.lonlat(p, where) for p in checks.listed(top, "access_points", default=[])
        ),
        car_access_points=tuple(
            checks.lonlat(p, where) for p in checks.listed(top, "car_access_points", default=[])
        ),
        serves=serves,
        outline=outline,
        min_age=low,
        max_age=high,
        purpose=checks.choice(top.get("purpose", "everyday"), f"{where} purpose", PURPOSES),
        nearest=nearest,
    )


def _outline(raw: Mapping[str, Any], base: Path, where: str) -> Outline:
    top = checks.keys(raw, Outline, where)
    path = checks.path(top["file"], base)
    assert path is not None
    filters: dict[str, tuple[str, ...]] = {}
    for key, allowed in checks.mapping(top.get("where", {}), f"{where}.where").items():
        options = allowed if isinstance(allowed, list) else [allowed]
        filters[checks.text(key, f"{where}.where")] = tuple(str(o).strip() for o in options)
    return Outline(
        file=path,
        name=checks.text(top.get("name", "Attendance area"), f"{where}.name"),
        where=filters,
        credit=None if top.get("credit") is None else checks.text(top["credit"], f"{where}.credit"),
        limits=checks.flag(top.get("limits", True), f"{where}.limits"),
    )


def demand_for(study: Study, destination: Destination) -> Demand:
    """Who makes trips to ``destination``: its own ages if it has them, else the study's."""
    if destination.min_age is None and destination.max_age is None:
        return study.demand
    return Demand(weight=study.demand.weight, min_age=destination.min_age,
                  max_age=destination.max_age)  # fmt: skip


def decay_for(study: Study, destination: Destination, mode: str) -> str | None:
    """The decay curve for trips by ``mode`` to ``destination``; None: a hard time limit.

    Cars have none.  A curve named in the study's ``distance_decay`` wins;
    otherwise the destination's purpose picks one, and for a school the
    middle of its age range picks the walking curve (cycling trips to school
    are too few in the survey to split by age).
    """
    if mode not in ("walk", "bike"):
        return None
    chosen = study.distance_decay.get(mode)
    if chosen is not None:
        return None if chosen == NO_DECAY else chosen
    if destination.purpose != "school":
        return f"{mode}-{destination.purpose}"
    if mode == "bike":
        return "bike-school-5-17"
    demand = demand_for(study, destination)
    if demand.min_age is None and demand.max_age is None:
        return "walk-school-5-17"
    low = demand.min_age if demand.min_age is not None else 5
    high = demand.max_age if demand.max_age is not None else 17
    middle = (low + high) / 2
    if middle < 11:
        return "walk-school-5-10"
    return "walk-school-11-13" if middle < 14 else "walk-school-14-17"


def _decay(raw: Any, modes: tuple[Mode, ...]) -> dict[str, str]:
    from walkbike_routes.demand import decay  # read the shipped curves only when needed

    if raw == NO_DECAY:
        return {m: NO_DECAY for m in modes if m != "car"}
    table = checks.mapping(raw, "distance_decay")
    out: dict[str, str] = {}
    for mode, name in table.items():
        where = f"distance_decay.{mode}"
        if mode not in ("walk", "bike") or mode not in modes:
            raise ConfigError(
                f"distance_decay: {mode!r} is not one of the study's walk or bike modes"
            )
        name = checks.text(name, where)
        if name != NO_DECAY:
            try:
                curve = decay.load(name)
            except decay.DecayError as err:
                raise ConfigError(f"{where}: {err}") from err
            if curve.mode != mode:
                raise ConfigError(f"{where}: {name!r} is a {curve.mode} curve")
        out[mode] = name
    return out


def _layer(raw: Mapping[str, Any], base: Path, index: int) -> LocalLayer:
    where = f"local_layers[{index}]"
    top = checks.keys(raw, LocalLayer, where)
    kind = checks.choice(top["kind"], f"{where}.kind", tuple(LAYER_KINDS))
    path = checks.path(top["file"], base)
    assert path is not None
    match_m = checks.positive(top.get("match_m", LAYER_KINDS[kind]), f"{where}.match_m")
    column = None if top.get("column") is None else checks.text(top["column"], f"{where}.column")
    values = {
        checks.text(k, f"{where}.values"): checks.choice(v, f"{where}.values", FACILITY_VALUES)
        for k, v in checks.mapping(top.get("values", {}), f"{where}.values").items()
    }
    filters: dict[str, tuple[str, ...]] = {}
    for key, allowed in checks.mapping(top.get("where", {}), f"{where}.where").items():
        options = allowed if isinstance(allowed, list) else [allowed]
        filters[checks.text(key, f"{where}.where")] = tuple(str(o).strip() for o in options)
    if kind in {"speeds", "bikeways"} and column is None:
        raise ConfigError(f"{where}: {kind} needs the column that holds the value")
    if kind in {"sidewalks", "signals"} and column is not None:
        raise ConfigError(f"{where}: {kind} takes no column")
    if (kind == "bikeways") != bool(values):
        raise ConfigError(
            f"{where}: values (facility types to none, lane, track, path or boulevard) "
            + ("are needed for bikeways" if kind == "bikeways" else "apply only to bikeways")
        )
    return LocalLayer(
        kind=kind,
        file=path,
        match_m=match_m,
        column=column,
        values=values,
        where=filters,
        name=None if top.get("name") is None else checks.text(top["name"], f"{where}.name"),
    )


def _network_flows(raw: Mapping[str, Any], modes: tuple[Mode, ...]) -> NetworkFlows:
    top = checks.keys(raw, NetworkFlows, "network_flows")
    if not {"walk", "bike"} & set(modes):
        raise ConfigError("network_flows: route walking or cycling; cars are not included")
    flows = NetworkFlows(
        destinations=checks.count(top.get("destinations", 80), "network_flows.destinations"),
        purpose=checks.choice(top.get("purpose", "everyday"), "network_flows.purpose", PURPOSES),
        jobs_year=checks.count(top.get("jobs_year", 2023), "network_flows.jobs_year"),
        workers=checks.count(top.get("workers", 1), "network_flows.workers"),
    )
    if flows.destinations < 1 or flows.workers < 1:
        raise ConfigError("network_flows: destinations and workers must be 1 or more")
    if flows.purpose == "school":
        raise ConfigError("network_flows.purpose: jobs are not schools; use everyday or work")
    return flows


def _exposure(raw: Any) -> ExposureRules:
    try:
        return rules_from(None if raw is None else checks.mapping(raw, "exposure"))
    except ValueError as err:
        raise ConfigError(str(err)) from err


def _route_choice(raw: Any) -> ChoiceSettings:
    top = checks.keys(checks.mapping(raw, "route_choice"), ChoiceSettings, "route_choice")
    values: dict[str, Any] = {}
    for key, value in top.items():
        number = checks.number(value, f"route_choice.{key}")
        if key in {"routes", "search_paths"}:
            if number != int(number):
                raise ConfigError(f"route_choice.{key}: must be a whole number")
            values[key] = int(number)
        else:
            values[key] = number
    try:
        return ChoiceSettings(**values)
    except AccessibilityError as err:
        raise ConfigError(f"route_choice: {err}") from err


def _bands(raw: Any, limits: Mapping[Mode, float]) -> tuple[float, ...]:
    """The bands the tables count homes within.

    Routes are not searched past a mode's time limit, so a band above the
    shortest limit would count too few homes: it is refused, and left out of
    the default bands.
    """
    mode, limit = min(limits.items(), key=lambda item: item[1], default=(None, math.inf))
    if raw is None:
        return tuple(b for b in (5.0, 10.0, 15.0, 20.0) if b <= limit)
    if not isinstance(raw, list) or not raw:
        raise ConfigError("time_bands_min: expected a list of minutes")
    bands = tuple(checks.positive(v, "time_bands_min") for v in raw)
    if list(bands) != sorted(set(bands)):
        raise ConfigError("time_bands_min: must increase")
    if bands[-1] > limit:
        raise ConfigError(
            f"time_bands_min: {bands[-1]:g} is above time_caps_min.{mode} ({limit:g}); routes "
            "are not searched past the limit"
        )
    return bands


def _shorter(raw: object, limits: Mapping[Mode, float]) -> tuple[float, ...]:
    """Shorter walk and bike limits for the map, each below both modes' limits.

    A shorter limit is worked out from the routes found within the full one,
    so it must be under the walk and bike limits it is cut from.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("shorter_limits_min: expected a list of minutes")
    shorter = tuple(checks.positive(v, "shorter_limits_min") for v in raw)
    if list(shorter) != sorted(set(shorter)):
        raise ConfigError("shorter_limits_min: must increase")
    active = {m: v for m, v in limits.items() if m in ("walk", "bike")}
    mode, limit = min(active.items(), key=lambda item: item[1], default=(None, math.inf))
    if shorter and shorter[-1] >= limit:
        raise ConfigError(
            f"shorter_limits_min: {shorter[-1]:g} is not below time_caps_min.{mode} ({limit:g})"
        )
    return shorter


def _demand(raw: Mapping[str, Any]) -> Demand:
    top = checks.keys(raw, Demand, "demand")
    weight = checks.choice(top["weight"], "demand.weight", WEIGHTS)
    low, high = (checks.age(top.get(k), f"demand.{k}") for k in ("min_age", "max_age"))
    if weight == "households" and (low is not None or high is not None):
        raise ConfigError("demand: ages apply to residents, not households")
    if low is not None and high is not None and low > high:
        raise ConfigError("demand: min_age is above max_age")
    return Demand(weight=weight, min_age=low, max_age=high)


def _modes(raw: Any) -> tuple[Mode, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("modes: list at least one of walk, bike, car")
    for mode in raw:
        checks.choice(mode, "modes", MODES)
    if len(set(raw)) != len(raw):
        raise ConfigError("modes: each mode once")
    return tuple(cast(Mode, m) for m in raw)


def _per_mode(
    raw: Any, where: str, modes: tuple[Mode, ...], required: tuple[str, ...] = ()
) -> dict[Mode, Any]:
    table = checks.mapping(raw, where)
    extra = sorted(set(table) - set(modes))
    if extra:
        raise ConfigError(f"{where}: {extra[0]!r} is not one of the study's modes")
    needed = [m for m in modes if m in required or not required]
    missing = [m for m in needed if m not in table]
    if missing:
        raise ConfigError(f"{where}: missing a value for {missing[0]}")
    return {cast(Mode, m): v for m, v in table.items()}


def _presets(mode: Mode, raw: Any) -> tuple[str, ...]:
    from walkbike_routes.costs import presets  # the network builders load only when needed

    chosen = checks.names(raw, f"presets.{mode}")
    for name in chosen:
        try:
            presets.load(mode, name)
        except presets.PresetError as err:
            raise ConfigError(f"presets.{mode}: {err}") from err
    return chosen


def _destination_id(raw: Any, where: str) -> str:
    text = checks.text(raw, where)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ConfigError(f"{where}: use only letters, digits, - and _ (it names a folder)")
    if text in RESERVED_IDS:
        raise ConfigError(f"{where}: {text!r} is a folder the run writes itself; pick another id")
    return text


def missing_files(study: Study) -> list[str]:
    """Each file the config names that is not there, as ``setting: path``."""
    # A folder can be a map file too (a File Geodatabase), so this asks only if it is there.
    return [
        f"{where}: no such file: {path}" for where, path in named_files(study) if not path.exists()
    ]


def named_files(study: Study) -> list[tuple[str, Path]]:
    """Every file the config names, with the setting that names it."""
    named: list[tuple[str, Path | None]] = [
        ("area.boundary_file", study.area.boundary_file),
        ("area.osm_file", study.area.osm_file),
        ("overrides", study.overrides),
    ]
    for d in study.destinations:
        named += [
            (f"destination {d.id!r} boundary_file", d.boundary_file),
            (f"destination {d.id!r} serves", d.serves),
            (f"destination {d.id!r} outline", d.outline.file if d.outline else None),
        ]
    named += [
        (f"local_layers[{n}].file", layer.file) for n, layer in enumerate(study.local_layers, 1)
    ]
    return [(where, path) for where, path in named if path is not None]


def _crs(raw: Any) -> str:
    text = checks.text(raw, "crs")
    try:
        crs = CRS.from_user_input(text)
    except CRSError as err:
        raise ConfigError(f"crs: {text!r} is not a known coordinate system") from err
    if not crs.is_projected or crs.axis_info[0].unit_name != "metre":
        raise ConfigError(f"crs: {text!r} is not a projected system in metres")
    return text


__all__ = [
    "AREA_SOURCES", "BUILDING_SOURCES", "DESTINATION_KINDS", "FACILITY_VALUES", "LAYER_KINDS",
    "LOCAL_SUFFIX", "NO_DECAY", "PURPOSES", "RESERVED_IDS", "TRAFFIC_SOURCES", "WEIGHTS", "Area",
    "ConfigError", "Demand", "Destination", "LocalLayer", "LonLat", "Mode", "NetworkFlows",
    "Outline", "Study", "decay_for", "demand_for", "load", "local_file", "missing_files",
    "named_files", "parse",
]  # fmt: skip
