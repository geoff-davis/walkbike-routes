"""The run map (walkbike_routes.report.map), on the made-up demo town."""

from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from pyproj import Transformer
from shapely.geometry import LineString, Point

from walkbike_routes import __main__ as cli
from walkbike_routes import config, demo
from walkbike_routes.config import LocalLayer
from walkbike_routes.report import map as report
from walkbike_routes.report import text as words

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"


@pytest.fixture(scope="module")
def out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The demo run, with walking under two presets, to the school only."""
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = [d for d in raw["destinations"] if d["id"] == "school"]
    raw["presets"]["walk"] = ["broach-2016", "shortest"]
    folder = tmp_path_factory.mktemp("report")
    path = folder / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    target = folder / "out"
    cli.build(config.load(path), target)
    return target


def _data(page: str) -> dict[str, Any]:
    found = re.search(r"const D=(\{.*?\});const CREDITS=", page, re.S)
    assert found
    data: dict[str, Any] = json.loads(found.group(1))
    return data


def test_a_map_is_written_for_each_destination(out: Path) -> None:
    paths = report.build_maps(out)
    assert paths == [out / "school" / "map.html"]
    page = paths[0].read_text()
    assert "/*" not in page.split("<script>", 1)[1].split("const D=", 1)[0]
    assert not re.search(r"/\*[A-Z]+\*/", page)


def test_every_run_is_offered_with_the_main_preset_first(out: Path) -> None:
    data = _data(report.build_maps(out)[0].read_text())
    runs = [(r["id"], r["main"]) for r in data["runs"]]
    assert runs == [
        ("walk-broach-2016", True),
        ("walk-shortest", False),
        ("bike-broach-2011", True),
        ("car-central", True),
    ]
    assert data["runs"][0]["title"] and data["runs"][0]["source"]


def test_flows_round_trip(out: Path) -> None:
    data = _data(report.build_maps(out)[0].read_text())
    flows = gpd.read_parquet(out / "school" / "bike-broach-2011" / "flows.parquet")
    expected = {
        str(e): round(float(f), 2)
        for e, f in zip(flows.physical_edge_id, flows.potential_flow, strict=True)
        if f > 0
    }
    links = gpd.read_parquet(out / "links.parquet").set_index("link_id")
    bike = next(r for r in data["runs"] if r["id"] == "bike-broach-2011")
    names = dict(zip(links.index.astype(str), links.name, strict=True))
    found: dict[str, float] = {}
    for index, flow in bike["flow"]:
        name = data["edges"]["n"][index]
        found[str(index)] = flow
        assert name in names.values()
    assert sorted(found.values()) == sorted(expected.values())


def test_the_squares_follow_the_runs_routes(out: Path) -> None:
    data = _data(report.build_maps(out)[0].read_text())
    walk = data["runs"][0]
    routes = pd.read_parquet(out / "school" / "walk-broach-2016" / "routes.parquet")
    assert sum(w for _, _, w in walk["cells"]) == pytest.approx(
        routes.demand_weight.sum(), abs=0.1 * len(walk["cells"])
    )
    assert all(len(square) == 4 for square in data["cells"])


def test_the_made_up_town_is_labelled_and_credited(out: Path) -> None:
    page = report.build_maps(out)[0].read_text()
    assert words.SYNTHETIC in page and html.escape(words.DRAFT) in page
    assert "Made-up demo town" in page
    assert words.NOT_COUNTS in page


def test_a_run_from_before_sources_were_listed_still_credits_osm(out: Path) -> None:
    manifest = json.loads((out / "manifest.json").read_text())
    del manifest["sources"]
    manifest["synthetic"] = False
    data = _data(report.build_maps(out)[0].read_text())
    page = report.page(data, manifest)
    assert "OpenStreetMap contributors" in page and words.SYNTHETIC not in page
    assert html.escape(words.DRAFT) in page  # every map says it is a draft


def test_the_online_page_links_leaflet_and_offers_tiles(out: Path) -> None:
    page = report.build_maps(out)[0].read_text()
    assert report.LEAFLET_JS in page and report.TILES in page


def test_the_offline_page_loads_nothing_from_the_internet(out: Path) -> None:
    page = report.build_maps(out, offline=True)[0].read_text()
    assert report.LEAFLET_CSS not in page and report.LEAFLET_JS not in page
    assert "tile.openstreetmap.org" not in page and report.NO_TILES in page
    # Links a reader may click (Leaflet's own credit) are fine; nothing is loaded.
    assert not re.search(r"""src\s*=\s*["']?https?:""", page)
    assert not re.search(r"""<link[^>]*https?:""", page)
    assert "@import" not in page and "url(http" not in page
    assert ".leaflet-container" in page


