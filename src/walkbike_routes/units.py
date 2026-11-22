"""Physical unit conversions shared by every travel mode."""

from __future__ import annotations

MPS_PER_MPH = 0.44704


def mph_to_mps(speed_mph: float) -> float:
    """Convert a speed in miles per hour to meters per second."""
    return speed_mph * MPS_PER_MPH
