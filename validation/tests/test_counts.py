"""Comparing network flows with counts (wbvalidate), on made-up counts."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import yaml
from scipy import stats
from shapely.geometry import LineString, Point, box
from wbvalidate import cli as vcli
from wbvalidate import compare, counts, dvrpc, match
from wbvalidate import report as vreport

from walkbike_routes import __main__ as cli
from walkbike_routes import config, demo
from walkbike_routes.report.tables import Scatter, markdown, scatter_svg

Y_MAIN = demo.ORIGIN[1] + demo.ARTERIAL * demo.SPACING_M  # Main Street runs along this y
X_CENTRAL = demo.ORIGIN[0] + demo.ARTERIAL * demo.SPACING_M  # Central Avenue along this x


def _site(site_id: str, mode: str, x: float, y: float, **extra: Any) -> dict[str, Any]:
    row = {"site_id": site_id, "mode": mode, "volume": 100.0, "year": 2020, "road": "",
           "direction": "both", "side": "", "facility": "", "source": "Made-up counter",
           "geometry": Point(x, y)}  # fmt: skip
    row.update(extra)
    return row


def _counts(rows: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    return counts.check(gpd.GeoDataFrame(rows, geometry="geometry", crs=demo.CRS).to_crs(4326))


def test_street_names_compare_after_tidying() -> None:
    assert match.street_key("North 6th Street") == match.street_key("6th st") == "6th st"
    assert match.street_key("S. Broad St.") == "broad st"
    assert match.street_key("Schuylkill River Trail") == "schuylkill river trl"
    assert match.street_key("Main Street") != match.street_key("Main Avenue")
    assert match.street_key("North") == "north"  # a name that is only a direction keeps it
    assert match.street_key("South Street") == "south st" != match.street_key("North Street")
    assert match.street_key("Elm St Northbound Lanes") == "elm st"


def test_same_street_allows_initials_and_bridges() -> None:
    key = match.street_key
    assert match.same_street(key("J F Kennedy Blvd"), key("John F. Kennedy Boulevard"))
    assert match.same_street(key("Elm St Bridge"), key("Elm Street"))
    assert not match.same_street(key("J F Kennedy Blvd"), key("John F. Kennedy Street"))
    assert not match.same_street(key("Elm St"), key("Oak St"))
    assert not match.same_street("", "")


def test_sites_match_by_name_and_say_why_when_not() -> None:
    town = demo.build()
    x_mid = demo.ORIGIN[0] + 2.5 * demo.SPACING_M
    sites = _counts(
        [
            _site("main", "bike", x_mid, Y_MAIN + 3, road="Main St"),
            _site("wrong-name", "bike", x_mid, Y_MAIN + 3, road="Oak St"),
            _site("one-way-count", "bike", x_mid, Y_MAIN - 3, road="main street",
                  direction="east"),
            _site("trail", "walk", demo.ORIGIN[0] + 2, demo.ORIGIN[1] + 1.5 * demo.SPACING_M,
                  facility="Multiuse Trail"),
            _site("far-away", "walk", X_CENTRAL + 3, Y_MAIN + 1.5 * demo.SPACING_M,
                  road="Central Ave"),
        ]
    )  # fmt: skip
    within = box(demo.ORIGIN[0] - 50, demo.ORIGIN[1] - 50, X_CENTRAL - 50, Y_MAIN + 50)
    edge = town.links.link_id.astype(str)
    flows = {
        "bike-broach-2011": pd.Series(np.arange(len(edge), dtype=float), index=edge),
        "walk-broach-2016": pd.Series(1.0, index=edge),
    }
    found = match.match(sites, town.links, flows, within=within).set_index("site_id")
    assert found.loc["main", "status"] == match.MATCHED
    assert found.loc["main", "matched_by"] == "name"
    assert found.loc["main", "highway"] == "primary"
    names = town.links.set_index(town.links.link_id.astype(str)).name
    row = found.loc["main"].to_dict()
    ids = [str(i) for i in row["link_ids"]]
    assert set(names.loc[ids]) == {"Main Street"}
    # The flow is the highest among the matched links, and walking runs give none.
    assert found.loc["main", "flow:bike-broach-2011"] == flows["bike-broach-2011"].loc[ids].max()
    assert math.isnan(row["flow:walk-broach-2016"])
    assert found.loc["wrong-name", "status"] == match.NO_STREET
    assert math.isnan(found.loc["wrong-name"].to_dict()["flow:bike-broach-2011"])
    assert found.loc["one-way-count", "status"] == match.ONE_DIRECTION
    assert found.loc["trail", "status"] == match.MATCHED
    assert found.loc["trail", "matched_by"] == "nearest path"
    assert found.loc["trail", "flow:walk-broach-2016"] == 1.0
    assert found.loc["far-away", "status"] == match.OUTSIDE


def test_a_one_way_street_takes_a_one_direction_count() -> None:
    town = demo.build()
    links = town.links.assign(oneway="yes", oneway_bicycle=None)
    x_mid = demo.ORIGIN[0] + 2.5 * demo.SPACING_M
    sites = _counts([_site("s", "bike", x_mid, Y_MAIN + 3, road="Main St", direction="east")])
    assert match.match(sites, links, {}).status.iloc[0] == match.MATCHED
    contraflow = links.assign(oneway_bicycle="no")
    assert match.match(sites, contraflow, {}).status.iloc[0] == match.ONE_DIRECTION


UTM = "EPSG:32618"


def _street(link_id: str, name: str, highway: str, y: float, **extra: Any) -> dict[str, Any]:
    row = {"link_id": link_id, "u": 0, "v": 1, "name": name, "highway": highway,
           "bike_facility": "none", "oneway": "no", "oneway_bicycle": None, "footway": "",
           "geometry": LineString([(0, y), (200, y)])}  # fmt: skip
    row.update(extra)
    return row


def _links(rows: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=UTM)


def _flow_at(links: gpd.GeoDataFrame, flows: dict[str, float], **site: Any) -> pd.Series:
    """Match one made-up site at (100, y) and return its row."""
    y = site.pop("y")
    row = {"site_id": "s", "mode": "bike", "volume": 10.0, "year": 2020, "road": "",
           "direction": "both", "side": "", "facility": "", "source": "made up",
           "geometry": Point(100, y), **site}  # fmt: skip
    sites = counts.check(gpd.GeoDataFrame([row], geometry="geometry", crs=UTM))
    run = f"{row['mode']}-broach"
    found = match.match(sites, links, {run: pd.Series(flows)})
    return found.iloc[0].rename({f"flow:{run}": "flow"})


def test_a_path_count_takes_the_nearest_path_only() -> None:
    links = _links([_street("near", "", "path", 1), _street("far", "", "cycleway", 20)])
    found = _flow_at(links, {"near": 10.0, "far": 1000.0}, y=0, facility="Multiuse Trail")
    assert found.matched_by == "nearest path" and found.flow == 10.0


def test_a_path_count_skips_paths_the_mode_may_not_use() -> None:
    links = _links(
        [
            _street("foot", "", "footway", 1, bike_allowed=False, ped_allowed=True),
            _street("cycle", "", "cycleway", 5, bike_allowed=True, ped_allowed=None),
        ]
    )
    flows = {"foot": 0.0, "cycle": 40.0}
    assert _flow_at(links, flows, y=0, facility="Multiuse Trail").flow == 40.0
    walk = _flow_at(links, flows, y=0, mode="walk", facility="Multiuse Trail")
    assert walk.link_ids == ("cycle", "foot") and walk.section == (("foot",),)


def test_a_divided_road_adds_both_carriageways_unless_one_direction_was_counted() -> None:
    links = _links(
        [
            _street("east", "Main Street", "primary", 8, oneway="yes"),
            _street("west", "Main Street", "primary", -8, oneway="-1"),
        ]
    )
    flows = {"east": 10.0, "west": 1000.0}
    both = _flow_at(links, flows, y=0, road="Main St")
    assert both.status == match.MATCHED and both.flow == 1010.0
    one = _flow_at(links, flows, y=6, road="Main St", direction="east")
    assert one.status == match.MATCHED and one.flow == 10.0


def test_a_counter_just_past_a_link_end_keeps_its_own_carriageway() -> None:
    links = _links(
        [
            _street("east", "Main Street", "primary", 0, oneway="yes",
                    geometry=LineString([(0, 0), (100, 0)])),
            _street("west", "Main Street", "primary", 8, oneway="yes",
                    geometry=LineString([(200, 8), (0, 8)])),
        ]
    )  # fmt: skip
    found = _flow_at(links, {"east": 10.0, "west": 1000.0}, y=1, road="Main St",
                     direction="east", geometry=Point(101, 1))  # fmt: skip
    assert found.status == match.MATCHED and found.flow == 10.0


def test_one_direction_on_a_one_way_cycleway_is_kept_and_walking_never_is() -> None:
    links = _links([_street("c", "Elm Trail", "cycleway", 0, oneway="yes")])
    bike = _flow_at(links, {"c": 20.0}, y=1, road="Elm Trail", direction="east")
    assert bike.status == match.MATCHED and bike.flow == 20.0
    walk = _flow_at(links, {"c": 20.0}, y=1, mode="walk", road="Elm Trail", direction="east")
    assert walk.status == match.ONE_DIRECTION


def test_links_beyond_the_next_junction_are_not_added() -> None:
    links = _links(
        [
            _street("here", "Main Street", "secondary", 0, geometry=LineString([(0, 0), (110, 0)])),
            _street("next", "Main Street", "secondary", 0,
                    geometry=LineString([(110, 0), (300, 0)])),
        ]
    )  # fmt: skip
    found = _flow_at(links, {"here": 10.0, "next": 1000.0}, y=2, road="Main St")
    assert found.flow == 10.0
    at_node = _flow_at(links, {"here": 10.0, "next": 30.0}, y=2, road="Main St",
                       geometry=Point(110, 2))  # fmt: skip
    assert at_node.flow == 30.0  # one carriageway at a node: the higher, not the sum


def test_a_named_crossing_is_not_the_street() -> None:
    crossing = _street("x", "Main Street", "footway", 0, footway="crossing",
                       geometry=LineString([(100, -10), (100, 10)]))  # fmt: skip
    links = _links([_street("road", "Main Street", "primary", 0), crossing])
    found = _flow_at(links, {"road": 10.0, "x": 1000.0}, y=2, road="Main St")
    assert found.flow == 10.0 and found.link_ids == ("road",)
    blank = _links([_street("road", "Main Street", "primary", 0), crossing]).assign(crossed_road="")
    assert _flow_at(blank, {"road": 10.0, "x": 1000.0}, y=2, road="Main St").flow == 10.0


def test_a_walking_count_of_one_side_takes_that_sidewalk() -> None:
    links = _links(
        [
            _street("road", "Main Street", "secondary", 0),
            _street("north", "Main Street", "footway", 9, footway="sidewalk"),
            _street("south", "Main Street", "footway", -9, footway="sidewalk"),
        ]
    )
    flows = {"road": 1.0, "north": 100.0, "south": 300.0}
    side = _flow_at(links, flows, y=7, mode="walk", road="Main St", side="north")
    assert side.flow == 100.0
    whole = _flow_at(links, flows, y=0, mode="walk", road="Main St")
    assert whole.flow == 401.0
    bike = _flow_at(links, flows, y=7, road="Main St")
    assert bike.flow == 1.0  # cyclists' flow is the roadway's, not the sidewalks'


def test_no_counts_give_an_empty_match_and_report() -> None:
    town = demo.build()
    empty = _counts([_site("a", "bike", X_CENTRAL, Y_MAIN)]).iloc[:0]
    found = match.match(empty, town.links, {"bike-broach-2011": pd.Series(dtype=float)})
    assert len(found) == 0 and "status" in found and "flow:bike-broach-2011" in found
    blocks = vreport.report_blocks(found, {}, "Counts: made up", ["bike-broach-2011"])
    assert "Sites" in markdown(blocks)


def test_spearman_handles_ties_like_scipy_and_constants_as_undefined() -> None:
    x = np.array([10.0, 20.0, 20.0, 40.0, 5.0, 5.0])
    y = np.array([1.0, 3.0, 2.0, 2.0, 0.0, 1.0])
    assert compare.spearman(x, y) == pytest.approx(stats.spearmanr(x, y).statistic)
    assert compare.spearman(np.array([1.0, 2, 3]), np.array([3.0, 5, 9])) == pytest.approx(1.0)
    assert math.isnan(compare.spearman(x, np.ones(6)))


def _scored_sites(n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(4)
    volume = rng.lognormal(4, 1, n)
    return pd.DataFrame(
        {
            "site_id": [f"s{i}" for i in range(n)],
            "mode": "bike",
            "status": match.MATCHED,
            "volume": volume,
            "highway": ["primary", "residential", "cycleway"] * (n // 3),
            "bike_facility": ["lane", "none", "path"] * (n // 3),
            "flow:bike-broach-2011": volume * rng.lognormal(0, 0.3, n),
            "flow:bike-shortest": rng.lognormal(4, 1, n),
        }
    )


def test_scores_are_repeatable_and_paired_against_shortest() -> None:
    sites = _scored_sites()
    first, again = compare.scores(sites, "bike"), compare.scores(sites, "bike")
    assert repr(first) == repr(again)  # NaN fields never compare equal
    other = compare.scores(sites, "bike", seed=9)
    by = {s.name: s for s in first}
    assert by["bike-broach-2011"].low != {s.name: s for s in other}["bike-broach-2011"].low
    model, shortest = by["bike-broach-2011"], by["bike-shortest"]
    assert model.rho > 0.8 and model.low < model.rho < model.high
    assert model.difference == pytest.approx(model.rho - shortest.rho)
    assert model.difference_low > 0  # clearly better than random shortest flows
    assert math.isnan(shortest.difference)
    assert [s.name for s in first][-1] == "street class"


def test_too_few_sites_get_no_interval() -> None:
    found = compare.scores(_scored_sites(9), "bike")
    assert all(math.isnan(s.low) for s in found)


def test_the_street_class_baseline_adds_bike_facilities() -> None:
    sites = pd.DataFrame(
        {"highway": ["primary", "residential", "cycleway", "secondary_link"],
         "bike_facility": ["lane", "none", "path", "track"]}
    )  # fmt: skip
    assert compare.class_score(sites, "bike").tolist() == [7.0, 2.0, 5.0, 7.0]
    assert compare.class_score(sites, "walk").tolist() == [6.0, 2.0, 3.0, 5.0]


def test_top_share_counts_the_busiest_tenth_in_common() -> None:
    volume = np.arange(20, dtype=float)
    assert compare.top_share(volume, volume) == 1.0
    assert compare.top_share(volume, -volume) == 0.0
    model = volume.copy()
    model[[19, 0]] = model[[0, 19]]  # the busiest site modelled as the quietest
    assert compare.top_share(volume, model) == 0.5
    # A model with every flow equal scores what chance would, whatever the order.
    assert compare.top_share(volume, np.zeros(20)) == pytest.approx(0.1)
    assert compare.top_share(volume[::-1], np.zeros(20)) == pytest.approx(0.1)


def _records(rows: list[dict[str, Any]], **base: Any) -> gpd.GeoDataFrame:
    common = {"road": "made up st", "program": "Cyclical", "setyear": 2020,
              "setdate": pd.Timestamp("2020-05-01"), "cntdir": "both", "volume": 50,
              "bikepedfacility": "Bike Lane", "sidewalk": None}  # fmt: skip
    common.update(base)
    frame = pd.DataFrame([{**common, **r} for r in rows])
    points = [Point(x, y) for x, y in zip(frame.pop("x"), frame.pop("y"), strict=True)]
    return gpd.GeoDataFrame(frame, geometry=points, crs="EPSG:32618").to_crs(4326)


def test_dvrpc_records_become_sites_with_both_directions_added() -> None:
    records = _records(
        [
            {"x": 500000, "y": 4420000, "cntdir": "east", "volume": 30,
             "road": "Made Up St Eastbound Lanes"},
            {"x": 500015, "y": 4420012, "cntdir": "west", "volume": 20},  # same session
            {"x": 500005, "y": 4420000, "cntdir": "both", "volume": 999,
             "setdate": pd.Timestamp("2015-05-01"), "setyear": 2015},  # older: ignored
            {"x": 501000, "y": 4420000, "cntdir": "north", "volume": 12},  # one direction
            {"x": 502000, "y": 4420000, "volume": 70, "program": "Spec. Event"},
            {"x": 503000, "y": 4420000, "volume": 0},
            {"x": 504000, "y": 4420000, "road": "other st", "volume": 5},
            {"x": 504010, "y": 4420000, "road": "Other St", "volume": 7,
             "setdate": pd.Timestamp("2021-05-01"), "setyear": 2021},
        ]
    )  # fmt: skip
    sites = counts.check(dvrpc.sites(records, "bike"))
    assert len(sites) == 3
    by = {(match.street_key(str(r.road)), r.direction): r for r in sites.itertuples()}
    assert by[("made up st", "both")].volume == 50.0  # the two halves of a divided road
    assert by[("made up st", "north")].volume == 12.0
    assert by[("other st", "both")].volume == 7.0 and by[("other st", "both")].year == 2021
    assert set(sites.facility) == {"Bike Lane"}


def test_dvrpc_walking_sites_are_split_by_sidewalk() -> None:
    records = _records(
        [
            {"x": 500000, "y": 4420000, "sidewalk": "north", "volume": 300},
            {"x": 500000, "y": 4420010, "sidewalk": "south", "volume": 200},
        ]
    )
    sites = dvrpc.sites(records.drop(columns="bikepedfacility"), "walk")
    assert sorted(sites.side) == ["north", "south"] and sorted(sites.volume) == [200.0, 300.0]


def test_counts_files_are_read_as_geoparquet_by_either_suffix(tmp_path: Path) -> None:
    good = _counts([_site("a", "bike", X_CENTRAL, Y_MAIN)])
    for name in ("c.geoparquet", "c.PARQUET"):
        good.to_parquet(tmp_path / name)
        assert counts.read(tmp_path / name).site_id.tolist() == ["a"]


def test_counts_files_are_checked() -> None:
    good = _counts([_site("a", "bike", X_CENTRAL, Y_MAIN)])
    assert good.direction.tolist() == ["both"]
    with pytest.raises(counts.CountsError, match="mode"):
        counts.check(good.assign(mode="car"))
    with pytest.raises(counts.CountsError, match="volume"):
        counts.check(good.assign(volume=-1))
    with pytest.raises(counts.CountsError, match="unique"):
        counts.check(pd.concat([good, good]))
    with pytest.raises(counts.CountsError, match="missing"):
        counts.check(good.drop(columns="road"))


def test_the_scatter_plot_escapes_text_and_draws_each_point() -> None:
    svg = scatter_svg(Scatter("<b>run</b>", "x & y", "flow", ((1.0, 2.0), (100.0, 0.0))))
    assert "<b>" not in svg and "&lt;b&gt;run" in svg and "x &amp; y" in svg
    assert svg.count("<circle") == 2


@pytest.fixture(scope="module")
def network_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("validate")
    raw = {
        "study": "Demo town network", "synthetic": True, "area": {"source": "demo"},
        "destinations": [], "modes": ["walk", "bike"],
        "presets": {"walk": ["broach-2016", "shortest"], "bike": ["broach-2011", "shortest"]},
        "time_caps_min": {"walk": 20, "bike": 20}, "demand": {"weight": "residents"},
        "network_flows": {"destinations": 4},
    }  # fmt: skip
    path = folder / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    cli.build(config.load(path), folder / "out")
    return folder / "out"


def test_validate_writes_the_report_from_a_counts_file(network_run: Path, tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    rows = []
    for i in range(12):  # sites along the named streets of the made-up town
        x = demo.ORIGIN[0] + (i + 0.5) * demo.SPACING_M
        rows.append(_site(f"b{i}", "bike", x, Y_MAIN + 3, road="Main St",
                          volume=float(rng.integers(5, 500))))  # fmt: skip
        rows.append(_site(f"w{i}", "walk", X_CENTRAL + 3, demo.ORIGIN[1] + (i + 0.5) *
                          demo.SPACING_M, road="Central Avenue",
                          volume=float(rng.integers(50, 5000))))  # fmt: skip
    path = tmp_path / "counts.geojson"
    _counts(rows).to_file(path, driver="GeoJSON")
    vcli.main(["counts", "--out", str(network_run), "--counts", str(path)])
    folder = network_run / "validation"
    text = (folder / "report.md").read_text()
    assert text.startswith("# Modelled flows against counts, Demo town network")
    assert "## Walking" in text and "## Cycling" in text and "street class" in text
    assert "Counts: Made-up counter" in text
    page = (folder / "report.html").read_text()
    assert page.count("<svg") == 4  # one plot per run
    sites = gpd.read_parquet(folder / "sites.parquet")
    assert len(sites) == 24 and sites.status.eq(match.MATCHED).sum() == 24
    assert sites["flow:walk-shortest"].notna().sum() == 12


def test_validate_needs_network_flows(tmp_path: Path) -> None:
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "demo.yaml").read_text())
    raw["destinations"], raw["modes"] = raw["destinations"][:1], ["walk"]
    raw["presets"], raw["time_caps_min"] = {"walk": ["shortest"]}, {"walk": 20}
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    cli.build(config.load(path), tmp_path / "out")
    good = tmp_path / "c.geojson"
    _counts([_site("a", "bike", X_CENTRAL, Y_MAIN)]).to_file(good, driver="GeoJSON")
    with pytest.raises(SystemExit) as stop:
        vcli.main(["counts", "--out", str(tmp_path / "out"), "--counts", str(good)])
    assert stop.value.code == 2