def test_text_from_the_data_cannot_leave_the_script_block(out: Path) -> None:
    data = _data(report.build_maps(out)[0].read_text())
    data["edges"]["n"][0] = "</script><script>example()</script><!-- /*DATA*/  "
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["sources"] = ["</div><b>made-up source</b>"]
    page = report.page(data, manifest)
    script = page.split("const D=", 1)[1]
    assert "</script><script>example()" not in page
    assert "<!--" not in script.split("</script>", 1)[0]
    assert " " not in page
    assert "/*DATA*/" in script.split("</script>", 1)[0]  # kept as text, not filled
    assert "&lt;/div&gt;&lt;b&gt;made-up source" in page


def test_busy_streets_are_split_by_agreement() -> None:
    def flows(values: dict[str, float]) -> pd.DataFrame:
        return pd.DataFrame(
            {"physical_edge_id": list(values), "potential_flow": list(values.values())}
        )

    ten = {f"e{i}": float(i) for i in range(1, 11)}  # the top tenth is e10
    other = {**ten, "e10": 1.0, "e9": 20.0}
    found = report.busy_agreement(
        {
            "walk-a": ("walk", flows(ten)),
            "walk-b": ("walk", flows(other)),
            "bike-a": ("bike", flows(ten)),
        }  # fmt: skip
    )
    assert set(found) == {"walk"}  # one bike preset: nothing to compare
    assert found["walk"] == {"every": set(), "some": {"e9", "e10"}}
    same = report.busy_agreement({"a": ("walk", flows(ten)), "b": ("walk", flows(ten))})
    assert same["walk"] == {"every": {"e10"}, "some": set()}


def test_a_squares_time_is_its_weighted_median_home() -> None:
    routes = pd.DataFrame(
        {
            "origin_id": ["a", "b", "c", "d", "e"],
            "total_travel_time_s": [60.0, 600.0, 1200.0, 300.0, 300.0],
            "route_reachable": [True, True, True, False, True],
            "demand_weight": [1.0, 1.0, 3.0, 5.0, 0.0],
        }
    )
    cells = pd.Series({"a": 0, "b": 0, "c": 0, "d": 1, "e": 2})
    rows = report.catchment(routes, cells)
    # Square 0: weights 1, 1, 3 at 1, 10, 20 minutes: the median home takes 20.
    # Square 1: its only home cannot get there.  Square 2 has no weight.
    assert rows == [[0, 20.0, 5.0], [1, None, 5.0]]


def test_a_car_square_counts_the_trip_to_the_destination() -> None:
    routes = pd.DataFrame(
        {
            "origin_id": ["a", "a"],
            "direction": ["to_destination", "from_destination"],
            "total_travel_time_s": [120.0, 900.0],
            "route_reachable": [True, True],
            "demand_weight": [2.0, 2.0],
        }
    )
    assert report.catchment(routes, pd.Series({"a": 0})) == [[0, 2.0, 2.0]]


def test_the_run_writes_maps_unless_told_not_to(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = raw["destinations"][:1]
    raw["modes"], raw["presets"] = ["walk"], {"walk": ["shortest"]}
    raw["time_caps_min"] = {"walk": 20}
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    cli.main(["run", "--config", str(path), "--out", str(tmp_path / "a"), "--no-map"])
    assert not list((tmp_path / "a").rglob("map.html"))
    cli.main(["report", "--out", str(tmp_path / "a"), "--offline"])
    assert (tmp_path / "a" / "school" / "map.html").exists()
    # A private outline left by an earlier run into the folder is removed.
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "outlines.parquet").write_text("private")
    cli.main(["run", "--config", str(path), "--out", str(tmp_path / "b")])
    assert (tmp_path / "b" / "school" / "map.html").exists()
    assert not (tmp_path / "b" / "outlines.parquet").exists()


