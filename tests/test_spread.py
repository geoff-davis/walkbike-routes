"""Spreading block counts over buildings, checked by hand."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from walkbike_routes.demand.spread import home_buildings, spread_blocks

CRS = 26910


def _blocks() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"block_id": ["A", "B"], "residents": [30.0, 12.0], "households": [9.0, 5.0]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100)],
        crs=CRS,
    )


def _buildings(
    rows: list[tuple[str, str | None, str | None, float | None, float | None, Polygon]],
) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"building_id": [r[0] for r in rows], "subtype": [r[1] for r in rows],
         "class": [r[2] for r in rows], "num_floors": [r[3] for r in rows],
         "height": [r[4] for r in rows]},
        geometry=[r[5] for r in rows], crs=CRS,
    )  # fmt: skip


def test_counts_follow_floor_area_and_add_back_to_the_block() -> None:
    buildings = _buildings([
        ("house", "residential", "house", None, None, box(10, 10, 20, 20)),  # 100 m2, 1 floor
        ("tall", None, None, None, 9.5, box(30, 30, 40, 40)),  # 100 m2, 3 floors from height
        ("flats", None, "apartments", 2, 30.0, box(50, 50, 60, 60)),  # num_floors wins: 2
        ("garage", None, "garage", None, None, box(70, 70, 80, 80)),
        ("shed", None, None, None, None, box(80, 10, 85, 15)),  # 25 m2, untagged: too small
        ("shop", "commercial", None, None, None, box(110, 10, 150, 50)),
    ])  # fmt: skip
    homes = spread_blocks(_blocks(), buildings, counts=["residents", "households"])
    a = homes[homes.block_id.eq("A")]
    assert a.residents.tolist() == pytest.approx([30 * 1 / 6, 30 * 3 / 6, 30 * 2 / 6])
    assert a.households.sum() == pytest.approx(9.0) and set(a.placed_by) == {"building"}
    assert a.home_id.tolist() == ["bA-0", "bA-1", "bA-2"]
    b = homes[homes.block_id.eq("B")]  # only a shop: the block stays one point
    assert b.home_id.tolist() == ["bB"] and b.placed_by.tolist() == ["block"]
    assert b.residents.tolist() == [12.0] and b.geometry.iloc[0].within(box(100, 0, 200, 100))


def test_small_buildings_count_when_tagged_as_homes() -> None:
    small = _buildings([
        ("cabin", None, "cabin", None, None, box(0, 0, 5, 5)),
        ("hut", None, None, None, None, box(10, 10, 15, 15)),
        ("bad floors", "residential", None, 0, -3.0, box(20, 20, 30, 30)),
        ("skyscraper", "residential", None, 400, None, box(40, 40, 50, 50)),
    ])  # fmt: skip
    kept = home_buildings(small, minimum_area_m2=40).set_index("building_id")
    assert list(kept.index) == ["cabin", "bad floors", "skyscraper"]
    assert kept.floor_area_m2.tolist() == pytest.approx([25.0, 100.0, 100.0 * 60])


def test_a_building_on_a_block_edge_is_counted_once_and_layers_must_match() -> None:
    edge = _buildings([("edge", "residential", None, None, None, box(95, 40, 105, 60))])
    homes = spread_blocks(_blocks(), edge, counts=["residents"])
    assert homes.residents.sum() == pytest.approx(42.0)
    with pytest.raises(ValueError, match="share a CRS"):
        spread_blocks(_blocks(), edge.to_crs(4326), counts=["residents"])
    with pytest.raises(ValueError, match="missing"):
        spread_blocks(_blocks(), edge, counts=["children"])


def test_a_building_on_the_shared_edge_of_two_blocks_is_still_a_home() -> None:
    edge = _buildings([("edge", "residential", None, None, None, box(95, 40, 105, 60))])
    homes = spread_blocks(_blocks(), edge, counts=["residents"])
    assert homes.placed_by.value_counts().to_dict() == {"building": 1, "block": 1}


def test_any_type_tag_that_is_not_a_home_type_rules_a_building_out() -> None:
    tagged = _buildings([
        ("house", None, "house", None, None, box(0, 0, 10, 10)),
        ("hospital", None, "hospital", None, None, box(20, 20, 60, 60)),
        ("church", None, "church", None, None, box(60, 0, 90, 30)),
        ("plain", None, None, None, None, box(0, 70, 10, 80)),
        ("hotel", "residential", "hotel", None, None, box(70, 70, 90, 90)),
    ])  # fmt: skip
    kept = home_buildings(tagged, minimum_area_m2=40)
    assert kept.building_id.tolist() == ["house", "plain"]


def test_a_block_with_no_count_is_refused() -> None:
    blocks = _blocks()
    blocks.loc[0, "residents"] = float("nan")
    with pytest.raises(ValueError, match="non-finite 'residents'"):
        spread_blocks(blocks, _buildings([]), counts=["residents"])
