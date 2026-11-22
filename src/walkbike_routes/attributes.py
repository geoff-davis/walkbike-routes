"""Shared physical-edge attribute readers for mode-specific link builders.

These helpers sit below the network package so adapters and every mode
builder can share one reading of identifiers, OSM tags, and one-way rules.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

ROUNDABOUT_JUNCTIONS = frozenset({"roundabout", "circular"})
NONMOTOR_HIGHWAYS = frozenset(
    {"cycleway", "path", "footway", "pedestrian", "steps", "track", "bridleway"}
)
# Road classes closed to a mode unless an access tag says otherwise.
NO_WALKING_OR_CYCLING = frozenset({"motorway", "motorway_link", "trunk", "trunk_link"})
NO_CYCLING = NO_WALKING_OR_CYCLING | {"footway", "steps", "pedestrian"}
NO_CARS = NONMOTOR_HIGHWAYS
_MISSING_TEXT = frozenset({"nan", "none", "null", "<na>"})


def text_or_none(value: object) -> str | None:
    """Return stripped text, or ``None`` for missing values and placeholder strings."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    text = str(value).strip()
    if not text or text.lower() in _MISSING_TEXT:
        return None
    return text


def physical_edge_id(edge: Any) -> str:
    """Return the canonical identifier of a physical edge row."""
    if hasattr(edge, "physical_edge_id"):
        return str(edge.physical_edge_id)
    return str(edge.edge_id)


def parse_osm_tags(value: object) -> dict[str, Any]:
    """Parse a serialized OSM tag mapping, returning an empty mapping on failure."""
    if not isinstance(value, str) or not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def junction_tag(edge: Any) -> str | None:
    """Read the OSM ``junction`` tag from an explicit column or serialized tags."""
    for field in ("junction", "junction_tag"):
        if hasattr(edge, field):
            value = text_or_none(getattr(edge, field))
            if value is not None:
                return value.lower()
    if hasattr(edge, "osm_tags"):
        value = text_or_none(parse_osm_tags(edge.osm_tags).get("junction"))
        return value.lower() if value is not None else None
    return None


def oneway_allows(oneway: object, junction: object, forward: bool) -> bool:
    """Return whether travel in one direction is legal on a physical edge.

    Roundabout and circular junctions are implicitly one-way in OSM even when
    the ``oneway`` tag is absent, so every mode reads them the same way.
    """
    value = (text_or_none(oneway) or "").lower()
    if not value and (text_or_none(junction) or "").lower() in ROUNDABOUT_JUNCTIONS:
        value = "yes"
    if value in {"yes", "true", "1"}:
        return forward
    if value in {"-1", "reverse"}:
        return not forward
    return value not in {"reversible", "alternating"}


def bicycle_oneway(edge: Any) -> object:
    """Resolve the bicycle override before the general vehicle direction rule."""
    for field in ("oneway_bicycle", "oneway:bicycle"):
        value = text_or_none(getattr(edge, field, None))
        if value is not None:
            return value
    value = text_or_none(parse_osm_tags(getattr(edge, "osm_tags", None)).get("oneway:bicycle"))
    return value if value is not None else getattr(edge, "oneway", None)