def test_report_refuses_a_folder_that_is_not_a_run(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stop:
        cli.main(["report", "--out", str(tmp_path)])
    assert stop.value.code == 2


def test_sources_name_the_data_a_real_run_used() -> None:
    town = demo.build()
    study = config.load(DEMO)
    assert cli.sources(study, town) == [
        "Made-up demo town (walkbike_routes.demo): no real streets, homes or people"
    ]
    real = replace(
        study,
        area=replace(study.area, source="place"),
        synthetic=False,
        local_layers=(LocalLayer(kind="signals", file=Path("city/signals.geojson")),),
    )
    homes = town.homes.copy()
    homes["building_source"] = ["Overture Maps 2026-10-21.0"] + [""] * (len(homes) - 1)
    nodes = town.nodes.copy()
    nodes["elevation_m"] = 30.0
    links = town.links.copy()
    links["traffic_aadt_source"] = None
    links.loc[links.index[0], "traffic_aadt_source"] = "FHWA HPMS CO 2023"
    found = cli.sources(real, replace(town, homes=homes, nodes=nodes, links=links))
    assert found[0].startswith("Streets: © OpenStreetMap contributors")
    assert (
        "Home locations: Overture Maps 2026-10-21.0 buildings, © OpenStreetMap contributors, "
        "Overture Maps Foundation (ODbL)"
    ) in found
    assert "Elevation: USGS 3D Elevation Program" in found
    assert "Traffic counts: FHWA HPMS CO 2023 (each state DOT, via FHWA)" in found
    assert found[-1] == "Local signals: signals.geojson"
    plain = cli.sources(real, replace(town, nodes=town.nodes.assign(elevation_m=float("nan"))))
    assert not any(s.startswith(("Home locations", "Elevation", "Traffic")) for s in plain)


def test_sources_credit_each_outline() -> None:
    study = config.load(DEMO)
    school = replace(
        study.destinations[0],
        outline=config.Outline(file=Path("lasd/bounds.geojson"), credit="A school district"),
        serves=Path("areas/west.geojson"),
    )
    plain = replace(study.destinations[1], outline=config.Outline(file=Path("x/core.geojson")))
    real = replace(
        study,
        area=replace(study.area, source="place"),
        synthetic=False,
        destinations=(school, plain),
    )
    found = cli.sources(real, demo.build())
    assert f"Attendance area, {school.name}: A school district" in found
    assert f"Homes counted, {school.name}: west.geojson" in found
    assert f"Attendance area, {plain.name}: core.geojson" in found


def test_outlines_are_drawn_only_on_their_destinations_map(out: Path, tmp_path: Path) -> None:
    copy = tmp_path / "out"
    shutil.copytree(out, copy)
    destination = gpd.read_parquet(copy / "destinations.parquet").geometry.iloc[0]
    enclave = destination.buffer(400).difference(destination.buffer(350).buffer(-300))
    rows = [
        ("school", "Attendance area", False, enclave),
        ("school", "Homes counted", True, destination.buffer(300)),
        ("elsewhere", "Attendance area", False, destination.buffer(200)),
    ]
    gpd.GeoDataFrame(
        rows, columns=["destination_id", "name", "served", "geometry"], crs=demo.CRS
    ).to_parquet(copy / "outlines.parquet")
    manifest = json.loads((copy / "manifest.json").read_text())
    manifest["files"]["outlines.parquet"] = "x"
    (copy / "manifest.json").write_text(json.dumps(manifest))
    page = report.build_maps(copy)[0].read_text()
    drawn = _data(page)["outlines"]
    assert [(o["name"], o["served"]) for o in drawn] == [
        ("Attendance area", False),
        ("Homes counted", True),
    ]
    assert [len(o["rings"]) for o in drawn] == [2, 1]  # the enclave's edge is drawn too
    # Not listed in the manifest: an outline from an earlier run is not drawn.
    del manifest["files"]["outlines.parquet"]
    (copy / "manifest.json").write_text(json.dumps(manifest))
    assert _data(report.build_maps(copy)[0].read_text())["outlines"] == []


def test_the_manifest_lists_its_sources(out: Path) -> None:
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["sources"] == [
        "Made-up demo town (walkbike_routes.demo): no real streets, homes or people"
    ]


def test_basemap_classes_and_labels() -> None:
    links = gpd.GeoDataFrame(
        {"name": ["Main Street", "Main Street", None], "highway": ["secondary", "secondary", "footway"]},
        geometry=[LineString([(0, 0), (0, 100)]), LineString([(0, 100), (0, 150)]),
                  LineString([(5, 0), (5, 100)])],
        crs=demo.CRS,
    )  # fmt: skip
    to_degrees = Transformer.from_crs(links.crs, report.WGS84, always_xy=True)
    lines, labels = report.basemap(links, to_degrees)
    assert len(lines["M"]) == 2 and len(lines["P"]) == 1
    assert [label[0] for label in labels] == ["Main Street"]  # one per 300 m square


def test_the_page_opens_on_the_homes_within_reach_and_the_main_streets() -> None:
    near, far = LineString([(0, 0), (500, 0)]), LineString([(500, 0), (9_000, 0)])
    flow = gpd.GeoDataFrame(
        {"physical_edge_id": ["near", "far"], "potential_flow": [100.0, 2.0]},
        geometry=[near, far], crs=demo.CRS,
    )  # fmt: skip
    homes = gpd.GeoDataFrame(
        {"home_id": ["a", "b", "c"]},
        geometry=[Point(100, -300), Point(400, 200), Point(8_000, 50)],
        crs=demo.CRS,
    )
    routes = pd.DataFrame({"origin_id": ["a", "b", "c"], "within_time_limit": [True, True, False]})
    view = report.opening_view(flow, routes, homes, Point(250, 0), flow.geometry)
    # The faint flow and the home beyond the limit are left out.
    assert view == (0.0, -300.0, 500.0, 200.0)
    # A corridor due north is framed, not mistaken for a single point.
    north = flow.set_geometry([LineString([(0, 0), (0, 500)]), LineString([(0, 500), (0, 9_000)])])
    alone = homes.iloc[:1].set_geometry([Point(0, 400)])
    assert report.opening_view(north, routes, alone, Point(0, 0), north.geometry) == (
        0.0, 0.0, 0.0, 500.0,
    )  # fmt: skip
    # A car run's home counts only by its trip there.
    both = pd.DataFrame({
        "origin_id": ["a", "c", "c"], "within_time_limit": [True, False, True],
        "direction": ["to_destination", "to_destination", "from_destination"],
    })  # fmt: skip
    assert report.opening_view(flow, both, homes, Point(250, 0), flow.geometry)[2] == 500.0
    # Nothing to frame but the destination: every street drawn.
    assert report.opening_view(None, None, homes, Point(250, 0), flow.geometry) == (
        0.0, 0.0, 9_000.0, 0.0,
    )  # fmt: skip


def test_a_point_destination_and_an_area_are_both_drawn() -> None:
    to_degrees = Transformer.from_crs(demo.CRS, report.WGS84, always_xy=True)
    assert "point" in report._outline(Point(0, 0), to_degrees)
    square = Point(0, 0).buffer(50, cap_style="square")
    assert len(report._outline(square, to_degrees)["rings"]) == 1


def test_a_destination_named_like_its_study_is_not_repeated() -> None:
    assert report._title("Downtown", "Downtown") == "Downtown"
    assert report._title("Downtown", "Los Altos") == "Downtown, Los Altos"


@pytest.mark.parametrize("name", ["001", "NA"])
def test_a_destination_id_that_looks_like_a_number_or_a_gap_is_kept(
    out: Path, tmp_path: Path, name: str
) -> None:
    import shutil

    copy = tmp_path / "run"
    shutil.copytree(out, copy)
    (copy / "school").rename(copy / name)
    summary = (copy / "summary.csv").read_text().replace("\nschool,", f"\n{name},")
    (copy / "summary.csv").write_text(summary)
    destinations = gpd.read_parquet(copy / "destinations.parquet")
    destinations["destination_id"] = destinations.destination_id.replace({"school": name})
    destinations.to_parquet(copy / "destinations.parquet")
    assert report.build_maps(copy) == [copy / name / "map.html"]


def test_files_left_from_an_earlier_run_are_not_shown(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = raw["destinations"][:1]
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    cli.build(config.load(path), tmp_path / "out")
    raw["modes"], raw["presets"] = ["walk"], {"walk": ["shortest"]}
    raw["time_caps_min"] = {"walk": 20}
    path.write_text(yaml.safe_dump(raw))
    cli.build(config.load(path), tmp_path / "out")
    assert (tmp_path / "out" / "school" / "overlap.parquet").exists()  # left behind
    data = _data(report.build_maps(tmp_path / "out")[0].read_text())
    assert data["overlap"] == {}
    assert [r["id"] for r in data["runs"]] == ["walk-shortest"]


def test_the_page_has_a_mode_menu_a_title_and_plain_labels(out: Path) -> None:
    page = report.build_maps(out)[0].read_text()
    assert '<select id="mode">' in page and '<select id="run">' in page
    assert 'id="btitle"' in page and "Not shaded: areas over" in page
    assert "<title>Demo Elementary, Demo town · route access map</title>" in page
    # The key does not name a data source the run may not have used.
    assert "2020 Census" not in page.split("<script>", 1)[1].split("const CREDITS=", 1)[1]
    assert "Barriers: where a change would open new routes" in page
    assert "Where routes meet traffic today" in page
    # Figures read the same whatever the reader's language.
    assert "toLocaleString(undefined" not in page and 'toLocaleString("en-US"' in page


def test_the_page_lists_modes_as_walking_cycling_driving(out: Path) -> None:
    page = report.build_maps(out)[0].read_text()
    data = json.loads(re.search(r"const D=(\{.*?\});const CREDITS=", page, re.S).group(1))  # type: ignore[union-attr]
    modes = list(dict.fromkeys(r["mode"] for r in data["runs"]))
    assert modes == [m for m in ("walk", "bike", "car") if m in modes]
    summary = pd.DataFrame(
        {
            "destination_id": ["b", "b", "a", "b", "a"],
            "mode": ["car", "bike", "car", "walk", "walk"],
            "preset": ["central", "x", "central", "y", "y"],
        }
    )
    ordered = report.in_order(summary)
    assert list(zip(ordered.destination_id, ordered["mode"], strict=True)) == [
        ("b", "walk"), ("b", "bike"), ("b", "car"), ("a", "walk"), ("a", "car"),
    ]  # fmt: skip


def test_every_fix_first_place_is_on_the_map_with_the_reports_figures(out: Path) -> None:
    page = report.build_maps(out)[0].read_text()
    data = json.loads(re.search(r"const D=(\{.*?\});const CREDITS=", page, re.S).group(1))  # type: ignore[union-attr]
    for run in data["runs"]:
        if not run["exposure"]:
            continue
        folder = out / data["id"] / run["id"]
        for kind in ("street", "crossing"):
            listed = gpd.read_parquet(folder / f"exposure_{kind}s.parquet")
            first = listed[listed.tier.eq("fix first")]
            drawn = [e for e in run["exposure"] if e["kind"] == kind]
            assert run["fix_first"][kind] == len(first)
            assert {e["tag"] for e in drawn if e["tier"] == "fix first"} == {
                f"{kind[0].upper()}{rank}" for rank in first["rank"]
            }
            # Whole people, as text: the page prints what the report prints.
            assert all(isinstance(e["people"], str) and "." not in e["people"] for e in drawn)
        assert run["flow_heading"] and run["unit"]


def _slug(heading: str) -> str:
    """GitHub's anchor for a Markdown heading."""
    kept = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return kept.replace(" ", "-")


def test_every_shipped_route_model_links_to_a_section_that_exists() -> None:
    from walkbike_routes.costs import presets

    methods = Path(__file__).resolve().parents[1] / "docs" / "routing-methods.md"
    anchors = {_slug(m[1]) for m in re.finditer(r"^#+ (.+)$", methods.read_text(), re.M)}
    for mode in ("walk", "bike", "car"):
        for name in presets.names(mode):
            link = words.model_doc(mode, name)
            assert link.startswith(words.METHODS_DOC + "#"), (mode, name)
            assert link.split("#", 1)[1] in anchors, (mode, name, link)


def test_the_report_links_the_repository_and_each_route_models_section(out: Path) -> None:
    from walkbike_routes.report.tables import build_report

    build_report(out)
    page = (out / "report.html").read_text()
    md = (out / "report.md").read_text()
    for link in (words.REPO, words.METHODS_DOC, words.model_doc("walk", "broach-2016")):
        assert f'href="{html.escape(link, quote=True)}"' in page, link
        assert f"]({link})" in md, link


def test_the_map_links_the_route_model_and_the_repository(out: Path) -> None:
    data = _data((out / "school" / "map.html").read_text())
    assert data["repo"] == words.REPO
    assert {r["doc"] for r in data["runs"] if r["mode"] == "bike"} == {
        words.model_doc("bike", "broach-2011")
    }
    page = (out / "school" / "map.html").read_text()
    assert "how it works" in page and "About walkbike-routes" in page
