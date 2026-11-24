"""The run report's tables and method note (walkbike_routes.report.tables)."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from pyproj import Transformer
from shapely.geometry import LineString

from walkbike_routes import __main__ as cli
from walkbike_routes import config
from walkbike_routes.analysis import exposure_lists, exposure_rules
from walkbike_routes.costs import presets
from walkbike_routes.report import map as page_map
from walkbike_routes.report import tables
from walkbike_routes.report import text as words
from walkbike_routes.report.map import build_maps
from walkbike_routes.report.text import DRAFT, NOT_COUNTS, ROUTE_COST_DOC, SYNTHETIC

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "config" / "demo.yaml"
CRS = "EPSG:32613"


@pytest.fixture(scope="module")
def out(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The demo run, walking under two presets, to the school only."""
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = [d for d in raw["destinations"] if d["id"] == "school"]
    raw["presets"]["walk"] = ["broach-2016", "shortest"]
    folder = tmp_path_factory.mktemp("tables")
    path = folder / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    target = folder / "out"
    cli.build(config.load(path), target)
    return target


def test_the_report_is_written_both_ways(out: Path) -> None:
    md, page = tables.build_report(out)
    assert md == out / "report.md" and page == out / "report.html"
    text = md.read_text()
    assert text.startswith("# Route access report, Demo town")
    assert SYNTHETIC in text and NOT_COUNTS in text and DRAFT in text
    assert "## Demo Elementary" in text and "## Method" in text
    assert "<table>" in page.read_text() and SYNTHETIC in page.read_text()


def test_the_summary_figures_come_from_summary_csv(out: Path) -> None:
    summary = tables.read_summary(out)
    walk = summary[summary.preset.eq("broach-2016") & summary["mode"].eq("walk")].iloc[0]
    table = tables.summary_table(summary)
    row = next(
        r
        for r in table.rows
        if r[0] == "Walking"
        and r[1] == tables.Noted("broach-2016", words.model_tip("walk", "broach-2016"))
    )
    assert row[2] == f"{walk.total_weight:,.0f}"
    assert row[3] == f"{walk.within_cap_weight:,.0f} (≤ {walk.time_cap_min:g} min)"
    assert row[4] == f"{100 * walk.within_cap_weight / walk.total_weight:.0f}%"
    bands = [
        c
        for c in map(str, summary.columns)
        if c.startswith("within_") and c.endswith("_min_weight")
    ]
    assert len([h for h in table.header if h.startswith("≤")]) == len(bands)
    car = next(r for r in table.rows if r[0] == "Driving")
    assert car[-1] == "–"  # cars are not decayed: no expected trips
    assert "(≤" not in str(car[3])  # nor is there a time limit to be within
    assert [r[0] for r in table.rows] == ["Walking", "Walking", "Cycling", "Driving"]


def test_each_destination_opens_with_who_can_get_there(out: Path) -> None:
    summary = tables.read_summary(out)
    who = tables.who_table(tables.main_rows(summary))
    assert who.header[:5] == ("Mode", "Time limit", "Can get there", "Share", "Median minutes")
    assert [r[0] for r in who.rows] == ["Walking", "Cycling", "Driving"]  # main runs only
    assert who.rows[0][1] == "20 min" and who.rows[2][1] == "none"
    build_maps(out)
    text = tables.build_report(out)[0].read_text()
    section = text.split("## Demo Elementary", 1)[1]
    assert section.lstrip().startswith("[Open the map](school/map.html)")
    walk = summary[summary["mode"].eq("walk")].iloc[0]
    # How much there is to fix comes first, then who can get there, as one table.
    assert section.split("\n\n")[2].startswith("To fix first:")
    assert f"Of the {walk.total_weight:,.0f} residents aged 5-11 in the study area:" in section
    assert f"| Walking | 20 min | {walk.within_cap_weight:,.0f} |" in section


