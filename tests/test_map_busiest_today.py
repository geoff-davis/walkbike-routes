"""A ranked place's popup gives the busiest street there today, before any change."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString, Point

from walkbike_routes.report.map import busiest_today

FLOWS = gpd.GeoDataFrame(
    {"physical_edge_id": ["a", "b", "c"], "potential_flow": [3.0, 7.5, 12.0]},
    geometry=[
        LineString([(0, 0), (100, 0)]),
        LineString([(100, 0), (200, 0)]),
        LineString([(100, 0), (100, 100)]),  # meets a and b at (100, 0)
    ],
    crs=26910,
)


def test_a_street_section_takes_its_busiest_street() -> None:
    assert busiest_today({"physical_edge_ids": "a;b"}, FLOWS) == 7.5


def test_a_junction_takes_the_busiest_street_meeting_there() -> None:
    row = {"physical_edge_ids": "J:n1", "geometry": Point(100, 0)}
    assert busiest_today(row, FLOWS) == 12.0
    away = {"physical_edge_ids": "J:n2", "geometry": Point(50, 50)}
    assert busiest_today(away, FLOWS) is None


def test_a_place_with_no_flow_today_has_none() -> None:
    assert busiest_today({"physical_edge_ids": "missing"}, FLOWS) is None
