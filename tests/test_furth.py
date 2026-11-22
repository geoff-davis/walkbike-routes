"""Furth's LTS v2.2 tables, checked cell by cell against the published PDF."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from walkbike_routes.network.bicycle import BicycleProfile
from walkbike_routes.network.furth import classify_furth
from walkbike_routes.network.osm_streets import (
    bike_lane_side,
    length_m,
    parking_sides,
    way_attributes,
)


def street(**tags: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"highway": "residential", "bike_facility": "none", "oneway": "no"}
    return base | tags


@pytest.mark.parametrize(
    ("edge", "lts"),
    [
        # Mixed traffic, unlaned 2-way: 751-1500 at 25 mph is LTS 1; 3001+ at 30 mph is LTS 3.
        (street(speed_mph=25, lanes=2, lane_markings="no", traffic_aadt=1200), 1),
        (street(speed_mph=30, lanes=2, lane_markings="no", traffic_aadt=3500), 3),
        # 1 lane each way with a centre line: 1001-1500 at 25 mph is LTS 2; 1501+ is LTS 3.
        (street(speed_mph=25, lanes=2, lane_markings="yes", traffic_aadt=1200), 2),
        (street(speed_mph=25, lanes=2, lane_markings="yes", traffic_aadt=1600), 3),
        # Narrow one-way: 601-1000 at 20 mph is LTS 2.
        (street(speed_mph=20, lanes=1, oneway="yes", traffic_aadt=800), 2),
        # 2 lanes each way: up to 8,000 at 30 mph is LTS 3; more is LTS 4.
        (street(highway="secondary", speed_mph=30, lanes=4, traffic_aadt=8000), 3),
        (street(highway="secondary", speed_mph=30, lanes=4, traffic_aadt=8001), 4),
        # 3+ lanes each way at 25 mph is LTS 3.
        (street(highway="primary", speed_mph=25, lanes=6, traffic_aadt=40000), 3),
        # Bike lane, no parking, 1 lane each way, 6+ ft at 30 mph is LTS 1 ...
        (street(highway="secondary", bike_facility="lane", speed_mph=30, lanes=2,
                traffic_aadt=12000, parking_sides=0, bike_lane_width_m=1.9), 1),
        # ... and under 6 ft at 45 mph is LTS 3; at 50 mph LTS 4.
        (street(highway="secondary", bike_facility="lane", speed_mph=45, lanes=2,
                traffic_aadt=12000, parking_sides=0), 3),
        (street(highway="secondary", bike_facility="lane", speed_mph=50, lanes=2,
                traffic_aadt=12000, parking_sides=0), 4),
        # Beside parking, 1 lane each way, reach under 15 ft at 35 mph is LTS 3.
        (street(highway="tertiary", bike_facility="lane", speed_mph=35, lanes=2,
                traffic_aadt=6000, parking_sides=2), 3),
        # Beside parking, 2 lanes each way (2-way) at 25 mph: reach under 15 ft is LTS 3.
        (street(highway="secondary", bike_facility="lane", speed_mph=25, lanes=4,
                traffic_aadt=20000, bike_lane_parking="yes"), 3),
        # A bike lane under 4 ft counts as mixed traffic (note 2).
        (street(highway="tertiary", bike_facility="lane", speed_mph=30, lanes=2,
                traffic_aadt=6000, parking_sides=0, bike_lane_width_m=1.0), 3),
        # Mixed traffic is used where it is lower (note 4): a quiet street with a narrow lane.
        (street(bike_facility="lane", speed_mph=25, lanes=2, lane_markings="no",
                traffic_aadt=500, parking_sides=2), 1),
        # Separated facilities and paths are LTS 1.
        (street(highway="primary", bike_facility="track", speed_mph=45, lanes=6), 1),
        (street(highway="cycleway"), 1),
    ],
)  # fmt: skip
def test_cells_match_the_published_tables(edge: dict[str, Any], lts: int) -> None:
    assert classify_furth(edge).lts == lts


def test_defaults_are_named_and_lean_stressful() -> None:
    # An untagged tertiary road: 30 mph, 2 lanes, a centre line, 4,000 a day: LTS 3.
    result = classify_furth(street(highway="tertiary"))
    assert result.lts == 3
    assert set(result.defaults) == {"speed", "daily traffic", "lanes", "centre line"}
    # An untagged residential street: 25 mph, unlaned, 1,000 a day: LTS 1.
    assert classify_furth(street()).lts == 1
    # A counted tertiary road keeps its count: 900 a day with a centre line at 30 mph is LTS 2.
    counted = classify_furth(street(highway="tertiary", speed_mph=30, lanes=2, traffic_aadt=900))
    assert counted.lts == 2 and "daily traffic" not in counted.defaults


def test_a_wide_one_way_uses_the_centre_line_row() -> None:
    narrow = street(speed_mph=25, lanes=1, oneway="yes", traffic_aadt=1200, parking_sides=0)
    assert classify_furth(narrow).lts == 3  # narrow one-way, 1001+
    assert classify_furth(narrow | {"width_m": 5.0}).lts == 2  # 16 ft, no parking: wide
    assert classify_furth(narrow | {"width_m": 5.0, "parking_sides": 1}).lts == 3  # under 22 ft


def test_a_centre_turn_lane_is_not_a_through_lane() -> None:
    edge = street(highway="tertiary", speed_mph=30, lanes=3, traffic_aadt=6000)
    assert classify_furth(edge).lts == 3  # 1 lane each way, not 3+
    assert classify_furth(edge | {"lanes_both_ways": 1}).lts == 3


def test_missing_values_from_frames_fall_back_to_defaults() -> None:
    nan = float("nan")
    edge = street(speed_mph=nan, lanes=nan, traffic_aadt=nan, parking_sides=nan, lane_markings=nan)
    assert classify_furth(edge).lts == 1


def test_osm_tags_feed_the_tables() -> None:
    assert parking_sides({"parking:both": "lane"}) == 2
    assert parking_sides({"parking:lane:right": "parallel"}) == 1
    assert parking_sides({"parking:right": "no", "parking:left": "no"}) == 0
    assert parking_sides({"parking:right": "no"}) is None
    assert parking_sides({}) is None
    assert length_m("1.5") == 1.5 and length_m("2 m") == 2.0
    assert length_m("6'") == pytest.approx(1.8288)
    assert length_m('5 ft 6"') == pytest.approx(1.6764)
    assert length_m("narrow") is None
    tags = {"highway": "tertiary", "lane_markings": "yes", "cycleway:right:width": "1.8",
            "lanes:both_ways": "1", "width": "12"}  # fmt: skip
    attributes = way_attributes(tags)
    assert attributes["lane_markings"] == "yes" and attributes["lanes_both_ways"] == 1
    assert attributes["bike_lane_width_m"] == 1.8 and attributes["width_m"] == 12


def test_a_profile_chooses_which_levels_to_read() -> None:
    assert BicycleProfile("p").lts_field == "lts_best"
    assert BicycleProfile("p", lts_criteria="furth").lts_field == "lts_furth"
    with pytest.raises(ValueError, match="lts_criteria"):
        BicycleProfile("p", lts_criteria="dutch")


def lane_street(**tags: Any) -> dict[str, Any]:
    base = {"highway": "secondary", "bike_facility": "lane", "speed_mph": 30, "traffic_aadt": 12000}
    return street(**base) | tags


def test_a_tagged_centre_turn_lane_widens_the_bike_lane_even_without_a_lane_total() -> None:
    # Default lanes (one each way) plus a turn lane: 1.3 m + 2 ft is over 6 ft, LTS 1.
    edge = lane_street(lanes_both_ways=1, bike_lane_parking="no", bike_lane_width_m=1.3)
    assert classify_furth(edge).lts == 1


def test_the_turn_lane_bonus_does_not_rescue_a_lane_under_4_ft() -> None:
    edge = lane_street(lanes=3, lanes_both_ways=1, bike_lane_parking="no", bike_lane_width_m=0.9144)
    assert classify_furth(edge).lts == 3  # mixed traffic: centre line, 12,000/day, 30 mph


def test_only_parking_beside_the_bike_lane_counts() -> None:
    tags = {"highway": "secondary", "cycleway:right": "lane", "cycleway:right:width": "2",
            "parking:right": "no", "parking:left": "lane"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_parking"] == "no" and side["bike_lane_width_m"] == 2.0
    assert classify_furth(lane_street(lanes=2, **side)).lts == 1
    assert (
        bike_lane_side({"highway": "secondary", "cycleway:right": "lane"})["bike_lane_parking"]
        == ""
    )


def test_a_marked_buffer_counts_toward_the_bike_lane_width() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:both:width": "1.3",
            "cycleway:both:buffer": "0.7", "parking:both": "no"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_width_m"] == pytest.approx(2.0)
    assert classify_furth(lane_street(lanes=2, **side)).lts == 1


def test_mapped_reach_sets_the_parking_row() -> None:
    tags = {"highway": "secondary", "cycleway:right": "lane", "cycleway:right:width": "2.2",
            "parking:right": "lane", "parking:right:width": "2.5", "parking:left": "no"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_parking"] == "yes" and side["bike_lane_reach_m"] == pytest.approx(4.7)
    assert classify_furth(lane_street(lanes=2, speed_mph=25, **side)).lts == 1  # 15 ft or more
    short = lane_street(
        lanes=2, bike_lane_parking="yes", bike_lane_width_m=1.3, bike_lane_reach_m=3.1
    )
    assert classify_furth(short).lts == 3  # reach under 12 ft: mixed traffic


def test_missing_access_is_not_a_ban() -> None:
    assert classify_furth(street(bike_allowed=None)).lts == 1
    assert classify_furth(street(bike_allowed=pd.NA)).lts == 1
    assert classify_furth(street(bike_allowed=False)).lts == 4


def test_each_side_is_read_on_its_own() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:left:width": "1.7",
            "cycleway:right:width": "1.3", "cycleway:right:buffer": "0.3"}  # fmt: skip
    assert bike_lane_side(tags)["bike_lane_width_m"] == pytest.approx(1.6)  # the narrower side
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:right": "no",
            "cycleway:left:width": "2", "parking:left": "no", "parking:right": "lane"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_parking"] == "no" and side["bike_lane_width_m"] == 2.0


def test_turn_lane_tags_rule_over_inference() -> None:
    edge = lane_street(lanes=3, lanes_both_ways=0, lane_markings="yes", bike_lane_parking="no",
                       bike_lane_width_m=1.3)  # fmt: skip
    assert classify_furth(edge).lts == 2  # no turn lane, so no 2 ft bonus
    centre = street(speed_mph=25, lanes=3, lanes_both_ways=1, traffic_aadt=2000)
    assert classify_furth(centre).lts == 3  # a turn lane means a centre line


def test_reach_is_worked_out_side_by_side() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "parking:both": "lane",
            "cycleway:left:width": "1.3", "parking:left:width": "3.4",
            "cycleway:right:width": "2.2", "parking:right:width": "2.5"}  # fmt: skip
    assert bike_lane_side(tags)["bike_lane_reach_m"] == pytest.approx(4.7)
    side = bike_lane_side(tags)
    assert classify_furth(lane_street(lanes=2, speed_mph=25, **side)).lts == 1


def test_a_side_without_a_width_makes_the_width_unknown() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:right:width": "2",
            "parking:both": "no"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_width_m"] is None
    assert classify_furth(lane_street(lanes=2, **side)).lts == 2  # under 6 ft by default


def test_three_through_lanes_put_two_in_one_direction() -> None:
    edge = street(highway="secondary", lanes=3, lanes_both_ways=0, lane_markings="yes",
                  speed_mph=30, traffic_aadt=12000)  # fmt: skip
    assert classify_furth(edge).lts == 4  # 2 lanes, over 8,000 a day, 30 mph


def test_every_one_way_spelling_puts_a_plain_bike_lane_on_the_right() -> None:
    for value in ("yes", "1", "true"):
        tags = {"highway": "secondary", "oneway": value, "cycleway": "lane",
                "cycleway:width": "2", "parking:right": "no", "parking:left": "lane"}  # fmt: skip
        assert bike_lane_side(tags)["bike_lane_parking"] == "no", value


def test_parking_beside_one_bike_lane_only_rates_both_sides() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:left:width": "1.3",
            "parking:left": "no", "cycleway:right:width": "2.2", "parking:right": "lane",
            "parking:right:width": "2.5"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_parking"] == "some"
    edge = lane_street(lanes=2, speed_mph=25, lane_markings="yes", **side)
    assert classify_furth(edge).lts == 2  # the unparked side's narrow lane


def test_a_reverse_one_way_has_its_bike_lane_on_the_left() -> None:
    tags = {"highway": "secondary", "oneway": "-1", "cycleway": "lane", "cycleway:width": "2",
            "parking:left": "no", "parking:right": "lane"}  # fmt: skip
    assert bike_lane_side(tags)["bike_lane_parking"] == "no"


def test_the_no_parking_width_comes_from_the_sides_without_parking() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:left:width": "1.5",
            "parking:left": "lane", "parking:left:width": "3.1", "cycleway:right:width": "2",
            "parking:right": "no"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_width_m"] == 2.0
    assert classify_furth(lane_street(lanes=2, speed_mph=25, **side)).lts == 1


def test_a_known_lane_under_4_ft_is_not_hidden_by_an_unknown_side() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:right:width": "1.0",
            "parking:both": "no"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_width_m"] == 1.0
    assert classify_furth(lane_street(lanes=2, **side)).lts == 3


def test_a_known_reach_under_12_ft_is_not_hidden_by_an_unknown_side() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:both:width": "1.3",
            "parking:both": "lane", "parking:right:width": "2.0"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_reach_m"] == pytest.approx(3.3)
    assert classify_furth(lane_street(lanes=2, **side)).lts == 3


def test_a_plain_cycleway_tag_still_covers_the_other_side() -> None:
    tags = {"highway": "secondary", "cycleway": "lane", "cycleway:right": "lane",
            "cycleway:left:width": "1.3", "cycleway:right:width": "2", "parking:both": "no"}  # fmt: skip
    assert bike_lane_side(tags)["bike_lane_width_m"] == 1.3


def test_an_untagged_side_beside_a_parked_one_is_rated_both_ways() -> None:
    tags = {"highway": "secondary", "cycleway:both": "lane", "cycleway:left:width": "1.3",
            "cycleway:right:width": "2.2", "parking:right": "lane", "parking:right:width": "2.5"}  # fmt: skip
    side = bike_lane_side(tags)
    assert side["bike_lane_parking"] == "some"
    assert classify_furth(lane_street(lanes=2, speed_mph=25, **side)).lts == 2


def test_directional_lane_tags_set_the_lanes_each_way() -> None:
    tags = {"highway": "secondary", "lanes": "3", "lanes:forward": "2", "lanes:backward": "1"}
    attributes = way_attributes(tags)
    assert attributes["lanes_per_direction"] == 2 and attributes["lanes_both_ways"] == 0
    edge = street(highway="secondary", lanes=3, lane_markings="yes", speed_mph=30,
                  traffic_aadt=12000, **{k: attributes[k] for k in ("lanes_per_direction", "lanes_both_ways")})  # fmt: skip
    assert classify_furth(edge).lts == 4  # 2 lanes, over 8,000 a day, 30 mph
    turn = way_attributes(tags | {"lanes": "4"})  # 2 + 1 + a centre turn lane
    assert turn["lanes_both_ways"] == 1


def test_one_tagged_direction_leaves_the_rest_to_the_other() -> None:
    tags = {"highway": "secondary", "lanes": "4", "lanes:both_ways": "1", "lanes:forward": "1"}
    assert way_attributes(tags)["lanes_per_direction"] == 2


def test_one_direction_without_a_turn_lane_tag_leaves_the_rest_as_through_lanes() -> None:
    tags = {"highway": "secondary", "lanes": "4", "lanes:forward": "1"}
    assert way_attributes(tags)["lanes_per_direction"] == 3


def test_a_shoulder_of_known_width_uses_the_bike_lane_table() -> None:
    tags = {"highway": "secondary", "cycleway": "shoulder", "cycleway:width": "2"}
    width = way_attributes(tags)["shoulder_width_m"]
    assert width == 2.0
    edge = street(
        highway="secondary", lanes=2, speed_mph=45, traffic_aadt=12000, shoulder_width_m=width
    )
    assert classify_furth(edge).lts == 3  # 6 ft or wider, 1 lane each way, 45 mph
    assert classify_furth(edge | {"shoulder_width_m": None}).lts == 4  # mixed traffic
    assert way_attributes({"highway": "primary", "shoulder": "both"})["shoulder_width_m"] is None