def test_a_run_from_before_units_and_decay_were_recorded_still_reports() -> None:
    old = pd.DataFrame(
        {"destination_id": ["s"], "mode": ["walk"], "preset": ["shortest"], "total_weight": [10.0],
         "within_cap_weight": [4.0], "time_cap_min": [20.0]}
    )  # fmt: skip
    assert tables.glance(old, []) == ""
    assert tables.who_line(old) == "Of the 10 people in the study area:"
    assert tables.who_table(old).rows == (("Walking", "20 min", "4", "40%", "–"),)


def test_figures_are_whole_people_and_plain_lengths() -> None:
    assert [words.people(v) for v in (0.0, 0.2, 0.6, 2.56, 1234.4)] == [
        "0", "fewer than 1", "1", "3", "1,234",
    ]  # fmt: skip
    assert [words.length(v) for v in (3.0, 118.0, 996.0, 1240.0)] == [
        "10 m", "120 m", "1000 m", "1.2 km",
    ]  # fmt: skip
    assert words.points(0.75) == "0.75" and words.minutes(7.0) == "7.0"
    assert words.flow_heading("households", False) == "Households"
    assert words.flow_heading("households", True) == "Expected trips"


def test_two_presets_get_an_agreement_table(out: Path) -> None:
    text = tables.build_report(out)[0].read_text()
    busiest = text.split("## Reference: busiest streets", 1)[1]
    walking = busiest.split("### Demo Elementary: walking", 1)[1].split("###", 1)[0]
    assert "| # | broach-2016 | shortest |" in walking
    assert "under every preset" in walking
    # How streets are grouped is said once, not under every table.
    assert text.count(tables.HOW_STREETS) == 1


def test_the_screen_is_in_route_cost_minutes_and_says_what_they_are(out: Path) -> None:
    paths = tables.build_report(out)
    text = paths[0].read_text()
    assert "### Walking: where a change would open new routes" in text
    assert "| Route-cost min saved |" in text and "Generalized min saved" not in text
    assert text.count(tables.SCREEN_NOTE) == 1  # in the method, once
    # Under each screen, its terms in a line; in the page, on each column too.
    assert tables.SCREEN_TERMS in text and "often called generalized cost" in tables.SCREEN_TERMS
    page = paths[1].read_text()
    for column in ("Expected trips helped", "Share of those counted", "Route-cost min saved"):
        assert re.search(rf'<span tabindex="0" data-tip="[^"]+">{column}</span>', page), column


def test_the_method_cites_each_preset_and_curve(out: Path) -> None:
    text = tables.build_report(out)[0].read_text()
    for mode, name in (("walk", "broach-2016"), ("walk", "shortest"), ("car", "central")):
        assert presets.load(mode, name).title in text
    assert "Walking: speed, waits and sidewalks (walk/shortest)" in text
    assert "Driving (car/\\*)" in text
    assert "### Distance-decay curves" in text and "National Household Travel Survey" in text


def test_the_shipped_sections_match_the_assumptions_doc() -> None:
    found = tables.assumption_sections(ROOT / "docs" / "assumptions.md")
    # To refresh: write this dict, sorted, to src/walkbike_routes/report/assumption_sections.json.
    assert tables.shipped_sections() == found


def test_every_shipped_preset_has_an_assumptions_section() -> None:
    sections = tables.shipped_sections()
    folder = ROOT / "src" / "walkbike_routes" / "presets"
    for path in folder.glob("*/*.yaml"):
        mode, name = path.parent.name, path.stem
        assert f"{mode}/{name}" in sections or f"{mode}/*" in sections, path


def test_files_not_in_the_manifest_are_left_out(out: Path) -> None:
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"] = {
        k: v for k, v in manifest["files"].items() if not k.endswith("screen.parquet")
    }
    blocks = tables.report_blocks(out, manifest)
    assert not any(
        isinstance(b, tables.Heading) and b.text.startswith("Improvement screen") for b in blocks
    )


