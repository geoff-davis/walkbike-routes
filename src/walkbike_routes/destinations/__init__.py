"""Destination entrances on the network."""

from walkbike_routes.destinations.access_points import (
    AccessPointGenerationPolicy,
    resolve_point_access_points,
    resolve_polygon_access_points,
    select_access_points,
)
from walkbike_routes.destinations.virtual_nodes import (
    access_point_node_map,
)

__all__ = [
    "AccessPointGenerationPolicy",
    "access_point_node_map",
    "resolve_point_access_points",
    "resolve_polygon_access_points",
    "select_access_points",
]
