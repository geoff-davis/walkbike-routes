"""Typed destination-neutral study and result models."""

from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    """Supported travel modes."""

    PEDESTRIAN = "pedestrian"
    BICYCLE = "bicycle"
    CAR = "car"


class TravelDirection(StrEnum):
    """Direction relative to a destination."""

    TO_DESTINATION = "to_destination"
    FROM_DESTINATION = "from_destination"
    BOTH = "both"


class LinkDirection(StrEnum):
    """Direction in which a routable link follows its physical edge."""

    FORWARD = "forward"
    REVERSE = "reverse"
    BOTH = "both"


class DestinationType(StrEnum):
    """Ways a destination can be represented."""

    POINT = "point"
    POLYGON = "polygon"
