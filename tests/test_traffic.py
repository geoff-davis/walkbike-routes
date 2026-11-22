"""Daily traffic counts: reading HPMS and laying counts onto streets."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import httpx
import numpy as np
import pytest
from shapely.geometry import LineString, MultiLineString
from tenacity import wait_none

from tests import fake_services as fake
from walkbike_routes.download import hpms
from walkbike_routes.download.http import DownloadError, Fetcher
from walkbike_routes.network import prepare, traffic
from walkbike_routes.network.bicycle import counted_traffic_band
from walkbike_routes.network.broach import traffic_band

BBOX = (fake.WEST, fake.SOUTH, fake.WEST + 0.01, fake.SOUTH + 0.01)


def test_hpms_reads_the_latest_year_page_by_page(tmp_path: Path) -> None:
    calls: list[str] = []
    found = hpms.segments(BBOX, fake.fetcher(tmp_path, calls))
    assert found.route_id.tolist() == ["MAIN", "C3"] and found.aadt.tolist() == [13000, 3000]
    assert set(found.source) == {"FHWA HPMS NE 2025"} and found.crs == "EPSG:4326"
    assert sum("HPMS_FULL_NE_2025" in c for c in calls) == 2


def _fetcher(tmp_path: Path, states: list[str]) -> Fetcher:
    def answer(request: httpx.Request) -> httpx.Response:
        if "State_County" in str(request.url):
            features = [{"attributes": {"STUSAB": s}} for s in states]
            return httpx.Response(200, json={"features": features})
        if request.url.path.endswith("/services/Hosted"):
            return httpx.Response(200, json=fake.hpms_services())
        if "HPMS_FULL_IA_2025" in str(request.url):
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        if "HPMS_FULL" in str(request.url):
            return httpx.Response(200, json={"error": {"code": 400, "message": "bad query"}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(answer))
    return Fetcher(tmp_path, client, backoff=wait_none())


def test_an_empty_page_that_says_more_follow_is_paged_past(tmp_path: Path) -> None:
    offsets: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if "State_County" in str(request.url):
            return httpx.Response(200, json={"features": [{"attributes": {"STUSAB": "NE"}}]})
        if request.url.path.endswith("/services/Hosted"):
            return httpx.Response(200, json=fake.hpms_services())
        offset = int(request.url.params["resultOffset"])
        offsets.append(offset)
        if offset == 0:
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [],
                                             "properties": {"exceededTransferLimit": True}})  # fmt: skip
        return httpx.Response(200, json=fake.hpms_page(1))

    client = httpx.Client(transport=httpx.MockTransport(answer))
    found = hpms.segments(BBOX, Fetcher(tmp_path, client, backoff=wait_none()))
    assert offsets == [0, hpms.PAGE] and found.route_id.tolist() == ["C3"]


def test_the_search_box_covers_the_streets_with_room_to_spare() -> None:
    links = _links(("road", "primary", [(500_000, 4_430_000), (500_200, 4_430_000)]))
    west, south, east, north = traffic.search_bbox(links)
    inner = links.to_crs(4326).total_bounds
    assert west < inner[0] - 0.0009 and east > inner[2] + 0.0009 and south < inner[1] < north


def test_hpms_problems_are_reported_and_empty_areas_are_empty(tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="no HPMS layer for GU"):
        hpms.segments(BBOX, _fetcher(tmp_path / "a", ["GU"]))
    assert hpms.segments(BBOX, _fetcher(tmp_path / "b", ["IA"])).empty
    with pytest.raises(DownloadError, match="refused"):
        hpms.segments(BBOX, _fetcher(tmp_path / "c", ["NE"]))


def _links(*rows: tuple[str, str, list[tuple[float, float]]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"link_id": [r[0] for r in rows], "highway": [r[1] for r in rows]},
        geometry=[LineString(r[2]) for r in rows],
        crs="EPSG:26914",
    )


def _counts(*rows: tuple[float, list[tuple[float, float]]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"aadt": [r[0] for r in rows], "source": [f"count {r[0]:g}" for r in rows]},
        geometry=[LineString(r[1]) for r in rows],
        crs="EPSG:26914",
    )


def test_streets_take_the_nearest_count_running_their_way() -> None:
    links = _links(
        ("road", "primary", [(0, 0), (200, 0)]),
        ("side", "residential", [(100, 2), (100, 22)]),  # midpoint 12 m off, but crosswise
        ("path", "cycleway", [(0, 6), (200, 6)]),  # beside the road, but no cars
        ("lane", "service", [(0, -8), (200, -8)]),  # parking aisle: not matched
        ("far", "secondary", [(0, 40), (200, 40)]),
        ("twin", "secondary", [(0, 300), (200, 300)]),
    )
    counts = _counts(
        (12000, [(-50, 1), (250, 1)]),
        (30000, [(-50, 12), (250, 12)]),  # also within reach of the road, but farther
        (8000, [(-50, 296), (250, 296)]),
        (9000, [(-50, 309), (250, 309)]),
    )
    got = traffic.match_counts(links, counts).set_index("link_id")
    assert got.traffic_aadt["road"] == 12000 and got.traffic_aadt_source["road"] == "count 12000"
    assert got.traffic_aadt[["side", "path", "lane", "far"]].isna().all()
    assert got.traffic_aadt["twin"] == 8000


def test_bends_multipart_counts_and_car_free_streets() -> None:
    links = _links(
        ("bend", "secondary", [(0, 0), (0, 150), (100, 150), (100, 300)]),  # east-west mid-way
        ("piece", "tertiary", [(1000, 1), (1010, 1)]),
        ("closed", "residential", [(0, 500), (200, 500)]),
    )
    links["car_allowed"] = [True, True, False]
    counts = gpd.GeoDataFrame(
        {"aadt": [11000, 6000, 15000], "source": ["bend", "piece", "closed"]},
        geometry=[
            LineString([(-50, 151), (150, 151)]),
            MultiLineString([[(1000, -500), (1000, -400)], [(1000, 0), (1010, 0)]]),
            LineString([(-50, 505), (250, 505)]),
        ],
        crs="EPSG:26914",
    )
    got = traffic.match_counts(links, counts).set_index("link_id").traffic_aadt
    assert got["bend"] == 11000 and got["piece"] == 6000 and np.isnan(got["closed"])


def test_each_side_of_a_divided_road_reaches_its_centre_line() -> None:
    links = _links(
        ("north", "primary", [(0, 22), (200, 22)]),
        ("south", "primary", [(200, -22), (0, -22)]),
        ("street", "residential", [(0, 25), (200, 25)]),  # two-way: too far
    )
    links["oneway"] = ["yes", "yes", "no"]
    counts = _counts((28000, [(-50, 0), (250, 0)]))
    got = traffic.match_counts(links, counts).set_index("link_id").traffic_aadt
    assert got[["north", "south"]].tolist() == [28000, 28000] and np.isnan(got["street"])


def test_counts_in_degrees_are_projected_and_bad_input_is_refused() -> None:
    links = _links(("road", "primary", [(500_000, 4_430_000), (500_200, 4_430_000)]))
    counts = _counts((12000, [(499_900, 4_430_003), (500_300, 4_430_003)])).to_crs(4326)
    assert traffic.match_counts(links, counts).traffic_aadt.tolist() == [12000]
    assert traffic.match_counts(links, counts.iloc[:0]).traffic_aadt.isna().all()
    with pytest.raises(ValueError, match="projected"):
        traffic.match_counts(links.to_crs(4326), counts)


def test_coverage_is_by_length_and_class() -> None:
    links = _links(
        ("a", "primary", [(0, 0), (300, 0)]),
        ("b", "primary", [(0, 50), (100, 50)]),
        ("c", "cycleway", [(0, 90), (100, 90)]),
    )
    links["traffic_aadt"] = [15000.0, np.nan, np.nan]
    table = traffic.coverage(links)
    assert table.share_counted.to_dict() == pytest.approx({"primary": 0.75})


@pytest.mark.parametrize(
    ("highway", "lanes", "aadt", "band"),
    [("primary", 2, 13229, 2), ("primary", 2, None, 3), ("tertiary", 2, 4999, 0),
     ("tertiary", 2, 5000, 1), ("residential", 2, 31000, 4), ("primary", 2, float("nan"), 3),
     ("primary", 6, 9000, 1), ("cycleway", None, 20000, -1), ("secondary", 2, "n/a", 2)],
)  # fmt: skip
def test_a_count_sets_the_broach_traffic_band(
    highway: str, lanes: float | None, aadt: object, band: int
) -> None:
    assert traffic_band(highway, lanes, aadt) == band


@pytest.mark.parametrize(
    ("inferred", "aadt", "band"),
    [("moderate", 2999, "low"), ("high", 1500, "low"), ("very_low", 800, "very_low"),
     ("low", 3000, "moderate"), ("very_low", 9000, "moderate"), ("unknown", 9000, "moderate"),
     ("high", 9000, "high"), ("very_high", 3500, "very_high"),
     ("no_motor_traffic", 9000, "no_motor_traffic"), ("low", float("nan"), "low")],
)  # fmt: skip
def test_a_count_settles_the_lts_low_traffic_test(inferred: str, aadt: float, band: str) -> None:
    assert counted_traffic_band(inferred, aadt) == band


def test_counts_change_traffic_stress() -> None:
    rows = [("quiet", "tertiary", 25, 2000.0), ("busy", "residential", 25, 8000.0),
            ("plain", "residential", 25, None), ("path", "cycleway", None, 5000.0),
            ("laned", "primary", 30, 2000.0)]  # fmt: skip
    links = gpd.GeoDataFrame(
        {"link_id": [r[0] for r in rows], "u": list("abcde"), "v": list("fghij"),
         "highway": [r[1] for r in rows], "speed_mph": [r[2] for r in rows],
         "traffic_aadt": [r[3] for r in rows], "lanes": [2, 2, 2, None, 2],
         "bike_facility": ["none"] * 4 + ["lane"], "sidewalk": ["both"] * 5},
        geometry=[LineString([(0, 100 * i), (200, 100 * i)]) for i in range(5)], crs="EPSG:26914",
    )  # fmt: skip
    edges = prepare.physical_edges(links).set_index("physical_edge_id")
    assert edges.lts_best.to_dict() == {"quiet": 1, "busy": 2, "plain": 1, "path": 1, "laned": 3}
    assert edges.traffic_band.to_dict() == {"quiet": "low", "busy": "moderate", "plain": "low",
                                            "path": "no_motor_traffic", "laned": "high"}  # fmt: skip
    assert edges.traffic_volume_type["quiet"] == "counted AADT"
    assert edges.traffic_volume_type["laned"] == "inferred traffic band"


@pytest.mark.parametrize(
    ("speed", "lanes", "aadt", "lts"),
    [(25, 2, 2000, 1), (25, 2, 8000, 2), (30, 2, 2000, 2), (30, 2, 8000, 3), (25, 3, 2000, 1),
     (35, 2, 2000, 3), (25, 4, 2000, 4), (30, 2, float("inf"), 2), (30, 2, -5, 2)],
)  # fmt: skip
def test_counted_streets_follow_furth_s_mixed_traffic_table(
    speed: float, lanes: float, aadt: float, lts: int
) -> None:
    links = gpd.GeoDataFrame(
        {"link_id": ["x"], "u": ["a"], "v": ["b"], "highway": ["tertiary"], "speed_mph": [speed],
         "lanes": [lanes], "traffic_aadt": [aadt], "bike_facility": ["none"],
         "sidewalk": ["both"]},
        geometry=[LineString([(0, 0), (200, 0)])], crs="EPSG:26914",
    )  # fmt: skip
    edge = prepare.physical_edges(links).iloc[0]
    assert edge.lts_best == lts
    usable = np.isfinite(aadt) and aadt >= 0
    assert edge.traffic_volume_type == ("counted AADT" if usable else "inferred traffic band")