def test_text_is_escaped_in_markdown_and_html() -> None:
    blocks: list[tables.Block] = [
        tables.Heading(2, "# <b>Made-up</b> | place"),
        tables.Para("- a *made-up* note with [link](x) and </p><script>"),
        tables.Table(("Name",), (("a | b <i>",),), (False,)),
    ]
    md = tables.markdown(blocks)
    assert "## \\# \\<b\\>Made-up\\</b\\> \\| place" in md
    assert md.splitlines()[2].startswith("\\- a \\*made-up\\*")
    assert "| a \\| b \\<i\\> |" in md
    page = tables.page(blocks, "<title>")
    assert "</p><script>" not in page and "<b>Made-up" not in page and "<i>" not in page
    assert "&lt;/p&gt;&lt;script&gt;" in page and "<title>&lt;title&gt;</title>" in page


def test_streets_group_by_name_sidewalk_crossing_and_way() -> None:
    links = gpd.GeoDataFrame(
        {
            "link_id": ["a", "b", "s", "c", "p1", "p2", "q"],
            "u": ["1", "2", "10", "11", "20", "21", "30"],
            "v": ["2", "3", "12", "2", "21", "22", "31"],
            "osm_way_id": [1, 1, 2, 3, 4, 4, 5],
            "name": ["Main Street", "Main Street", None, "", None, None, None],
            "highway": ["secondary", "secondary", "footway", "footway", "footway", "footway",
                        "footway"],
            "footway": [None, None, "sidewalk", "crossing", None, None, None],
            "crossed_road": ["", "", "", "secondary", "", "", ""],
        },
        geometry=[
            LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)]),
            LineString([(0, 8), (100, 8)]), LineString([(100, -10), (100, 0)]),
            LineString([(0, 500), (50, 500)]), LineString([(50, 500), (100, 500)]),
            LineString([(0, 900), (50, 900)]),
        ],
        crs=CRS,
    )  # fmt: skip
    names = tables.street_names(links)
    cycle = links.copy()
    cycle.loc[3, ["highway", "footway"]] = ["cycleway", None]  # cycleway=crossing
    assert tables.street_names(cycle)["c"] == "crossing of Main Street"
    assert names["a"] == names["b"] == names["s"] == "Main Street"
    assert names["c"] == "crossing of Main Street"
    assert names["p1"] == names["p2"] != names["q"]
    assert tables.label(names["p1"]) == "unnamed footway (OSM way 4)"
    assert tables.label(names["q"]) == "unnamed footway (OSM way 5)"
    flows = pd.DataFrame(
        {"physical_edge_id": ["a", "b", "s", "p1", "p2", "q"],
         "potential_flow": [5.0, 7.0, 9.0, 3.0, 4.0, 2.0]}
    )  # fmt: skip
    top = tables.top_streets(flows, names)
    assert top.to_dict() == {"Main Street": 9.0, names["p1"]: 4.0, names["q"]: 2.0}


def test_a_street_in_every_top_list_is_not_marked() -> None:
    tops = {
        "a": pd.Series({"Main Street": 9.0, "Oak Street": 5.0}),
        "b": pd.Series({"Main Street": 8.0, "Elm Street": 6.0}),
    }
    table, note = tables.busiest_blocks(tops)
    assert isinstance(table, tables.Table) and isinstance(note, tables.Para)
    assert table.rows == (("1", "Main Street (9)", "Main Street (8)"),
                          ("2", "Oak Street (5) †", "Elm Street (6) †"))  # fmt: skip
    assert note.text.startswith("1 of these streets")


def test_the_cli_writes_the_report(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEMO.read_text())
    raw["destinations"] = raw["destinations"][:1]
    raw["modes"], raw["presets"] = ["walk"], {"walk": ["shortest"]}
    raw["time_caps_min"] = {"walk": 20}
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw))
    cli.main(["run", "--config", str(path), "--out", str(tmp_path / "a"), "--no-map"])
    assert (tmp_path / "a" / "report.md").exists()
    (tmp_path / "a" / "report.md").unlink()
    cli.main(["report", "--out", str(tmp_path / "a")])
    assert (tmp_path / "a" / "report.html").exists() and (tmp_path / "a" / "report.md").exists()


