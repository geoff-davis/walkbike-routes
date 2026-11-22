"""Homes shared by a nearest group: each goes only to the group's nearest destination.

A destination's ``nearest`` names its group (see
:class:`walkbike_routes.config_models.Destination`).  "Nearest" is the shortest
walk along streets and paths, under the shipped ``walk/shortest`` preset, so
every mode and route model sees the same split.  A home with no street route
to any of the group goes to the destination nearest in a straight line.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

import geopandas as gpd
import pandas as pd

from walkbike_routes import config as settings
from walkbike_routes.analysis import calculate_accessibility
from walkbike_routes.costs import presets
from walkbike_routes.models import TravelDirection
from walkbike_routes.routing.turns import sink
from walkbike_routes.study_context import CORE_MODES, MPS_PER_MPH, Context
from walkbike_routes.study_entrances import access_points
from walkbike_routes.study_networks import ModeNetwork, mode_network

log = logging.getLogger(__name__)

NEAREST_PRESET = "shortest"


def nearest_origins(
    context: Context,
    destinations: Sequence[settings.Destination],
    origins: Sequence[gpd.GeoDataFrame],
) -> list[gpd.GeoDataFrame]:
    """Each destination's origins, with a nearest group's homes split among its members."""
    groups: dict[str, list[int]] = defaultdict(list)
    for i, destination in enumerate(destinations):
        if destination.nearest is not None:
            groups[destination.nearest].append(i)
    if not groups:
        return list(origins)
    preset = presets.load("walk", NEAREST_PRESET)
    result = list(origins)
    for group, members in groups.items():
        every = pd.concat([origins[i] for i in members]).drop_duplicates("origin_id")
        shared = replace(context, origins=gpd.GeoDataFrame(every, crs=origins[members[0]].crs))
        built = mode_network(shared, "walk", preset)
        lengths = {i: _street_lengths(shared, destinations[i], built, preset) for i in members}
        table = pd.DataFrame(lengths, index=every.origin_id.astype(str))
        straight = pd.DataFrame(
            {i: _straight_lengths(context, destinations[i], every) for i in members},
            index=every.origin_id.astype(str),
        )
        # Street distance where any is known; otherwise straight-line distance.
        by_street = table.notna().any(axis=1)
        choice = table.idxmin(axis=1).where(by_street, straight.idxmin(axis=1))
        log.info(
            "nearest group %s: %d homes split by street distance, %d by straight line",
            group, int(by_street.sum()), int((~by_street).sum()),
        )  # fmt: skip
        for i in members:
            mine = set(choice.index[choice.eq(i)])
            result[i] = origins[i][origins[i].origin_id.astype(str).isin(mine)]
            log.info(
                "%s: %d homes nearest it in group %s", destinations[i].id, len(result[i]), group
            )
    return result


def _street_lengths(
    context: Context, destination: settings.Destination, built: ModeNetwork, preset: presets.Preset
) -> pd.Series:
    """Each origin's shortest street distance to ``destination``, in metres (absent if none)."""
    search = built.search
    access = access_points(context, destination, built.nodes, built.links, CORE_MODES["walk"])
    if search is not built.graph:
        access = access.assign(node_id=access.node_id.astype(str).map(sink))
    result = calculate_accessibility(
        search, context.origins, built.attachments, access,
        study_id=context.study.study, destination_id=destination.id, profile_id=preset.name,
        scenario_id=f"nearest:{preset.name}", mode=CORE_MODES["walk"],
        direction=TravelDirection.TO_DESTINATION, route_weight=preset.objective,
        thresholds_min=(60.0,), time_limit_min=None,
        snap_speed_mps=float(preset.parameters["speed_mph"]) * MPS_PER_MPH,
    )  # fmt: skip
    routes = result.routes
    reached = routes[routes.route_reachable.astype(bool)]
    return reached.groupby(reached.origin_id.astype(str)).physical_length_m.min()


def _straight_lengths(
    context: Context, destination: settings.Destination, homes: pd.DataFrame
) -> pd.Series:
    """Each home's straight-line distance to ``destination``, in the study's units."""
    place = context.destinations[context.destinations.destination_id.eq(destination.id)]
    shape = place.to_crs(homes.crs).geometry.union_all()
    return pd.Series(
        gpd.GeoSeries(homes.geometry, crs=homes.crs).distance(shape).to_numpy(),
        index=homes.origin_id.astype(str).to_numpy(),
    )
