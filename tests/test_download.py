"""The public-data readers, against made-up answers from each service."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import geopandas as gpd
import httpx
import pytest
from shapely.geometry import Point
from tenacity import wait_none

from tests import fake_services as fake
from walkbike_routes import config
from walkbike_routes.download import area, census, elevation, geofabrik, osm
from walkbike_routes.download.http import DownloadError, Fetcher


def _client(respond: fake.Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(respond))


def test_answers_are_cached_and_secrets_are_not_written(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"n": len(seen)})

    fetcher = Fetcher(tmp_path, _client(respond))
    first = fetcher.json("https://x.test/a", {"q": "1"}, secret_params={"key": "s3cret"}, name="a")
    again = fetcher.json("https://x.test/a", {"q": "1"}, secret_params={"key": "other"}, name="a")
    assert first == again == {"n": 1} and len(seen) == 1
    assert seen[0].url.params["key"] == "s3cret"
    (cached,) = tmp_path.glob("a-*.json")
    assert "s3cret" not in cached.read_text() and "s3cret" not in cached.name


def test_busy_servers_are_retried_but_refusals_are_not(tmp_path: Path) -> None:
    codes = iter([503, 429, 200])

    def flaky(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(codes), json={"ok": True})

    assert Fetcher(tmp_path, _client(flaky), wait_none()).json("https://x.test/f") == {"ok": True}
    calls: list[int] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403, text="no")

    with pytest.raises(DownloadError, match="403"):
        Fetcher(tmp_path, _client(refuse), wait_none()).json("https://x.test/r")
    assert len(calls) == 1


def test_error_reports_and_non_json_answers_are_not_cached(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, _client(lambda r: httpx.Response(200, json={"error": "busy"})))
    with pytest.raises(DownloadError, match="busy"):
        fetcher.json("https://x.test/e", check=lambda a: a.get("error"))
    assert not list(tmp_path.glob("*.json"))
    html = Fetcher(tmp_path, _client(lambda r: httpx.Response(200, text="<html>key</html>")))
    with pytest.raises(DownloadError, match="did not answer with JSON"):
        html.json("https://x.test/h")


@pytest.mark.parametrize(
    "respond",
    [lambda r: httpx.Response(403, text="no"),
     lambda r: httpx.Response(200, text=f"<html>bad request {r.url}</html>"),
     lambda r: httpx.Response(200, json={"error": f"bad key {r.url.params['key']}"})],
)  # fmt: skip
def test_secrets_stay_out_of_error_messages(tmp_path: Path, respond: fake.Handler) -> None:
    fetcher = Fetcher(tmp_path, _client(respond), wait_none())
    with pytest.raises(DownloadError) as caught:
        fetcher.json(
            "https://x.test/k", secret_params={"key": "s3cret"}, check=lambda a: a.get("error")
        )
    assert "s3cret" not in str(caught.value) and "***" in str(caught.value)
    assert caught.value.__cause__ is None


def test_keys_are_masked_in_httpx_request_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fetcher = Fetcher(tmp_path, _client(lambda r: httpx.Response(200, json={})))
    with caplog.at_level(logging.INFO, logger="httpx"):
        fetcher.json("https://x.test/c", {"get": "P1"}, secret_params={"key": "s3cret"})
    assert caplog.records and "s3cret" not in caplog.text and "key=***" in caplog.text


def test_the_command_line_keeps_request_urls_out_of_its_log(tmp_path: Path) -> None:
    from walkbike_routes import __main__ as cli

    with pytest.raises(SystemExit) as stopped:
        cli.main(["run", "--config", str(tmp_path / "missing.yaml")])
    assert stopped.value.code == 2
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


@pytest.mark.parametrize(
    ("place", "expected"),
    [("Boulder, Colorado", ("Boulder", "08")), ("St. Mary's, ga", ("St. Mary's", "13")),
     ("Washington, DC", ("Washington", "11")), ("A, B, Texas", ("A, B", "48"))],
)  # fmt: skip
def test_places_are_read_as_name_and_state(place: str, expected: tuple[str, str]) -> None:
    assert area.split_place(place) == expected


@pytest.mark.parametrize(
    ("place", "message"),
    [("Boulder", "should read 'Name, State'"), ("Boulder, Atlantis", "not a US state")],
)
def test_bad_places_are_refused(place: str, message: str) -> None:
    with pytest.raises(area.AreaError, match=message):
        area.split_place(place)


def test_a_place_is_found_and_projected_to_its_utm_zone(tmp_path: Path) -> None:
    calls: list[str] = []
    study_area = area.resolve(
        config.Area(source="place", place="Gridville, Nebraska", buffer_m=200),
        fake.fetcher(tmp_path, calls),
    )
    assert study_area.crs == "EPSG:26914"  # NAD83 UTM zone 14N covers -100 degrees
    assert study_area.extent.area.sum() > study_area.boundary.area.sum()
    west, south, east, north = study_area.bbox_4326()
    assert west < fake.WEST < east and south < fake.SOUTH < north
    assert calls == [
        "tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer/25/query"
    ]


def test_a_refused_place_query_is_not_cached(tmp_path: Path) -> None:
    refused = Fetcher(
        tmp_path, _client(lambda r: httpx.Response(200, json={"error": {"code": 500}}))
    )
    with pytest.raises(DownloadError, match="TIGERweb refused"):
        area.find_place("Gridville, Nebraska", refused)
    assert not list(tmp_path.glob("*.json"))
    frame, kind = area.find_place("Gridville, Nebraska", fake.fetcher(tmp_path, []))
    assert kind == "incorporated place" and len(frame) == 1


def test_a_cached_error_report_is_fetched_again(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, _client(lambda r: httpx.Response(200, json={"ok": 1})))
    fetcher.json("https://x.test/s", name="s")
    (saved,) = tmp_path.glob("s-*.json")
    saved.write_text(json.dumps({"error": "busy"}))
    assert fetcher.json("https://x.test/s", name="s", check=lambda a: a.get("error")) == {"ok": 1}
    assert json.loads(saved.read_text()) == {"ok": 1}


def test_place_lookups_that_find_several_or_none_are_refused(tmp_path: Path) -> None:
    two = fake.place_answer()
    two["features"] = two["features"] * 2
    several = Fetcher(tmp_path / "a", _client(lambda r: httpx.Response(200, json=two)))
    with pytest.raises(area.AreaError, match="matches several"):
        area.find_place("Gridville, Nebraska", several)
    empty = {"type": "FeatureCollection", "features": []}
    none = Fetcher(tmp_path / "b", _client(lambda r: httpx.Response(200, json=empty)))
    with pytest.raises(area.AreaError, match="no Census place named 'Gridville'"):
        area.find_place("Gridville, Nebraska", none)


def test_a_boundary_file_is_used_with_the_configured_projection(tmp_path: Path) -> None:
    path = tmp_path / "town.geojson"
    path.write_text(json.dumps(fake.place_answer()))
    study_area = area.resolve(
        config.Area(source="boundary_file", boundary_file=path), Fetcher(tmp_path), "EPSG:32614"
    )
    assert study_area.crs == "EPSG:32614" and study_area.source == "boundary file town.geojson"
    with pytest.raises(area.AreaError, match="does not exist"):
        area.resolve(
            config.Area(source="boundary_file", boundary_file=tmp_path / "x.geojson"),
            Fetcher(tmp_path),
        )
    with pytest.raises(area.AreaError, match="not a projected"):
        area.resolve(
            config.Area(source="boundary_file", boundary_file=path), Fetcher(tmp_path), "EPSG:4326"
        )
    points = tmp_path / "points.geojson"
    gpd.GeoDataFrame(geometry=[Point(0, 0)], crs=4326).to_file(points)
    with pytest.raises(area.AreaError, match="must hold polygons"):
        area.resolve(config.Area(source="boundary_file", boundary_file=points), Fetcher(tmp_path))


def test_the_smallest_covering_extract_is_downloaded_once_and_cut_to_the_area(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    bbox = (fake.WEST - 0.001, fake.SOUTH - 0.001, fake.WEST + 0.0045, fake.SOUTH + 0.0045)
    answer = geofabrik.area_elements(bbox, fake.fetcher(tmp_path, calls))
    assert answer["source"]["extract"] == "us/gridland"
    assert (tmp_path / "gridland-latest.osm.pbf").exists()
    ways = {e["id"] for e in answer["elements"] if e["type"] == "way"}
    # Rows 0-2 and columns 0-2 touch the box; whole ways are kept, not cut.
    assert ways == {1, 2, 3, 11, 12, 13}
    signal = next(e for e in answer["elements"] if e.get("id") == 122)
    assert signal["tags"] == {"highway": "traffic_signals"}
    stop = next(e for e in answer["elements"] if e.get("id") == 142)
    assert "tags" not in stop  # the stop sign is outside the box
    again: list[str] = []
    assert geofabrik.area_elements(bbox, fake.fetcher(tmp_path, again)) == answer
    assert again == []


def test_a_way_crossing_the_area_with_no_node_inside_is_kept(tmp_path: Path) -> None:
    answer = {"elements": [
        {"type": "node", "id": 1, "lon": -100.002, "lat": 40.001},
        {"type": "node", "id": 2, "lon": -99.998, "lat": 40.001},
        {"type": "node", "id": 3, "lon": -99.998, "lat": 40.003},
        {"type": "way", "id": 7, "nodes": [1, 2], "tags": {"highway": "secondary"}},
        {"type": "way", "id": 8, "nodes": [2, 3], "tags": {"highway": "secondary"}},
    ]}  # fmt: skip
    path = tmp_path / "x.osm.pbf"
    path.write_bytes(fake.pbf_bytes(answer))
    kept = geofabrik.extract(path, (-100.001, 40.0, -99.999, 40.002))
    assert [e["id"] for e in kept["elements"] if e["type"] == "way"] == [7]


def test_no_covering_extract_is_an_error() -> None:
    with pytest.raises(DownloadError, match="no Geofabrik extract covers"):
        geofabrik.choose_region((50.0, 50.0, 51.0, 51.0), fake.geofabrik_index())


def test_a_study_may_bring_its_own_osm_file(tmp_path: Path) -> None:
    bbox = (fake.WEST - 1, fake.SOUTH - 1, fake.WEST + 1, fake.SOUTH + 1)
    saved = tmp_path / "snapshot.json"
    saved.write_text(json.dumps(fake.overpass_answer()))
    assert osm.elements(bbox, Fetcher(tmp_path), saved) == fake.overpass_answer()
    pbf = tmp_path / "town.osm.pbf"
    pbf.write_bytes(fake.pbf_bytes(fake.overpass_answer()))
    from_pbf = osm.elements(bbox, Fetcher(tmp_path), pbf)
    assert sum(e["type"] == "way" for e in from_pbf["elements"]) == 10
    with pytest.raises(DownloadError, match="does not exist"):
        osm.elements(bbox, Fetcher(tmp_path), tmp_path / "missing.osm.pbf")
    (tmp_path / "list.json").write_text("[]")
    with pytest.raises(DownloadError, match="not an Overpass JSON answer"):
        osm.elements(bbox, Fetcher(tmp_path), tmp_path / "list.json")
    (tmp_path / "streets.csv").write_text("")
    with pytest.raises(DownloadError, match="expected .osm.pbf"):
        osm.elements(bbox, Fetcher(tmp_path), tmp_path / "streets.csv")


def test_large_files_stream_to_the_cache_and_failures_leave_nothing(tmp_path: Path) -> None:
    payload = b"x" * (3 << 20)
    good = Fetcher(tmp_path, _client(lambda r: httpx.Response(200, content=payload)))
    path = good.download("https://x.test/f.bin", "f.bin")
    assert path.read_bytes() == payload
    assert good.download("https://x.test/other", "f.bin") == path  # reused by name
    bad = Fetcher(tmp_path, _client(lambda r: httpx.Response(404)), wait_none())
    with pytest.raises(DownloadError, match="could not download"):
        bad.download("https://x.test/g.bin", "g.bin")
    assert not list(tmp_path.glob("g.bin*"))


def test_blocks_are_read_page_by_page(tmp_path: Path) -> None:
    calls: list[str] = []
    blocks = census.blocks((-100.0, 40.0, -99.99, 40.01), fake.fetcher(tmp_path, calls))
    assert len(blocks) == 16 and blocks.residents.sum() == 15 * 40
    assert set(blocks.columns) >= {"block_id", "residents", "housing_units", "STATE", "COUNTY"}
    assert blocks.block_id.str.len().eq(15).all()


def test_age_bands_are_split_evenly_when_a_range_cuts_them() -> None:
    weights = census.age_weights(5, 11)
    assert weights == {"P12_004N": 1.0, "P12_028N": 1.0, "P12_005N": 0.4, "P12_029N": 0.4}
    assert census.age_weights(85, 120) == {"P12_025N": 1.0, "P12_049N": 1.0}


def test_ages_and_households_need_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fetcher = fake.fetcher(tmp_path, calls)
    blocks = census.blocks((-100.0, 40.0, -99.99, 40.01), fetcher)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    with pytest.raises(census.CensusKeyMissing, match="key_signup"):
        census.add_counts(blocks, fetcher, min_age=5, max_age=11)
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    counted = census.add_counts(blocks, fetcher, min_age=5, max_age=11, households=True)
    # Every band holds 2 people of each sex; 5-9 counts whole and 10-14 at 2/5.
    assert counted.residents_5_11.tolist() == [pytest.approx(2 * 2 + 2 * 2 * 0.4)] * 16
    assert counted.households.eq(14).all()
    assert census.add_counts(blocks, fetcher) is blocks
    assert not any("made-up-key" in p.read_text() for p in tmp_path.glob("*.json"))


def test_elevations_come_in_batches_and_missing_values_stay_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(elevation, "BATCH", 3)
    calls: list[str] = []
    nodes = gpd.GeoDataFrame(
        {"node_id": [f"n{i}" for i in range(7)]},
        geometry=[Point(fake.WEST, fake.SOUTH + i * fake.STEP) for i in range(7)],
        crs=4326,
    )
    result = elevation.node_elevations(nodes, fake.fetcher(tmp_path, calls))
    assert len(calls) == 3
    # The first sample in each batch is NoData in the fake service.
    assert result.elevation_m.isna().tolist() == [True, False, False, True, False, False, True]
    assert result.elevation_m[1] == pytest.approx(1010.0)


def test_a_changed_network_asks_only_for_its_new_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(elevation, "BATCH", 3)
    calls: list[str] = []
    fetcher = fake.fetcher(tmp_path, calls)

    def nodes(points: list[Point]) -> gpd.GeoDataFrame:
        ids = [f"n{i}" for i in range(len(points))]
        return gpd.GeoDataFrame({"node_id": ids}, geometry=points, crs=4326)

    grid = [Point(fake.WEST, fake.SOUTH + i * fake.STEP) for i in range(7)]
    first = elevation.node_elevations(nodes(grid), fetcher)
    assert len(calls) == 3
    # One node added at the start shifts every batch of 3; only it is asked for.
    added = Point(fake.WEST + fake.STEP / 2, fake.SOUTH)
    second = elevation.node_elevations(nodes([added, *grid]), fetcher)
    assert len(calls) == 4
    assert second.elevation_m.iloc[1:].tolist() == pytest.approx(
        first.elevation_m.tolist(), nan_ok=True
    )
    # Nothing new: no request at all, and a repeated point is asked for once.
    elevation.node_elevations(nodes([*grid, grid[2]]), fetcher)
    assert len(calls) == 4
    # A damaged table is read as empty and rebuilt.
    (tmp_path / elevation.POINTS_FILE).write_text("{not json")
    for saved in tmp_path.glob("3dep-*.json"):
        if saved.name != elevation.POINTS_FILE:
            saved.unlink()
    elevation.node_elevations(nodes(grid), fetcher)
    assert len(calls) == 7


def test_points_that_round_to_signed_zeros_are_one_point(tmp_path: Path) -> None:
    calls: list[str] = []
    fetcher = fake.fetcher(tmp_path, calls)
    points = [Point(1e-8, 10), Point(-1e-8, 10), Point(10, 1e-8), Point(10, -1e-8)]
    nodes = gpd.GeoDataFrame({"node_id": list("abcd")}, geometry=points, crs=4326)
    first = elevation.node_elevations(nodes, fetcher)
    # Each pair rounds to the same place, so it shares one answer.
    assert first.elevation_m[0] == pytest.approx(first.elevation_m[1], nan_ok=True)
    assert first.elevation_m[2] == pytest.approx(first.elevation_m[3], nan_ok=True)
    asked = len(calls)
    again = elevation.node_elevations(nodes, fetcher)  # warm: all from the table
    assert len(calls) == asked
    assert again.elevation_m.tolist() == pytest.approx(first.elevation_m.tolist(), nan_ok=True)


def test_a_service_error_or_no_data_at_all_is_reported(tmp_path: Path) -> None:
    nodes = gpd.GeoDataFrame({"node_id": ["a"]}, geometry=[Point(0, 0)], crs=4326)
    refused = Fetcher(
        tmp_path / "a", _client(lambda r: httpx.Response(200, json={"error": {"code": 400}}))
    )
    with pytest.raises(DownloadError, match="3DEP refused"):
        elevation.node_elevations(nodes, refused)
    empty = {"samples": [{"locationId": 0, "value": "NoData"}]}
    nothing = Fetcher(tmp_path / "b", _client(lambda r: httpx.Response(200, json=empty)))
    with pytest.raises(DownloadError, match="no elevations"):
        elevation.node_elevations(nodes, nothing)


def test_a_short_page_that_says_there_are_more_is_followed(tmp_path: Path) -> None:
    features = fake.block_answer()["features"]
    asked: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["resultOffset"])
        asked.append(offset)
        page = features[offset : offset + 5]  # the server's own limit, below the 2,000 asked for
        more = offset + 5 < len(features)
        return httpx.Response(200, json={"features": page, "exceededTransferLimit": more})

    fetcher = Fetcher(tmp_path, _client(respond), backoff=wait_none())
    blocks = census.blocks((-100.0, 40.0, -99.99, 40.01), fetcher)
    assert len(blocks) == 16 and asked == [0, 5, 10, 15]


def test_saved_counts_are_used_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    fetcher = fake.fetcher(tmp_path, calls)
    blocks = census.blocks((-100.0, 40.0, -99.99, 40.01), fetcher)
    monkeypatch.setenv("CENSUS_API_KEY", "made-up-key")
    first = census.add_counts(blocks, fetcher, min_age=5, max_age=11)
    monkeypatch.delenv("CENSUS_API_KEY")
    calls.clear()
    again = census.add_counts(blocks, fetcher, min_age=5, max_age=11)
    assert calls == [] and again.residents_5_11.tolist() == first.residents_5_11.tolist()
    with pytest.raises(census.CensusKeyMissing):  # a count not yet saved still needs the key
        census.add_counts(blocks, fetcher, households=True)


def test_a_damaged_saved_answer_is_asked_for_again(tmp_path: Path) -> None:
    fetcher = Fetcher(tmp_path, _client(lambda request: httpx.Response(200, json={"n": 1})))
    assert fetcher.cached("https://example.org/a", name="a") is None
    assert fetcher.json("https://example.org/a", name="a") == {"n": 1}
    (saved,) = tmp_path.glob("a-*.json")
    saved.write_text('{"n": ')
    assert fetcher.cached("https://example.org/a", name="a") is None and not saved.exists()
    assert fetcher.json("https://example.org/a", name="a") == {"n": 1}


def test_a_damaged_jobs_file_is_removed_so_the_next_run_fetches_it(tmp_path: Path) -> None:
    from walkbike_routes.download import lodes

    fetcher = Fetcher(tmp_path, _client(lambda request: httpx.Response(200, content=b"not gzip")))
    with pytest.raises(DownloadError, match="not a LODES workplace file"):
        lodes.workplace_jobs(["31"], fetcher)
    assert not list(tmp_path.glob("lodes-*"))