def test_the_method_says_when_decay_is_off() -> None:
    def summary(curves: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            {"mode": ["walk", "bike"], "preset": ["shortest", "shortest"], "decay_curve": curves}
        )

    def text(curves: list[str]) -> str:
        return tables.markdown(tables.method_blocks(summary(curves)))

    assert "distance-decay share" in text(["walk-everyday", "bike-everyday"])
    assert "Distance decay is off" in text(["none", "none"])
    assert "Expected trips" not in text(["none", "none"])
    mixed = text(["walk-everyday", "none"])
    assert "except in bike/shortest, where distance decay is off" in mixed
    assert "Driving" not in mixed


def test_the_report_says_which_local_layers_a_run_used() -> None:
    none = tables.local_layers_line({"local_layers": []})
    assert "OpenStreetMap only" in none
    used = tables.local_layers_line(
        {"local_layers": [{"kind": "sidewalks", "name": "City sidewalks"}]}
    )
    assert used == "City or county layers used: City sidewalks (sidewalks)."
    assert tables.local_layers_line({"local_layers": [], "synthetic": True}) == ""
    assert tables.local_layers_line({}) == ""  # a run from before the manifest said


def test_main_runs_list_where_routes_meet_traffic(out: Path) -> None:
    import geopandas as gpd

    from walkbike_routes.analysis.exposure import CROSSING_COLUMNS, STREET_COLUMNS

    run = out / "school" / "walk-broach-2016"
    streets = gpd.read_parquet(run / "exposure_streets.parquet")
    crossings = gpd.read_parquet(run / "exposure_crossings.parquet")
    assert list(streets.columns) == STREET_COLUMNS
    assert list(crossings.columns) == CROSSING_COLUMNS
    assert not (out / "school" / "walk-shortest" / "exposure_streets.parquet").exists()
    text = tables.build_report(out)[0].read_text()
    assert "### Walking: places to fix first" in text
    section = text.split("### Walking: places to fix first", 1)[1].split("\n### ", 1)[0]
    # Every fix-first place is listed, under the name the map gives it.
    for kind, listed in (("S", streets), ("C", crossings)):
        for rank in listed[listed.tier.eq("fix first")]["rank"]:
            assert f"| {kind}{rank} |" in section or f"| [{kind}{rank}](" in section
        for rank in listed[listed.tier.ne("fix first")]["rank"]:
            assert f"| {kind}{rank} |" not in section and f"| [{kind}{rank}](" not in section
    if streets.tier.ne("fix first").any() or crossings.tier.ne("fix first").any():
        assert "### Demo Elementary: walking" in text.split("## Reference: watch lists", 1)[1]
    # The terms the lists use are defined once, before them.
    terms = text.split("## Key terms", 1)[1].split("\n## ", 1)[0]
    assert [line.split(":**")[0] for line in terms.strip().splitlines()] == [
        "- **Risk points", "- **Expected trips", "- **Fix first", "- **Watch", "- **S1, C1",
        "- **Route-cost minutes",
    ]  # fmt: skip
    # The barrier lists' unit is defined, with a link to the full explanation.
    assert f"generalized cost. [More]({ROUTE_COST_DOC})" in terms
    assert "**Fix first:** A place with 3 or more risk points, and at least 5 people" in terms
    assert text.index("## Key terms") < text.index("places to fix first")


def test_a_link_is_written_both_ways() -> None:
    blocks: list[tables.Block] = [tables.Link("Open the map", "a school/map.html")]
    assert tables.markdown(blocks) == "[Open the map](a%20school/map.html)\n"
    assert '<a href="a%20school/map.html">Open the map</a>' in tables.page(blocks, "t")


def _places(tiers: list[str]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "rank": range(1, len(tiers) + 1),
            "label": [f"Made-up Street {i}" for i in range(len(tiers))],
            "people": 12.4, "risk_points": 3.0, "risk_factors": "a made-up factor (+3)",
            "tier": tiers, "tier_reason": "A made-up reason.", "people_by_destination": "",
        },
        geometry=[LineString([(0, 0), (118, 0)])] * len(tiers), crs=CRS,
    )  # fmt: skip


