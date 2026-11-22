#!/usr/bin/env python3
"""Generate the deterministic synthetic school-network fixture used by CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).with_name("mini_district.json")


def _node(node_id: str, x: float, y: float) -> dict[str, Any]:
    return {"node_id": node_id, "geometry": [x, y]}


def _edge(
    edge_id: str,
    u: str,
    v: str,
    name: str,
    coordinates: list[list[float]],
    length_m: float,
    *,
    highway: str = "residential",
    bike_allowed: bool = True,
    ped_allowed: bool = True,
    sidewalk_state: str = "present_manual",
    bike_facility: str = "none",
    speed_mph: int = 20,
    lanes: int = 2,
    traffic_band: str = "low",
    prototype02_gap_edge: bool = False,
) -> dict[str, Any]:
    return {
        "edge_id": edge_id,
        "u": u,
        "v": v,
        "name": name,
        "geometry": coordinates,
        "length_m": length_m,
        "highway": highway,
        "bike_allowed": bike_allowed,
        "ped_allowed": ped_allowed,
        "sidewalk_state": sidewalk_state,
        "bike_facility": bike_facility,
        "speed_mph": speed_mph,
        "lanes": lanes,
        "traffic_band": traffic_band,
        "traffic_volume_type": "observed fixture band",
        "oneway": "",
        "prototype02_gap_edge": prototype02_gap_edge,
    }


def _motor_edge(
    edge_id: str,
    u: str,
    v: str,
    coordinates: list[list[float]],
    edge_time_s: float,
) -> dict[str, Any]:
    return {
        "edge_id": edge_id,
        "osm_way_id": f"way_{edge_id}",
        "u": u,
        "v": v,
        "name": edge_id.replace("_", " ").title(),
        "geometry": coordinates,
        "length_m": 100.0,
        "edge_running_time_s": edge_time_s,
        "oneway": "yes",
        "junction_tag": "",
    }


def build_fixture() -> dict[str, Any]:
    """Return a tiny metric network whose model outcomes are known exactly."""
    nodes = [
        _node("W", 590000, 4137000),
        _node("M", 590100, 4137000),
        _node("G", 590200, 4137000),
        _node("NW", 590000, 4137080),
        _node("NM", 590100, 4137080),
        _node("NG", 590200, 4137080),
        _node("SW", 590000, 4136920),
        _node("SM", 590100, 4136920),
        _node("SG", 590200, 4136920),
        _node("CAR_O", 589900, 4137000),
        _node("CAR_C", 590000, 4137000),
        _node("CAR_B", 589900, 4137100),
        _node("CAR_G", 590000, 4137100),
    ]
    edges = [
        _edge(
            "maple_w_m",
            "W",
            "M",
            "Maple Avenue",
            [[590000, 4137000], [590100, 4137000]],
            100,
            highway="tertiary",
            bike_facility="bike lane",
            speed_mph=30,
            traffic_band="moderate",
            sidewalk_state="absent_manual",
        ),
        _edge(
            "maple_m_g_gap",
            "M",
            "G",
            "Maple Avenue",
            [[590100, 4137000], [590200, 4137000]],
            100,
            highway="tertiary",
            bike_facility="bike lane",
            speed_mph=30,
            traffic_band="moderate",
            sidewalk_state="absent_manual",
            prototype02_gap_edge=True,
        ),
        _edge(
            "west_north_connector",
            "W",
            "NW",
            "West Path",
            [[590000, 4137000], [590000, 4137080]],
            80,
            highway="path",
        ),
        _edge(
            "north_w_m",
            "NW",
            "NM",
            "Quiet North Street",
            [[590000, 4137080], [590100, 4137080]],
            100,
        ),
        _edge(
            "north_m_g",
            "NM",
            "NG",
            "Quiet North Street",
            [[590100, 4137080], [590200, 4137080]],
            100,
        ),
        _edge(
            "gate_north_connector",
            "G",
            "NG",
            "School Path",
            [[590200, 4137000], [590200, 4137080]],
            80,
            highway="path",
        ),
        _edge(
            "west_south_connector",
            "W",
            "SW",
            "West South Path",
            [[590000, 4137000], [590000, 4136920]],
            80,
            highway="path",
        ),
        _edge(
            "south_w_m",
            "SW",
            "SM",
            "Quiet South Street",
            [[590000, 4136920], [590100, 4136920]],
            100,
        ),
        _edge(
            "south_m_g",
            "SM",
            "SG",
            "Quiet South Street",
            [[590100, 4136920], [590200, 4136920]],
            100,
        ),
        _edge(
            "gate_south_connector",
            "G",
            "SG",
            "Rear School Path",
            [[590200, 4137000], [590200, 4136920]],
            80,
            highway="path",
        ),
    ]
    motor_edges = [
        _motor_edge(
            "car_short_1",
            "CAR_O",
            "CAR_C",
            [[589900, 4137000], [590000, 4137000]],
            10,
        ),
        _motor_edge(
            "car_short_2",
            "CAR_C",
            "CAR_G",
            [[590000, 4137000], [590000, 4137100]],
            10,
        ),
        _motor_edge(
            "car_bypass_1",
            "CAR_O",
            "CAR_B",
            [[589900, 4137000], [589900, 4137100]],
            15,
        ),
        _motor_edge(
            "car_bypass_2",
            "CAR_B",
            "CAR_G",
            [[589900, 4137100], [590000, 4137100]],
            15,
        ),
    ]
    return {
        "schema_version": 2,
        "fixture_id": "synthetic_school_network_v1",
        "description": (
            "Synthetic mini district for clean-checkout integration tests; "
            "coordinates and attributes are not observed Los Altos data."
        ),
        "crs": "EPSG:26910",
        "destinations": [
            {
                "destination_id": "fixture_destination",
                "label": "Fixture Destination",
                "destination_type": "explicit_access_points",
                "source": "synthetic fixture definition",
                "review_status": "synthetic_reviewed",
                "geometry": [590200, 4137040],
            }
        ],
        "access_points": [
            {
                "access_point_id": "fixture_bicycle_access",
                "destination_id": "fixture_destination",
                "node_id": "G",
                "mode": "bicycle",
                "direction": "both",
                "access_role": "synthetic bicycle threshold",
                "source": "synthetic fixture definition",
                "confidence": "high",
                "review_status": "synthetic_reviewed",
                "geometry": [590200, 4137000],
            },
            {
                "access_point_id": "fixture_pedestrian_access",
                "destination_id": "fixture_destination",
                "node_id": "NG",
                "mode": "pedestrian",
                "direction": "both",
                "access_role": "synthetic pedestrian threshold",
                "source": "synthetic fixture definition",
                "confidence": "high",
                "review_status": "synthetic_reviewed",
                "geometry": [590200, 4137080],
            },
            {
                "access_point_id": "fixture_motor_access",
                "destination_id": "fixture_destination",
                "node_id": "CAR_G",
                "mode": "car",
                "direction": "to_destination",
                "access_role": "synthetic motor threshold",
                "source": "synthetic fixture definition",
                "confidence": "high",
                "review_status": "synthetic_reviewed",
                "geometry": [590000, 4137100],
            },
        ],
        "generic_origins": [
            {
                "origin_id": "fixture_origin",
                "demand_weight": 5.0,
                "weight_unit": "synthetic_people_proxy",
                "source": "synthetic fixture definition",
                "geometry": [589900, 4137000],
            }
        ],
        "origin_attachments": [
            {
                "origin_id": "fixture_origin",
                "mode": "bicycle",
                "attached_node_id": "W",
                "snap_distance_m": 0.0,
                "network_attached": True,
                "attachment_policy": "synthetic fixture attachment",
            },
            {
                "origin_id": "fixture_origin",
                "mode": "pedestrian",
                "attached_node_id": "SW",
                "snap_distance_m": 0.0,
                "network_attached": True,
                "attachment_policy": "synthetic fixture attachment",
            },
            {
                "origin_id": "fixture_origin",
                "mode": "car",
                "attached_node_id": "CAR_O",
                "snap_distance_m": 0.0,
                "network_attached": True,
                "attachment_policy": "synthetic fixture attachment",
            },
        ],
        "nodes": nodes,
        "edges": edges,
        "crossings": [
            {
                "crossing_id": "west_reviewed_crossing",
                "node_id": "W",
                "crosses_maple": True,
                "supported_crossing_state": "supported_reviewed",
                "legal_crossing_state": "possible_intersection_crossing",
                "geometry": [590000, 4137000],
            },
            {
                "crossing_id": "center_possible_crossing",
                "node_id": "M",
                "crosses_maple": True,
                "supported_crossing_state": "unknown",
                "legal_crossing_state": "possible_intersection_crossing",
                "geometry": [590100, 4137000],
            },
            {
                "crossing_id": "center_side_street_marking",
                "node_id": "M",
                "crosses_maple": False,
                "supported_crossing_state": "supported_reviewed",
                "legal_crossing_state": "possible_intersection_crossing",
                "geometry": [590100, 4137000],
            },
        ],
        "population": {
            "attendance_boundary": {
                "school_id": "maple",
                "geometry": [
                    [589950, 4136880],
                    [590250, 4136880],
                    [590250, 4137120],
                    [589950, 4137120],
                    [589950, 4136880],
                ],
            },
            "blocks": [
                {
                    "GEOID20": "fixture_block_a",
                    "elementary_tk_6_age_4_11_proxy_2020": 12.0,
                    "internal_point": [590020, 4137000],
                    "geometry": [
                        [589960, 4136890],
                        [590080, 4136890],
                        [590080, 4137110],
                        [589960, 4137110],
                        [589960, 4136890],
                    ],
                },
                {
                    "GEOID20": "fixture_block_b",
                    "elementary_tk_6_age_4_11_proxy_2020": 8.0,
                    "internal_point": [590150, 4137000],
                    "geometry": [
                        [590090, 4136890],
                        [590190, 4136890],
                        [590190, 4137110],
                        [590090, 4137110],
                        [590090, 4136890],
                    ],
                },
            ],
            "residential_candidates": [
                {
                    "candidate_id": "home_a1",
                    "candidate_source": "synthetic residence",
                    "capacity_proxy": 1.0,
                    "GEOID20": "fixture_block_a",
                    "geometry": [589990, 4136970],
                },
                {
                    "candidate_id": "home_a2",
                    "candidate_source": "synthetic residence",
                    "capacity_proxy": 3.0,
                    "GEOID20": "fixture_block_a",
                    "geometry": [590050, 4137030],
                },
            ],
        },
        "motor_edges": motor_edges,
        "car_controls": [
            {
                "node_id": "CAR_C",
                "control_type": "signal",
                "control_cluster_id": "signal_fixture_1",
            }
        ],
        "car_origins": [
            {
                "origin_id": "fixture_car_origin",
                "resident_age_proxy": 5.0,
                "car_attached_node_id": "CAR_O",
                "car_snap_distance_m": 0.0,
                "car_network_attached": True,
                "geometry": [589900, 4137000],
            }
        ],
        "car_gates": [
            {
                "school_id": "fixture_school",
                "direction": "to_school",
                "gate_id": "fixture_car_gate",
                "node_id": "CAR_G",
                "geometry": [590000, 4137100],
            }
        ],
        "school_centers": [
            {
                "school_id": "fixture_school",
                "school_name": "Fixture School",
                "center_source": "synthetic fixture point",
                "geometry": [590000, 4137100],
            }
        ],
        "collisions": [
            {
                "CASE_ID": "fixture_bike",
                "collision_date": "2025-02-03",
                "year": 2025,
                "provisional_batch": True,
                "COLLISION_SEVERITY": "3",
                "NUMBER_KILLED": 0,
                "NUMBER_INJURED": 1,
                "PRIMARY_RD": "FIXTURE ROAD",
                "SECONDARY_RD": "SCHOOL WAY",
                "INTERSECTION": "Y",
                "TYPE_OF_COLLISION": "fixture",
                "MVIW": "fixture",
                "ped_involved": False,
                "bike_involved": True,
                "original_safetrec_geocode_available": True,
                "geometry_is_approximate": False,
                "geometry_provenance": "synthetic exact fixture",
                "intersection_match_gap_m": None,
                "geometry": [590010, 4137105],
            },
            {
                "CASE_ID": "fixture_ped",
                "collision_date": "2024-04-05",
                "year": 2024,
                "provisional_batch": True,
                "COLLISION_SEVERITY": "2",
                "NUMBER_KILLED": 0,
                "NUMBER_INJURED": 1,
                "PRIMARY_RD": "FIXTURE ROAD",
                "SECONDARY_RD": "SCHOOL WAY",
                "INTERSECTION": "Y",
                "TYPE_OF_COLLISION": "fixture",
                "MVIW": "fixture",
                "ped_involved": True,
                "bike_involved": False,
                "original_safetrec_geocode_available": False,
                "geometry_is_approximate": True,
                "geometry_provenance": "synthetic approximate fixture",
                "intersection_match_gap_m": 0.0,
                "geometry": [590010, 4137105],
            },
            {
                "CASE_ID": "fixture_far",
                "collision_date": "2023-01-02",
                "year": 2023,
                "provisional_batch": False,
                "COLLISION_SEVERITY": "4",
                "NUMBER_KILLED": 0,
                "NUMBER_INJURED": 0,
                "PRIMARY_RD": "DISTANT ROAD",
                "SECONDARY_RD": "FAR WAY",
                "INTERSECTION": "Y",
                "TYPE_OF_COLLISION": "fixture",
                "MVIW": "fixture",
                "ped_involved": False,
                "bike_involved": False,
                "original_safetrec_geocode_available": True,
                "geometry_is_approximate": False,
                "geometry_provenance": "synthetic exact fixture",
                "intersection_match_gap_m": None,
                "geometry": [591000, 4137100],
            },
        ],
        "collision_parties": [
            {"CASE_ID": "fixture_bike", "PARTY_TYPE": "1"},
            {"CASE_ID": "fixture_bike", "PARTY_TYPE": "4"},
            {"CASE_ID": "fixture_ped", "PARTY_TYPE": "1"},
            {"CASE_ID": "fixture_ped", "PARTY_TYPE": "2"},
            {"CASE_ID": "fixture_far", "PARTY_TYPE": "1"},
            {"CASE_ID": "fixture_far", "PARTY_TYPE": "3"},
        ],
        "expected": {
            "lts_best": {"maple_m_g_gap": 2, "north_w_m": 1},
            "bike_base_edge_ids": [
                "west_north_connector",
                "north_w_m",
                "north_m_g",
                "gate_north_connector",
            ],
            "bike_base_length_m": 360.0,
            "bike_improved_edge_ids": ["maple_w_m", "maple_m_g_gap"],
            "bike_improved_length_m": 200.0,
            "walk_south_to_north_gate_length_m": 372.0,
            "population_total": 20.0,
            "population_equal_weights_block_a": [6.0, 6.0],
            "population_capacity_weights_block_a": [3.0, 9.0],
            "population_fallback_weight_block_b": 8.0,
            "car_edge_time_only_edge_ids": ["car_short_1", "car_short_2"],
            "car_central_edge_ids": ["car_bypass_1", "car_bypass_2"],
            "car_central_time_s": 33.0,
            "near_school_collision_ids": ["fixture_bike", "fixture_ped"],
        },
    }


def render_fixture() -> str:
    return json.dumps(build_fixture(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail rather than write if the committed fixture is stale",
    )
    args = parser.parse_args()
    rendered = render_fixture()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != rendered:
            print(f"{OUTPUT} is stale; regenerate it with {Path(__file__).as_posix()}")
            return 1
        return 0
    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
