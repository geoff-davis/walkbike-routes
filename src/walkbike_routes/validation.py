"""Validation for canonical accessibility GeoDataFrame contracts."""

from __future__ import annotations

import geopandas as gpd

from walkbike_routes.exceptions import SchemaValidationError


def require_metric_projected_crs(frame: gpd.GeoDataFrame, name: str) -> None:
    """Require a declared projected CRS whose horizontal units are meters."""
    crs = frame.crs
    if crs is None:
        raise SchemaValidationError(f"{name} has no CRS")
    if not crs.is_projected:
        raise SchemaValidationError(f"{name} must use a projected analysis CRS, not {crs}")
    axes = crs.axis_info
    if not axes or any(abs(float(axis.unit_conversion_factor) - 1.0) > 1e-12 for axis in axes[:2]):
        raise SchemaValidationError(f"{name} CRS linear units must be meters, not {crs}")