def test_fix_first_lists_every_place_or_says_how_many_it_left_out() -> None:
    streets = _places(["watch"] + ["fix first"] * 7)
    none = streets.iloc[:0]
    every = tables.fix_first_blocks("Walking", streets, none, "people", False)
    assert [type(b) for b in every] == [tables.Heading, tables.Table]
    table = every[1]
    assert isinstance(table, tables.Table)
    assert [r[0] for r in table.rows] == [f"S{i}" for i in range(2, 9)]
    assert table.header == (
        "On the map", "Street", "People", "Length", "Risk points", "Why it has these points",
    )  # fmt: skip
    assert table.rows[0][2:5] == ("12", "120 m", "3")
    assert table.more_after is None  # seven rows are shown whole
    longer = tables.fix_first_blocks("Walking", _places(["fix first"] * 8), none, "people", False)
    assert isinstance(longer[1], tables.Table)
    assert len(longer[1].rows) == 8 and longer[1].more_after == 5  # opens at its first rows
    brief = tables.fix_first_blocks("Walking", streets, none, "people", True, limit=5)
    shown, note = brief[1], brief[2]
    assert isinstance(shown, tables.Table) and isinstance(note, tables.Para)
    assert len(shown.rows) == 5 and shown.header[2] == "Expected trips"
    assert note.text.startswith("The 5 highest-ranked of 7 streets to fix first")
    nothing = tables.fix_first_blocks("Walking", streets.iloc[:1], none, "people", False)
    assert isinstance(nothing[1], tables.Para) and "watch lists" in nothing[1].text
    # Beside the lists for all together a destination has no watch list in the report.
    alone = tables.fix_first_blocks("Walking", streets.iloc[:1], none, "people", False, limit=5)
    assert isinstance(alone[1], tables.Para) and "watch lists" not in alone[1].text
    assert alone[1].text.endswith("The rest are on its map.")
    watch = tables.watch_blocks("Made-up: walking", streets, none, "people", False)
    listed = watch[1]
    assert isinstance(listed, tables.Table)
    assert len(listed.rows) == 1
    assert listed.rows[0][0] == "S1" and listed.rows[0][2:] == (
        "12",
        "120 m",
        "3",
        "A made-up reason.",
    )
    place = listed.rows[0][1]
    assert isinstance(place, tables.Where) and place.text == "Made-up Street 0"


def test_a_place_links_to_the_map_and_to_a_street_map() -> None:
    # A made-up street 118 m long; its middle is where the links point.
    streets = _places(["fix first"])
    to_degrees = Transformer.from_crs(CRS, 4326, always_xy=True)
    lon, lat = to_degrees.transform(59.0, 0.0)
    table = tables.exposure_table(
        streets, "People", streets=True, where=("a school/map.html", "walk-made up")
    )
    tag, place = table.rows[0][0], table.rows[0][1]
    assert tag == tables.Linked("S1", f"a school/map.html#{lat:.5f},{lon:.5f},17,walk-made%20up,S1")
    assert isinstance(place, tables.Where)
    assert (place.lat, place.lon) == pytest.approx((lat, lon))
    url = f"https://www.google.com/maps/search/?api=1&query={lat:.5f},{lon:.5f}"
    md = tables.markdown([table])
    assert f"| [S1]({tag.href}) | Made-up Street 0 ([Google Maps]({url})) |" in md
    page = tables.page([table], "t")
    assert f'<a class="tag" href="{tag.href}">S1</a>' in page
    assert f'Made-up Street 0 <a class="ext" href="{html.escape(url)}"' in page
    # A watch place too far down for the map to draw has no link to it.
    low = _places(["fix first"] * 11 + ["watch"])
    cells = [
        r[0] for r in tables.exposure_table(low, "People", streets=True, where=("m", "r")).rows
    ]
    assert all(isinstance(c, tables.Linked) for c in cells[:11]) and cells[11] == "S12"
    low["tier"] = ["watch"] * 12
    cells = [
        r[0] for r in tables.exposure_table(low, "People", streets=True, where=("m", "r")).rows
    ]
    assert isinstance(cells[9], tables.Linked) and cells[10:] == ["S11", "S12"]
    # With no map beside the report the label is plain; with no shape, so is the place.
    plain = tables.exposure_table(streets, "People", streets=True)
    assert plain.rows[0][0] == "S1"
    bare = tables.exposure_table(
        pd.DataFrame(streets.drop(columns="geometry")), "People", streets=False
    )
    assert bare.rows[0][:2] == ("C1", "Made-up Street 0")


