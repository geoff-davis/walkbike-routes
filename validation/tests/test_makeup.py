"""Route makeup and trips per street (wbvalidate.makeup), on made-up links and trips."""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point
from wbvalidate import makeup

# A made-up crossroads: Elm Street runs east from x=0 to x=200, crossed at
# x=100 by Main Street (busy); a path leaves Elm's east end to the north.
LINKS = gpd.GeoDataFrame(
    {
        "physical_edge_id": ["elm-1", "elm-2", "main-n", "main-s", "path"],
        "name": ["Elm Street", "Elm Street", "Main Street", "Main Street", None],
        "highway": ["residential", "residential", "primary", "primary", "cycleway"],
        "bike_facility": ["lane", "none", "none", "none", "path"],
        "lts_furth": [2, 4, 4, 4, 1],
        "oneway": ["yes", "no", "no", "no", "no"],  # elm-1 is one way, eastbound
        "oneway_bicycle": [None, None, None, None, None],
    },
    geometry=[
        LineString([(0, 0), (100, 0)]),
        LineString([(100, 0), (200, 0)]),
        LineString([(100, 0), (100, 100)]),
        LineString([(100, 0), (100, -100)]),
        LineString([(200, 0), (200, 100)]),
    ],
    crs=3310,
)


@pytest.fixture(scope="module")
def links() -> makeup.Links:
    return makeup.prepare_links(LINKS)


def _ids(links: makeup.Links, line: LineString) -> list[str]:
    used, _ = makeup.match(line, links)
    return sorted(links.ids[used])


def test_a_line_uses_the_links_it_rides_along_not_those_it_crosses(links: makeup.Links) -> None:
    east = LineString([(0, 3), (200, 3)])  # drawn 3 m off the centre line
    used, forward = makeup.match(east, links)
    assert sorted(links.ids[used]) == ["elm-1", "elm-2"]
    assert forward.all()
    assert _ids(links, LineString([(0, 0), (100, 0), (100, 100)])) == ["elm-1", "main-n"]


def test_riding_west_crosses_main_street_against_the_one_way(links: makeup.Links) -> None:
    row, _ = makeup.describe(LineString([(200, 0), (0, 0)]), links)
    assert row["matched_m"] == pytest.approx(200)
    assert row["against_m"] == pytest.approx(100)  # elm-1 only
    assert (row["lane_m"], row["low_m"], row["high_m"]) == pytest.approx((100, 100, 100))
    assert (row["turns"], row["crossings"]) == (0, 1)
    row, _ = makeup.describe(LineString([(0, 0), (200, 0)]), links)
    assert row["against_m"] == 0


def test_turning_onto_a_busy_street_is_not_crossing_it(links: makeup.Links) -> None:
    row, _ = makeup.describe(LineString([(0, 0), (100, 0), (100, 100)]), links)
    assert (row["turns"], row["crossings"]) == (1, 0)


def test_small_wiggles_are_not_turns() -> None:
    wiggly = LineString([(0, 0), (50, 4), (100, 0), (150, 4), (200, 0), (200, 100)])
    assert makeup.turns(wiggly) == 1


def _rows(**columns: list[float]) -> pd.DataFrame:
    base = {"trip_id": ["a", "b", "a", "b"], "user_id": ["u1", "u2", "u1", "u2"],
            "source": ["ridden", "ridden", "p", "p"], "length_m": [1000.0] * 4,
            "matched_m": [1000.0] * 4, "separated_m": [0.0] * 4, "lane_m": [0.0] * 4,
            "low_m": [0.0] * 4, "high_m": [0.0] * 4, "against_m": [0.0] * 4,
            "turns": [0.0] * 4, "crossings": [0.0] * 4}  # fmt: skip
    return pd.DataFrame(base | columns)


def test_makeup_shares_are_summed_over_trips_and_compared_with_ridden() -> None:
    rows = _rows(lane_m=[500.0, 100.0, 1000.0, 0.0], turns=[4.0, 2.0, 2.0, 2.0])
    rows.loc[1, ["length_m", "matched_m"]] = 500.0  # trip b's ridden route is shorter
    found = {m.source: m for m in makeup.makeup(rows, draws=200)}
    assert list(found) == ["ridden", "p"]
    assert found["ridden"].values["lane_m"][0] == pytest.approx(600 / 1500)
    assert found["p"].values["lane_m"][0] == pytest.approx(1000 / 2000)
    assert found["p"].differences["lane_m"][0] == pytest.approx(0.5 - 0.4)
    assert found["ridden"].values["turns"][0] == pytest.approx(6 / 1.5)
    assert found["ridden"].differences == {}
    low, high = found["p"].values["lane_m"][1:]
    assert 0 <= low <= high <= 1
    again = makeup.makeup(rows, draws=200)
    assert repr(again) == repr(list(found.values()))


