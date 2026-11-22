"""Checking route choice against recorded trips (wbvalidate.routes), on made-up trips."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import LineString, MultiLineString, box
from shapely.geometry.base import BaseGeometry
from wbvalidate import cli as vcli
from wbvalidate import cyclephilly, route_report, routes, traces

from walkbike_routes import config, demo
from walkbike_routes import study as study_runs
from walkbike_routes.costs import presets

S = demo.SPACING_M
ROW = 4  # E Street, a quiet street with no bike lane


def _at(row: float, column: float) -> tuple[float, float]:
    return (demo.ORIGIN[0] + column * S, demo.ORIGIN[1] + row * S)


STRAIGHT = LineString([_at(ROW, 2), _at(ROW, 7)])  # 600 m along E Street
DETOUR = LineString([_at(ROW, 2), _at(ROW + 1, 2), _at(ROW + 1, 7), _at(ROW, 7)])  # 840 m


def _study(tmp_path: Path, bike: list[str]) -> config.Study:
    raw = {
        "study": "Demo town trips", "synthetic": True, "area": {"source": "demo"},
        "destinations": [], "modes": ["bike"], "presets": {"bike": bike},
        "time_caps_min": {"bike": 20}, "demand": {"weight": "residents"},
        "network_flows": {"destinations": 2},
    }  # fmt: skip
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    return config.load(path)


def _trips(
    lines: Mapping[str, BaseGeometry], users: dict[str, str] | None = None
) -> gpd.GeoDataFrame:
    ids = list(lines)
    return gpd.GeoDataFrame(
        {
            "trip_id": ids,
            "user_id": [(users or {}).get(i, "rider-1") for i in ids],
            "purpose": "Commute",
            "group": "",
        },
        geometry=list(lines.values()),
        crs=demo.CRS,
    )


def _route(tmp_path: Path, preset_name: str, lines: Mapping[str, BaseGeometry]) -> pd.DataFrame:
    town = demo.build()
    study = _study(tmp_path, [preset_name])
    trips = _trips(lines)
    ends = routes.trip_ends(trips.geometry, trips.trip_id)
    context = routes.context(study, town.links, town.nodes, ends, tmp_path)
    preset = presets.load("bike", preset_name)
    built = study_runs.mode_network(context, "bike", preset)
    return routes.route_trips(built, preset, trips, context.edges).set_index("trip_id")


def test_overlap_is_the_share_of_each_line_near_the_other() -> None:
    assert routes.overlap(STRAIGHT, STRAIGHT, 20) == pytest.approx((1.0, 1.0))
    # Only the detour's first and last 20 m are within 20 m of the straight
    # route, and only the straight route's first and last 20 m near the detour.
    observed, modelled = routes.overlap(DETOUR, STRAIGHT, 20)
    assert observed == pytest.approx(40 / 840)
    assert modelled == pytest.approx(40 / 600)
    assert all(math.isnan(v) for v in routes.overlap(STRAIGHT, LineString(), 20))


def test_the_shortest_route_matches_a_trip_ridden_along_it(tmp_path: Path) -> None:
    found = _route(tmp_path, "shortest", {"straight": STRAIGHT, "detour": DETOUR})
    assert set(found.route_status) == {routes.ROUTED}
    assert found.loc["straight", "route_m"] == pytest.approx(600, abs=1)
    assert found.loc["straight", routes.column(20)] == pytest.approx(1.0)
    # The detour's ends are the straight trip's ends, so its route is the same.
    assert found.loc["detour", routes.column(20)] == pytest.approx((40 / 840 + 40 / 600) / 2)
    assert found.loc["detour", "observed_20m"] == pytest.approx(40 / 840)


def test_a_preset_with_turn_costs_routes_to_the_end_from_any_side(tmp_path: Path) -> None:
    short = LineString([_at(ROW, 2), _at(ROW, 5)])  # three quiet blocks
    found = _route(tmp_path, "broach-2011", {"short": short, "across": STRAIGHT})
    assert set(found.route_status) == {routes.ROUTED}
    assert found.loc["short", routes.column(20)] == pytest.approx(1.0)
    # Further on, E Street crosses Central Avenue, an arterial, with no signal;
    # the model takes the bike lane on D Street instead, a longer route.
    across = found.loc["across"]
    assert across.route_m > STRAIGHT.length
    assert across[routes.column(20)] < 0.5


def test_a_far_end_joins_the_network_and_says_how_far_it_moved(tmp_path: Path) -> None:
    # Ends join the nearest street however far, as homes do: a trace ending
    # 4 km beyond the town is routed, and the distance moved is kept.
    far = LineString([_at(ROW, 2), _at(ROW, 40)])
    same = LineString([_at(ROW, 2), _at(ROW + 0.05, 2.05)])
    found = _route(tmp_path, "shortest", {"far": far, "same": same})
    beyond = (40 - (demo.SIZE - 1)) * S  # from the last intersection on E Street
    assert found.loc["far", "route_status"] == routes.ROUTED
    assert found.loc["far", "start_moved_m"] == pytest.approx(0, abs=1)
    assert found.loc["far", "end_moved_m"] == pytest.approx(beyond, abs=1)
    assert found.loc["same", "route_status"] == routes.SAME_NODE


def test_the_report_counts_trips_with_an_end_moved_far(tmp_path: Path) -> None:
    def row(trip: str, preset: str, start: float, end: float) -> dict[str, object]:
        return {"trip_id": trip, "preset": preset, "status": traces.USED,
                "start_moved_m": start, "end_moved_m": end}  # fmt: skip

    def line(rows: list[dict[str, object]]) -> str:
        return route_report._moved_para(pd.DataFrame(rows), ["a", "b"]).text

    near = [row("t1", p, 5, 12) for p in ("a", "b")]
    assert line(near).endswith("to reach the network: none.")
    both = [*near, row("t2", "a", 3, 400), row("t2", "b", 3, 380)]
    assert line(both).endswith(": 1 under every preset (farthest 400 m).")
    one = [*near, row("t2", "a", 3, 400), row("t2", "b", 3, 20)]
    assert line(one).endswith(": a 1, b 0 (farthest 400 m).")
    # A trip the filters left out is not counted, however far its end.
    skipped = [*near, {**row("t3", "a", 900, 0), "status": "too short"}]
    assert line(skipped).endswith(": none.")


def test_the_filters_look_only_at_the_trace() -> None:
    start = _at(ROW, 2)
    lines = {
        "used": STRAIGHT,
        "short": LineString([start, _at(ROW, 3)]),
        "long": LineString([start, (start[0] + 16_000, start[1])]),
        "loop": LineString([start, _at(ROW, 10), _at(ROW + 0.1, 10), _at(ROW + 0.1, 2.5)]),
        "outside": LineString([_at(ROW, 20), _at(ROW, 25)]),
        "broken": MultiLineString([[start, _at(ROW, 3)], [_at(ROW, 4), _at(ROW, 6)]]),
        "joined": MultiLineString([[start, _at(ROW, 3)], [_at(ROW, 7), _at(ROW, 3.1)]]),
        "exercise": STRAIGHT,
    }
    trips = _trips(lines)
    trips.loc[trips.trip_id.eq("exercise"), "purpose"] = "Exercise"
    within = box(*_at(-1, -1), *_at(13, 13))
    status, joined = traces.eligible(trips, within)
    found = dict(zip(trips.trip_id, status, strict=True))
    assert found == {
        "used": traces.USED, "short": traces.TOO_SHORT, "long": traces.OUTSIDE,
        "loop": traces.LOOP, "outside": traces.OUTSIDE, "broken": traces.BROKEN,
        "joined": traces.USED, "exercise": traces.LEISURE,
    }  # fmt: skip
    status, _ = traces.eligible(trips)  # with no area, length decides
    assert dict(zip(trips.trip_id, status, strict=True))["long"] == traces.TOO_LONG
    # The second piece, recorded backwards, is turned round to follow the first.
    line = joined[trips.trip_id.eq("joined").to_numpy()].iloc[0]
    assert line.coords[0] == start and line.coords[-1] == _at(ROW, 7)
    assert line.length == pytest.approx(4.9 * S + 0.1 * S, abs=1)


def test_a_traces_file_is_checked() -> None:
    trips = _trips({"a": STRAIGHT})
    with pytest.raises(traces.TracesError, match="missing columns"):
        traces.check(trips.drop(columns="user_id"))
    with pytest.raises(traces.TracesError, match="unique"):
        traces.check(pd.concat([trips, trips]))
    points = trips.assign(geometry=trips.geometry.centroid)
    with pytest.raises(traces.TracesError, match="line"):
        traces.check(points)


def test_scores_resample_riders_not_trips() -> None:
    # Rider A's three trips follow the preset's routes, rider B's one trip the baseline's.
    rows = pd.DataFrame(
        [(f"a{i}", "A", p, 1.0 if p == "keen" else 0.0) for i in range(3)
         for p in ("keen", "shortest")]
        + [("b0", "B", p, 0.0 if p == "keen" else 1.0) for p in ("keen", "shortest")],
        columns=["trip_id", "user_id", "preset", routes.column(20)],
    )  # fmt: skip
    first = {s.preset: s for s in routes.route_scores(rows)}
    keen = first["keen"]
    assert (keen.trips, keen.riders) == (4, 2)
    assert keen.mean == pytest.approx(0.75)
    assert keen.rider_mean == pytest.approx(0.5)  # (1 + 0) / 2
    assert keen.close == pytest.approx(0.75)
    assert keen.difference == pytest.approx(0.75 - 0.25)
    # Drawing riders gives a mean of 0 (B twice), 0.75 or 1 (A twice): the
    # interval spans 0 to 1. Drawing trips would give quarters in between.
    assert (keen.low, keen.high) == (0.0, 1.0)
    assert math.isnan(first["shortest"].difference)
    assert repr(routes.route_scores(rows)) == repr(routes.route_scores(rows))  # a fixed seed


def test_cyclephilly_records_become_traces() -> None:
    records = gpd.GeoDataFrame(
        {"TripID": [1, 2], "UserId": [7, 7], "Purpose": ["Commute", None],
         "rider_type": ["no data", "Strong & Fearless"], "age": ["25 - 34", "no data"]},
        geometry=[STRAIGHT, DETOUR], crs=demo.CRS,
    )  # fmt: skip
    found = cyclephilly.frame(records)
    assert list(found.columns) == [*traces.COLUMNS, "geometry"]  # riders' other answers dropped
    assert list(found.group) == ["", "Strong & Fearless"]
    assert list(found.purpose) == ["Commute", ""]


def test_routes_writes_the_report_from_a_run(tmp_path: Path) -> None:
    town = demo.build()
    out = tmp_path / "out"
    out.mkdir()
    town.links.to_parquet(out / "links.parquet")
    town.nodes.to_parquet(out / "nodes.parquet")
    study_path = tmp_path / "study.yaml"
    _study(tmp_path, ["legacy", "shortest"])  # writes study.yaml
    users = {f"t{i}": f"rider-{i % 3}" for i in range(6)}
    lines = {f"t{i}": STRAIGHT if i % 2 else DETOUR for i in range(6)}
    path = tmp_path / "trips.geojson"
    _trips(lines, users).to_crs(4326).to_file(path)
    vcli.main(["routes", "--config", str(study_path), "--out", str(out), "--traces", str(path)])
    folder = out / route_report.FOLDER
    table = pd.read_parquet(folder / "trips.parquet")
    assert len(table) == 6 * 2 and set(table.route_status) == {routes.ROUTED}
    text = (folder / "report.md").read_text()
    assert "| shortest | 6 | 3 |" in text
    assert "Better than shortest" in text and "Recorded trips: trips.geojson" in text
    assert "made-up" in text.lower()  # the synthetic banner comes from the study, not a manifest
    assert "What the routes are made of" in text and "Trips per street" in text
    assert "end moved more than 50 m to reach the network: none." in text
    streets = gpd.read_parquet(folder / "streets.parquet")
    # The three detours turn off E Street at once; both presets send all six trips along it.
    assert streets[["ridden", "legacy", "shortest"]].max().tolist() == [3, 6, 6]


def test_the_philadelphia_study_reads() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "philadelphia_core.yaml"
    study = config.load(path)
    assert study.area.boundary_file is not None and study.area.boundary_file.exists()
    assert "shortest" in study.presets["bike"]


def test_a_ridden_stretch_counts_each_time_it_is_ridden() -> None:
    # Riding 600 m, back 100 m, then on to 1,000 m: every metre is on the route.
    trace = LineString([(0, 0), (600, 0), (500, 0), (1000, 0)])
    assert routes.overlap(trace, LineString([(0, 0), (1000, 0)]), 20) == pytest.approx((1, 1))


def test_pieces_join_only_in_their_recorded_order() -> None:
    # The second piece starts where the first began: joining needs a 600 m jump.
    assert traces.joined(MultiLineString([[(0, 0), (600, 0)], [(0, 0), (-600, 0)]])) is None
    joined = traces.joined(MultiLineString([[(0, 0), (600, 0)], [(600, 0), (900, 0)]]))
    assert joined is not None and joined.length == pytest.approx(900)


def test_a_joined_gap_outside_the_area_puts_the_trip_outside() -> None:
    pieces = MultiLineString([[(0, 0), (480, 0)], [(520, 0), (1000, 0)]])
    trips = gpd.GeoDataFrame(
        {"trip_id": ["a"], "user_id": ["u"], "purpose": ["Commute"], "group": [""]},
        geometry=[pieces], crs=demo.CRS,
    )  # fmt: skip
    gap = box(490, -10, 510, 10)
    area = box(-10, -10, 1010, 10).difference(gap)
    status, _ = traces.eligible(trips, area)
    assert status.tolist() == [traces.OUTSIDE]
    assert traces.eligible(trips, box(-10, -10, 1010, 10))[0].tolist() == [traces.USED]


def test_routes_reports_when_no_trip_is_eligible(tmp_path: Path) -> None:
    town = demo.build()
    out = tmp_path / "out"
    out.mkdir()
    town.links.to_parquet(out / "links.parquet")
    town.nodes.to_parquet(out / "nodes.parquet")
    _study(tmp_path, ["shortest"])
    path = tmp_path / "trips.geojson"
    _trips({"short": LineString([_at(ROW, 2), _at(ROW, 2.5)])}).to_crs(4326).to_file(path)
    vcli.main(["routes", "--config", str(tmp_path / "study.yaml"), "--out", str(out),
               "--traces", str(path)])  # fmt: skip
    folder = out / route_report.FOLDER
    table = pd.read_parquet(folder / "trips.parquet")
    assert table.status.tolist() == [traces.TOO_SHORT]
    assert "nothing to score" in (folder / "report.md").read_text()