def test_factors_and_destinations_are_one_to_a_line_in_the_page() -> None:
    streets = _places(["fix first"])
    streets["risk_factors"] = "a made-up factor (+2); another <made-up> one (+1)"
    table = tables.exposure_table(streets, "People", streets=True)
    assert table.rows[0][5] == tables.Lines(("a made-up factor (+2)", "another <made-up> one (+1)"))
    assert "| a made-up factor (+2); another \\<made-up\\> one (+1) |" in tables.markdown([table])
    assert (
        '<ul class="f"><li>a made-up factor (+2)</li><li>another &lt;made-up&gt; one (+1)</li></ul>'
        in tables.page([table], "t")
    )


def test_a_long_table_and_a_reference_section_start_closed_in_the_page() -> None:
    table = tables.Table(("Name",), tuple((f"row {i}",) for i in range(4)), (False,), more_after=2)
    fold = tables.Fold(2, "Reference: made-up", (tables.Para("Inside."), table))
    blocks: list[tables.Block] = [tables.Heading(1, "T"), tables.Heading(2, "First part"), fold]
    md = tables.markdown(blocks)
    assert "## Reference: made-up\n\nInside." in md and md.count("| row ") == 4
    page = tables.page(blocks, "t")
    assert '<details class="l2" id="reference-made-up"><summary><h2>Reference: made-up</h2>' in page
    assert (
        '<tbody><tr><td>row 0</td></tr><tr><td>row 1</td></tr></tbody><tbody class="more" hidden>'
        in page
    )
    assert 'data-more="Show all 4" data-less="Show the first 2">Show all 4</button>' in page
    # The top sections are listed after the title, each a link to its place in the page.
    assert (
        '<nav><a href="#first-part">First part</a> '
        '<a href="#reference-made-up">Reference: made-up</a></nav>' in page
    )
    # Sections of one name each keep their own place.
    same = tables.page([tables.Heading(2, "Central School")] * 3, "t")
    for anchor in ("central-school", "central-school-2", "central-school-3"):
        assert same.count(f'<h2 id="{anchor}">') == 1 and same.count(f'href="#{anchor}"') == 1
    short = tables.page([tables.Table(("Name",), (("a",), ("b",)), (False,), more_after=2)], "t")
    assert "<button" not in short and "tbody class" not in short


def test_a_mode_with_no_limit_says_why_it_is_not_everyone() -> None:
    rows = pd.DataFrame(
        {"destination_id": ["s", "s"], "mode": ["walk", "car"], "preset": ["a", "b"],
         "total_weight": [1000.0, 1000.0], "within_cap_weight": [300.0, 970.0],
         "time_cap_min": [20.0, None]}
    )  # fmt: skip
    note = tables.unconnected_note(rows)
    assert note.startswith(
        "Driving is not 100%: the other 30 (3%) are at homes with no route on the map"
    )
    assert "homes with no route on the map" in note
    rows.loc[1, "within_cap_weight"] = 999.9  # everyone, as the table prints it
    assert tables.unconnected_note(rows) == ""
    assert tables.unconnected_note(rows.iloc[:1]) == ""  # a time limit explains itself


def test_all_from_one_destination_is_just_its_name() -> None:
    labels = {"a": "Almond"}
    assert words.from_each(json.dumps({"a": 7.0}), labels) == "Almond"
    assert words.from_each(json.dumps({"b": 7.0}), labels) == "b"


def test_every_column_and_route_model_is_defined_on_hover(out: Path) -> None:
    _, page = tables.build_report(out)
    doc = page.read_text()
    bare = {h for h in re.findall(r"<th(?:\s[^>]*)?>(.*?)</th>", doc) if "data-tip=" not in h}
    assert bare <= {"#"}, bare
    # A route model's name, wherever it is a cell, says what the model is.
    assert re.search(r'data-tip="Route model: [^"]+">broach-2016</span>', doc)
    # Jargon in sentences is defined at its first use in each passage.
    tip = html.escape(words.GLOSSARY["path-size logit"], quote=True)
    assert f'<span tabindex="0" data-tip="{tip}">path-size logit</span>' in doc