def test_makeup_keeps_only_trips_every_source_has() -> None:
    rows = _rows(lane_m=[500.0, 100.0, 1000.0, 0.0]).drop(index=3)  # p lacks trip b
    found = {m.source: m for m in makeup.makeup(rows, draws=50)}
    assert found["ridden"].values["lane_m"][0] == pytest.approx(0.5)


def _used(source: str, counts: dict[int, int]) -> pd.DataFrame:
    rows = [(f"t{i}", source, link) for link, n in counts.items() for i in range(n)]
    return pd.DataFrame(rows, columns=["trip_id", "source", "link"])


def test_street_scores_rank_trips_per_link() -> None:
    ridden = _used("ridden", {0: 5, 1: 3, 2: 1})
    used = pd.concat(
        [ridden, _used("same", {0: 5, 1: 3, 2: 1}), _used("backwards", {0: 1, 1: 3, 2: 5})]
    )
    users = pd.Series({f"t{i}": f"u{i % 2}" for i in range(5)})
    found = {s.preset: s for s in makeup.street_scores(used, users, baseline="same", draws=100)}
    assert found["same"].rho == pytest.approx(1)
    assert found["backwards"].rho == pytest.approx(-1)
    assert found["backwards"].difference == pytest.approx(-2)
    assert math.isnan(found["same"].difference)
    assert found["same"].links == 3


def test_street_totals_and_table(links: makeup.Links) -> None:
    used = pd.concat([_used("ridden", {0: 300, 2: 10}), _used("p", {0: 10, 1: 300})])
    totals = makeup.street_totals(used, links)
    assert list(totals.loc["elm-1"]) == [10, 300]  # columns p, ridden
    length = pd.Series(np.array([100.0, 100, 100, 100, 100]), index=links.ids)
    names = pd.Series(LINKS["name"].fillna("").to_numpy(), index=links.ids)
    table = makeup.street_table(totals, length, names, min_km=5)
    # Elm Street carries 30 ridden and 31 modelled trip-km; Main 1 ridden: under min_km.
    assert table.index.tolist() == ["Elm Street"]
    assert list(table.loc["Elm Street"]) == pytest.approx([31, 30])


def test_a_divided_street_counts_only_the_carriageway_ridden() -> None:
    # Two one-way carriageways of Broad Street, 16 m apart.
    divided = gpd.GeoDataFrame(
        {"physical_edge_id": ["north", "south"], "name": ["Broad Street"] * 2,
         "highway": ["primary"] * 2, "bike_facility": ["none"] * 2, "lts_furth": [4, 4],
         "oneway": ["yes", "yes"], "oneway_bicycle": [None, None]},
        geometry=[LineString([(8, 0), (8, 200)]), LineString([(-8, 200), (-8, 0)])],
        crs=3310,
    )  # fmt: skip
    links = makeup.prepare_links(divided)
    row, used = makeup.describe(LineString([(0, 0), (0, 200)]), links)
    assert links.ids[used].tolist() == ["north"]
    assert (row["matched_m"], row["against_m"]) == pytest.approx((200, 0))


def _links(**columns: list[object]) -> gpd.GeoDataFrame:
    geometry = [g for g in columns.pop("geometry") if isinstance(g, LineString)]
    n = len(geometry)
    base: dict[str, list[object]] = {
        "physical_edge_id": [f"e{i}" for i in range(n)], "name": [None] * n,
        "highway": ["residential"] * n, "bike_facility": ["none"] * n, "lts_furth": [2] * n,
        "oneway": ["no"] * n, "oneway_bicycle": [None] * n,
    }  # fmt: skip
    return gpd.GeoDataFrame(base | columns, geometry=geometry, crs=3310)


