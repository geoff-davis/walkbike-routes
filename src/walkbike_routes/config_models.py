"""What a study's settings are: the area, destinations, demand and options, once read and checked."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from walkbike_routes.analysis.exposure_rules import ExposureRules
from walkbike_routes.choice import ChoiceSettings

Mode = Literal["walk", "bike", "car"]


AREA_SOURCES = ("demo", "place", "boundary_file")


BUILDING_SOURCES = ("overture", "none")


TRAFFIC_SOURCES = ("hpms", "none")


DESTINATION_KINDS = ("point", "area")


WEIGHTS = ("residents", "households")


PURPOSES = ("everyday", "school", "work")


NO_DECAY = "none"


# Local layer kinds and how far (metres) a feature may be from the street it describes.
LAYER_KINDS = {"sidewalks": 18.0, "signals": 22.0, "speeds": 18.0, "bikeways": 14.0}


FACILITY_VALUES = ("none", "lane", "track", "path", "boulevard")


LonLat = tuple[float, float]


@dataclass(frozen=True)
class Area:
    """Where the street network and the trip origins come from.

    ``demo`` is the made-up town in :mod:`walkbike_routes.demo`; ``place`` is a
    US Census place ("Name, State"); ``boundary_file`` is an outline of
    your own.  ``buffer_m`` widens the area so routes can leave and re-enter it.
    ``osm_file`` is an OpenStreetMap file to use instead of a Geofabrik extract.
    ``buildings`` spreads each Census block's counts over its buildings
    ("overture") or keeps one point per block ("none"); ``overture_release``
    pins an Overture release such as "2026-10-21.0" (default: the latest).
    ``traffic_counts`` lays federal HPMS daily traffic counts onto the streets
    ("hpms") or leaves traffic to be inferred from road class ("none").
    """

    source: str
    place: str | None = None
    boundary_file: Path | None = None
    buffer_m: float = 0.0
    osm_file: Path | None = None
    buildings: str = "overture"
    overture_release: str | None = None
    traffic_counts: str = "hpms"


@dataclass(frozen=True)
class Outline:
    """A school's attendance area, or another shape drawn on a destination's map.

    With ``limits`` (the default) only homes inside it make trips there, as
    with ``serves``; with ``limits: false`` it is only drawn and every home
    within reach counts.  ``where`` keeps the file's features whose columns have
    one of the given values, so one file of several schools' areas can serve
    each; ``name`` labels it on the map; ``credit`` is printed with the map's
    sources (default: the file name).
    """

    file: Path
    name: str = "Attendance area"
    where: dict[str, tuple[str, ...]] = field(default_factory=dict)
    credit: str | None = None
    limits: bool = True


@dataclass(frozen=True)
class Destination:
    """Where trips go.

    A ``point`` destination has a ``location``; an ``area`` destination (a
    downtown, a campus) has a ``boundary_file``.  Either may list
    ``access_points``, the entrances trips must end at, such as school gates.
    ``car_access_points`` are where cars stop: drop-off curbs or car park
    entrances; without them cars go to the walking and cycling entrances, or
    to the nearest street.  In the demo town all of these come from the town.

    ``serves`` is an outline (a school's attendance area): only homes inside it
    make trips here.  ``outline`` is drawn on the map and, unless it says
    otherwise, also limits the homes (see :class:`Outline`); it may also be
    given in the private ``.local.yaml``.  ``min_age`` and ``max_age`` replace the study's ages for
    this destination, so an elementary and a high school can share a study.

    ``purpose`` (everyday, school or work) picks the distance-decay curves
    for walking and cycling here; for a school the ages pick the walking curve.

    ``nearest`` names a group of destinations that share their homes: each
    home makes trips only to the group's destination nearest it along streets
    and paths (high schools whose attendance areas are not known, say).  It
    takes the place of ``serves`` or a limiting outline.
    """

    id: str
    name: str
    kind: str
    location: LonLat | None = None
    boundary_file: Path | None = None
    access_points: tuple[LonLat, ...] = ()
    car_access_points: tuple[LonLat, ...] = ()
    serves: Path | None = None
    outline: Outline | None = None
    min_age: int | None = None
    max_age: int | None = None
    purpose: str = "everyday"
    nearest: str | None = None


@dataclass(frozen=True)
class Demand:
    """Who the trips start from: residents in an age range, or households."""

    weight: str
    min_age: int | None = None
    max_age: int | None = None


@dataclass(frozen=True)
class LocalLayer:
    """A local file with better evidence than OpenStreetMap for one attribute.

    ``kind`` is ``sidewalks`` (lines), ``signals`` (points), ``speeds``
    (lines; ``column`` holds mph) or ``bikeways`` (lines; ``column`` holds a
    facility type and ``values`` maps each to none, lane, track, path or boulevard).
    ``where`` keeps only features whose columns have one of the given values;
    ``match_m`` is how far a feature may be from its street (0: the kind's
    default in ``LAYER_KINDS``); ``name`` labels
    the source in the outputs (default: the file name).  See
    :mod:`walkbike_routes.local_layers`.
    """

    kind: str
    file: Path
    match_m: float = 0.0
    column: str | None = None
    values: dict[str, str] = field(default_factory=dict)
    where: dict[str, tuple[str, ...]] = field(default_factory=dict)
    name: str | None = None


@dataclass(frozen=True)
class NetworkFlows:
    """Flows across the whole network, to clusters of jobs, not to one destination.

    ``destinations`` is how many job clusters to make from the Census
    Bureau's LODES workplace counts (see :mod:`walkbike_routes.network_flows`);
    ``purpose`` picks the distance-decay curves that share each home's trips
    among them; ``jobs_year`` is the LODES year; ``workers`` routes that many
    clusters at once, in separate processes.
    """

    destinations: int = 80
    purpose: str = "everyday"
    jobs_year: int = 2023
    workers: int = 1


@dataclass(frozen=True)
class Study:
    study: str
    area: Area
    destinations: tuple[Destination, ...]
    modes: tuple[Mode, ...]
    presets: dict[Mode, tuple[str, ...]]
    time_caps_min: dict[Mode, float]
    demand: Demand
    # How trips split across near-best routes; routes: 1 is all-or-nothing.
    route_choice: ChoiceSettings = ChoiceSettings()
    # Travel-time bands reported for catchments, in minutes.
    time_bands_min: tuple[float, ...] = (5.0, 10.0, 15.0, 20.0)
    # Shorter walk and bike limits the map can switch to: route shares, flows
    # and exposure lists are worked out again at each, from the same routes.
    shorter_limits_min: tuple[float, ...] = ()
    # Street sections tried in each walk and bike improvement screen; 0 turns them off.
    screen_candidates: int = 25
    # Risk points and the fix-first rule of the exposure lists; see analysis/exposure_rules.py.
    exposure: ExposureRules = field(default_factory=ExposureRules)
    # A city's own sidewalks, signals, speeds or bikeways, laid over OpenStreetMap.
    local_layers: tuple[LocalLayer, ...] = ()
    # A YAML file of reviewed hand corrections to the streets; see walkbike_routes.overrides.
    overrides: Path | None = None
    # Decay curve by mode, or "none" for a hard time limit; unset modes use each
    # destination's purpose.  See walkbike_routes.demand.decay.
    distance_decay: dict[str, str] = field(default_factory=dict)
    # A projected CRS such as "EPSG:32610"; left out, the local UTM zone is used.
    crs: str | None = None
    # Flows across the whole network to job clusters; None: only the destinations.
    network_flows: NetworkFlows | None = None
    # Another model's forecast of crashes at intersections (points with ``rank``
    # and ``expected_bike_crashes``, as bike-safety-ca writes them), drawn as a
    # map layer only; it changes no route or list.  Set in the private file.
    crash_forecast: Path | None = None
    # The years the forecast's expected crashes cover (first, last), if given.
    crash_forecast_years: tuple[int, int] | None = None
    # Police crash records in the California SWITRS/TIMS layout (crashes, and
    # parties for the rider's age), drawn as a map layer only.  Private file.
    recorded_crashes: Path | None = None
    crash_parties: Path | None = None
    # True when the inputs are made up: every output is then labelled synthetic.
    synthetic: bool = False
    source: Path | None = field(default=None, compare=False)
    # The private file next to the config whose local layers were added, if any.
    local_source: Path | None = field(default=None, compare=False)


# A destination's id names its output folder, so it is plain text with no path in it,
# and not a folder the run writes itself.
RESERVED_IDS = ("network", "all-destinations")
