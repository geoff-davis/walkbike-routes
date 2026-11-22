"""Virtual destination nodes and directional zero-cost access links."""

from __future__ import annotations

import geopandas as gpd


def access_point_node_map(access_points: gpd.GeoDataFrame) -> dict[str, str]:
    """Return the access-point-to-network-node mapping used by path trees."""
    return {
        str(row.access_point_id): str(row.node_id) for row in access_points.itertuples(index=False)
    }