def test_modelled_edges_are_joined_in_the_direction_ridden() -> None:
    shapes = {"a": LineString([(0, 0), (100, 0)]), "b": LineString([(200, 0), (100, 0)])}
    line = makeup.oriented_line(("a", "b"), shapes, Point(0, 0))
    assert line is not None and list(line.coords) == [(0, 0), (100, 0), (200, 0)]
    back = makeup.oriented_line(("b", "a"), shapes, Point(200, 0))
    assert back is not None and list(back.coords) == [(200, 0), (100, 0), (0, 0)]
    # Alone, an edge drawn east but ridden west is turned to begin at the start.
    alone = makeup.oriented_line(("a",), shapes, Point(100, 0))
    assert alone is not None and list(alone.coords) == [(100, 0), (0, 0)]
    one_way = makeup.prepare_links(
        _links(physical_edge_id=["a"], oneway=["-1"], geometry=[shapes["a"]])
    )
    assert makeup.describe(alone, one_way)[0]["against_m"] == 0


def test_a_two_way_twin_does_not_excuse_riding_against_a_one_way() -> None:
    links = makeup.prepare_links(_links(
        name=["Broad Street"] * 2, oneway=["yes", "no"],
        geometry=[LineString([(0, 0), (200, 0)]), LineString([(0, 10), (200, 10)])],
    ))  # fmt: skip
    row, used = makeup.describe(LineString([(200, 0), (0, 0)]), links)
    assert sorted(links.ids[used]) == ["e0", "e1"]
    assert row["against_m"] == pytest.approx(200)


def test_each_crossing_of_one_busy_link_counts() -> None:
    links = makeup.prepare_links(_links(
        highway=["primary"], geometry=[LineString([(0, -1000), (0, 1000)])],
    ))  # fmt: skip
    line = LineString([(-100, 0), (100, 0), (100, 200), (-100, 200)])
    assert makeup.describe(line, links)[0]["crossings"] == 2


def test_turning_onto_an_unnamed_busy_street_is_not_crossing_it() -> None:
    links = makeup.prepare_links(_links(
        highway=["primary", "primary", "residential"],
        geometry=[LineString([(0, -100), (0, 0)]), LineString([(0, 0), (0, 100)]),
                  LineString([(-100, 0), (0, 0)])],
    ))  # fmt: skip
    row, _ = makeup.describe(LineString([(-100, 0), (0, 0), (0, 100)]), links)
    assert (row["turns"], row["crossings"]) == (1, 0)


def test_crossing_a_road_bikes_may_not_ride_still_counts() -> None:
    roads = _links(
        highway=["residential", "residential", "primary"],
        geometry=[LineString([(-100, 0), (0, 0)]), LineString([(0, 0), (100, 0)]),
                  LineString([(0, -100), (0, 100)])],
    )  # fmt: skip
    links = makeup.prepare_links(roads.iloc[:2], roads=roads)
    assert makeup.describe(LineString([(-100, 0), (100, 0)]), links)[0]["crossings"] == 1


def test_crossing_an_unnamed_busy_street_from_another_counts() -> None:
    links = makeup.prepare_links(_links(
        highway=["primary", "primary"],
        geometry=[LineString([(-200, 0), (200, 0)]), LineString([(0, -200), (0, 200)])],
    ))  # fmt: skip
    assert makeup.describe(LineString([(-200, 0), (200, 0)]), links)[0]["crossings"] == 1


def test_a_stretch_along_a_busy_link_does_not_hide_a_later_crossing_of_it() -> None:
    links = makeup.prepare_links(_links(
        highway=["primary"], geometry=[LineString([(0, -1000), (0, 1000)])],
    ))  # fmt: skip
    line = LineString([(0, 0), (0, 100), (100, 100), (100, 300), (-100, 300)])
    assert makeup.describe(line, links)[0]["crossings"] == 1


def test_prepared_paths_count_as_separated() -> None:
    links = makeup.prepare_links(_links(
        bike_facility=["separated path"], geometry=[LineString([(0, 0), (1000, 0)])],
    ))  # fmt: skip
    row, _ = makeup.describe(LineString([(0, 0), (1000, 0)]), links)
    assert row["separated_m"] == pytest.approx(1000)


def test_a_busy_street_just_short_of_the_line_is_still_crossed() -> None:
    links = makeup.prepare_links(_links(
        highway=["primary"], geometry=[LineString([(0, 0), (0, 1000)])],
    ))  # fmt: skip
    assert makeup.describe(LineString([(-100, -3), (100, -3)]), links)[0]["crossings"] == 1