def test_jargon_is_defined_once_per_passage_and_only_as_a_whole_word() -> None:
    got = tables._glossed("Traffic stress & traffic stress; a stopwatch, a watch-list, <watch>")
    assert got.count("data-tip=") == 2  # the first "Traffic stress", and "watch"
    assert got.startswith('<span tabindex="0" data-tip="Level of traffic stress')
    assert ">Traffic stress</span> &amp; traffic stress;" in got
    assert "a stopwatch, a watch-list, &lt;<span" in got
    assert all(key == key.lower() for key in words.GLOSSARY)  # matching lowers the text


def test_a_route_models_note_is_for_its_own_mode() -> None:
    # Walking and cycling both ship a "shortest" model; each cell says which (TT1).
    rows = pd.DataFrame(
        {
            "mode": ["walk", "bike"],
            "preset": ["shortest", "shortest"],
            "total_weight": [100.0, 100.0],
            "within_cap_weight": [40.0, 60.0],
            "time_cap_min": [20.0, 20.0],
            "median_minutes_within_cap": [12.0, 9.0],
            "expected_weight": [None, None],
            "decay_curve": ["", ""],
        }
    )
    walk, bike = (row[1] for row in tables.summary_table(rows).rows)
    assert walk == tables.Noted(
        "shortest", f"Route model: {presets.load('walk', 'shortest').title}."
    )
    assert bike == tables.Noted(
        "shortest", f"Route model: {presets.load('bike', 'shortest').title}."
    )
    flows = pd.Series([3.0], index=["Main Street"])
    table = tables.busiest_blocks({"shortest": flows, "broach-2011": flows}, mode="bike")[0]
    assert isinstance(table, tables.Table)
    assert table.tips[1] == bike.tip


def test_glossary_notes_do_not_fix_what_a_study_can_set() -> None:
    # The drop-off share and points are rules a study may change (TT2); some route
    # models' values are set by hand, not taken from a study (TT3).
    assert not re.search(r"\d", words.GLOSSARY["drop-off"])
    assert "set by hand" in words.GLOSSARY["uncalibrated"]
    assert "Not fitted to observed trips" in words.GLOSSARY["uncalibrated"]


def test_watch_places_after_ten_fix_first_places_are_still_mapped_and_linked(
    tmp_path: Path,
) -> None:
    """Fix-first places are numbered first; the busiest watch place keeps its map
    entry and its link (review FO1)."""
    made = gpd.GeoDataFrame(
        {
            "place_id": [f"f{i:02d}" for i in range(10)] + ["watch"],
            "label": [f"Made-up Street {i}" for i in range(11)],
            "people": [6.0] * 10 + [100.0], "risk_points": [3.0] * 10 + [2.0],
            "risk_factors": "a made-up factor", "people_by_destination": "",
        },
        geometry=[LineString([(0, 0), (118, 0)])] * 11, crs=CRS,
    )  # fmt: skip
    made["exposure"] = made.people * made.risk_points
    ranked = exposure_lists.ranked(
        made, exposure_lists.STREET_COLUMNS, CRS, 200.0, exposure_rules.FixFirst()
    )
    last = ranked.iloc[-1]
    assert (last.place_id, last["rank"], last.tier_rank, last.tier) == ("watch", 11, 1, "watch")
    ranked.to_parquet(tmp_path / "exposure_streets.parquet")
    to_degrees = Transformer.from_crs(CRS, 4326, always_xy=True)
    drawn = page_map._exposure(tmp_path, to_degrees, Path.exists)["exposure"]
    assert [r["tag"] for r in drawn] == [f"S{i}" for i in range(1, 12)]
    cells = [
        r[0] for r in tables.exposure_table(ranked, "People", streets=True, where=("m", "r")).rows
    ]
    linked = [c for c in cells if isinstance(c, tables.Linked)]
    assert len(linked) == 11 and linked[10].text == "S11"
