"""The map page runs in a real browser: every mode and layer draws without a script error.

Skipped where no Chrome or Chromium is installed.
"""

from __future__ import annotations

import dataclasses
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from walkbike_routes import __main__ as cli
from walkbike_routes import config
from walkbike_routes.report import tables
from walkbike_routes.report.map import build_maps

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"
BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
CHROME = next((found for name in BROWSERS if (found := shutil.which(name))), None)

# Runs in the page once it has drawn: every mode and time limit in turn, with
# every layer on, a list row clicked, zoomed in and out, then set for printing and back.
EXERCISE = """
for (const mode of [...$("mode").options].map(o => o.value)) {
  $("mode").value = mode; $("mode").dispatchEvent(new Event("change"));
  for (const limit of [...$("limit").options].map(o => o.value)) {
    $("limit").value = limit; $("limit").dispatchEvent(new Event("change"));
    for (const box of document.querySelectorAll("#bar input[type=checkbox]")) box.checked = true;
    draw();
    const row = document.querySelector("tr[data-go]"); if (row) row.click();
    map.setZoom(16); map.setZoom(12);
    toPrint(); fromPrint();
    for (const box of document.querySelectorAll("#bar input[type=checkbox]")) box.checked = false;
    draw();
  }
}
"""
WATCH = (
    "<script>window.addEventListener('error',"
    "e=>document.body.setAttribute('data-err',e.message));</script>"
)
RUN = """<script>window.addEventListener("load", () => setTimeout(() => {
  try { %s; document.body.setAttribute("data-ok", "ok"); }
  catch (e) { document.body.setAttribute("data-err", String(e && e.message || e)); }
}, 500));</script></body>"""


@pytest.fixture(scope="module")
def pages(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    """The demo town's maps: one for each destination and one for all together.

    With a shorter time limit, so each page can switch to it.
    """
    out = tmp_path_factory.mktemp("maps")
    cli.build(dataclasses.replace(config.load(DEMO), shorter_limits_min=(5.0,)), out)
    return build_maps(out, offline=True)


def _result(page: Path, exercise: str = EXERCISE) -> list[str]:
    """What the page reports after drawing and being exercised: ok, or its script errors."""
    assert CHROME is not None
    text = page.read_text()
    assert "<script>\nconst D=" in text and "</body>" in text
    text = text.replace("<script>\nconst D=", WATCH + "<script>\nconst D=", 1)
    copy = page.with_name("_smoke.html")
    copy.write_text(text.replace("</body>", RUN % exercise, 1))
    dom = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=20000", "--dump-dom", copy.resolve().as_uri()],
        capture_output=True, text=True, timeout=180, check=False,
    ).stdout  # fmt: skip
    return re.findall(r'data-(?:ok|err)="[^"]*"', dom)


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the map in")
def test_every_map_draws_each_mode_and_layer_without_a_script_error(pages: list[Path]) -> None:
    assert sorted(p.parent.name for p in pages) == ["all-destinations", "downtown", "school"]
    for page in pages:
        assert _result(page) == ['data-ok="ok"'], page.parent.name


# Jargon in map text is defined on hover at its first use, as a whole word only.
GLOSS = """
const got = gl("Traffic stress & traffic stress, a stopwatch, a watch-list");
const marked = got.match(/class="gl"/g) || [];
if (marked.length !== 1 || !got.startsWith('<span class="gl" tabindex="0" title="Level of traffic stress'))
  throw new Error("gloss: " + got);
if (!got.includes("&amp; traffic stress,")) throw new Error("not escaped once: " + got);
"""


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the map in")
def test_the_map_defines_jargon_on_hover(pages: list[Path]) -> None:
    assert _result(pages[0], GLOSS) == ['data-ok="ok"']


# The page draws its own notes: Chrome on Wayland never shows native title tooltips.
HOVER = """
const el = document.querySelector("label[title]"), note = el.title, tip = $("tip");
el.dispatchEvent(new MouseEvent("mouseover", {bubbles: true}));
if (tip.hidden || tip.textContent !== note) throw new Error("no note: " + tip.textContent);
if (el.hasAttribute("title")) throw new Error("the native tooltip would show too");
el.dispatchEvent(new MouseEvent("mouseout", {bubbles: true}));
if (!tip.hidden) throw new Error("note stays after the pointer leaves");
"""


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the map in")
def test_the_map_draws_its_own_notes_on_hover(pages: list[Path]) -> None:
    assert _result(pages[0], HOVER) == ['data-ok="ok"']


# Switching to the shorter limit shows its own run, with its flows and lists, keeps
# the route model, says the trips are cut, and switching back returns the full run.
LIMIT = """
$("mode").value = "walk"; $("mode").dispatchEvent(new Event("change"));
const full = run(), sum = r => r.flow.reduce((t, x) => t + x[1], 0);
if ($("limitwrap").style.display === "none") throw new Error("no switch");
if ([...$("limit").options].map(o => o.textContent).join() !== "20 min,5 min" &&
    [...$("limit").options].map(o => o.textContent).join() !== "Full limit,5 min")
  throw new Error("options: " + [...$("limit").options].map(o => o.textContent));
$("limit").value = "5"; $("limit").dispatchEvent(new Event("change"));
const cut = run();
if (cut.id !== full.id + "@5" || cut.preset !== full.preset) throw new Error("run: " + cut.id);
if (!(sum(cut) < sum(full))) throw new Error("flows not cut");
if (!/Trips cut at 5 minutes/.test($("panel").textContent)) throw new Error("no note");
if (cut.screen.length) throw new Error("barriers at the shorter limit");
$("showoverlap").checked = true; draw();
const drawn = overlapLayer.getLayers().length, at = D.overlap["5"] || [], all = D.overlap[""] || [];
if (drawn !== at.length) throw new Error(`overlap drawn ${drawn}, at 5 min ${at.length}`);
if (all.length && JSON.stringify(at) === JSON.stringify(all)) throw new Error("overlap not cut");
$("showoverlap").checked = false;
$("limit").value = ""; $("limit").dispatchEvent(new Event("change"));
if (run().id !== full.id) throw new Error("not back: " + run().id);
$("mode").value = "car"; $("mode").dispatchEvent(new Event("change"));
if (D.runs.some(r => r.mode === "car") && $("limitwrap").style.display !== "none")
  throw new Error("switch shown for driving");
"""


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the map in")
def test_the_map_switches_to_a_shorter_time_limit(pages: list[Path]) -> None:
    for page in pages:
        assert _result(page, LIMIT) == ['data-ok="ok"'], page.parent.name


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the map in")
def test_fix_first_starts_off_unless_a_link_names_one_of_its_places(
    pages: list[Path], tmp_path: Path
) -> None:
    page = pages[0]
    off = 'if ($("showexposure").checked) throw new Error("on");'
    assert _result(page, off) == ['data-ok="ok"']
    text = page.read_text()
    tag = re.search(r'"tag":"([A-Z][0-9]+)"', text)
    assert tag is not None
    # The report's link: centre, zoom, run, place.
    linked = text.replace(
        "<script>\nconst D=",
        f"<script>history.replaceState(null,'','#0,0,3,walk-broach-2016,{tag[1]}');</script>"
        "<script>\nconst D=", 1,
    )  # fmt: skip
    copy = page.with_name("_linked.html")
    copy.write_text(linked)
    on = 'if (!$("showexposure").checked) throw new Error("off");'
    assert _result(copy, on) == ['data-ok="ok"']


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the map in")
def test_the_check_notices_a_script_error(pages: list[Path], tmp_path: Path) -> None:
    broken = tmp_path / "map.html"
    broken.write_text(pages[0].read_text().replace("</body>", "<script>nope()</script></body>", 1))
    assert any(found.startswith("data-err=") for found in _result(broken))


# Runs in the report once it has loaded: a column's note shows on focus and goes on
# blur, a long table opens and closes, and a closed section opens for a link to it
# and for printing.
REPORT = """
const fail = why => { throw new Error(why); };
const tip = document.getElementById("tip");
const noted = document.querySelector("[data-tip]");
noted.focus();
if (tip.hidden || tip.textContent !== noted.dataset.tip) fail("no note on focus");
noted.blur();
if (!tip.hidden) fail("the note stayed");
const button = document.querySelector("button.more"), rest = document.querySelector("tbody.more");
button.click();
if (rest.hidden || !button.textContent.startsWith("Show the first")) fail("the table did not open");
button.click();
if (!rest.hidden) fail("the table did not close");
const closed = document.querySelector("details[id]");
if (closed.open) fail("a reference section starts open");
location.hash = "#" + closed.id;
window.dispatchEvent(new Event("hashchange"));
if (!closed.open) fail("a link did not open its section");
closed.open = false;
window.dispatchEvent(new Event("beforeprint"));
if (!closed.open) fail("printing did not open the sections");
window.dispatchEvent(new Event("afterprint"));
if (closed.open) fail("the sections stayed open after printing");
"""


def _report_result(page: Path, exercise: str = REPORT) -> list[str]:
    assert CHROME is not None
    text = page.read_text()
    assert "<body>" in text and "</body>" in text
    copy = page.with_name("_smoke.html")
    copy.write_text(
        text.replace("<body>", "<body>" + WATCH, 1).replace("</body>", RUN % exercise, 1)
    )
    dom = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=20000", "--dump-dom", copy.resolve().as_uri()],
        capture_output=True, text=True, timeout=180, check=False,
    ).stdout  # fmt: skip
    return re.findall(r'data-(?:ok|err)="[^"]*"', dom)


def _long_report(folder: Path) -> Path:
    """A made-up report with a column note, a table longer than it opens at, and a fold."""
    table = tables.Table(
        ("Name",), tuple((f"row {i}",) for i in range(9)), (False,), ("A made-up note.",), 5
    )
    blocks: list[tables.Block] = [
        tables.Heading(1, "Made-up report"),
        tables.Heading(2, "First"),
        table,
        tables.Fold(2, "Reference: made-up", (tables.Para("Inside."),)),
    ]
    page = folder / "report.html"
    page.write_text(tables.page(blocks, "Made-up report"))
    return page


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the report in")
def test_the_reports_notes_long_tables_and_folds_work(pages: list[Path], tmp_path: Path) -> None:
    assert _report_result(_long_report(tmp_path)) == ['data-ok="ok"']
    # The demo's own report loads without a script error.
    report = tables.build_report(pages[0].parent.parent)[1]
    assert _report_result(report, "") == ['data-ok="ok"']
    assert _report_result(_long_report(tmp_path), "throw new Error('made-up')") == [
        'data-err="made-up"'
    ]


# The crash forecast layer: drawn when its box is ticked, with a legend entry and popups.
CRASH = (
    EXERCISE
    + """
const box = $("showcrash");
if (box.parentElement.style.display === "none") throw new Error("crash toggle hidden");
box.checked = true; draw();
if (!map.hasLayer(crashLayer) || crashLayer.getLayers().length !== 2) throw new Error("crash layer");
if (!lg.getContainer().innerHTML.includes("Crash forecast")) throw new Error("crash legend");
crashLayer.getLayers()[0].openPopup();
if (!document.querySelector(".leaflet-popup-content").textContent.includes("rank 1"))
  throw new Error("crash popup");
box.checked = false; draw();
if (map.hasLayer(crashLayer)) throw new Error("crash layer not removed");
"""
)


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the map in")
def test_the_crash_forecast_layer_toggles_with_a_legend_and_popups(pages: list[Path]) -> None:
    page = next(p for p in pages if p.parent.name == "school")
    text = page.read_text()
    assert '"crash":[]' in text
    points = '[[0.0002,0.0002,1,1.6,2,"Made-up Ave"],[0.0004,0.0004,2,0.4,0,"Made-up St"]]'
    page.with_name("crash.html").write_text(text.replace('"crash":[]', f'"crash":{points}', 1))
    assert _result(page.with_name("crash.html"), CRASH) == ['data-ok="ok"']


CRASHES = (
    EXERCISE
    + """
const box = $("showcrashes");
if (box.parentElement.style.display === "none") throw new Error("crashes toggle hidden");
if ($("crashwrap").style.display !== "none") throw new Error("forecast toggle shown with no forecast");
box.checked = true; draw();
if (!map.hasLayer(crashesLayer) || crashesLayer.getLayers().length !== 2)
  throw new Error("crashes layer");
const legend = lg.getContainer().innerHTML;
if (!legend.includes("Recorded bike crashes (2019–2023)")) throw new Error("crashes legend");
const [minor, worst] = crashesLayer.getLayers();
if (worst.options.radius <= minor.options.radius) throw new Error("killed not drawn larger");
worst.openPopup();
const shown = document.querySelector(".leaflet-popup-content").textContent;
if (!shown.includes("2 bike crashes here") || !shown.includes("2023: killed; rider child (under 13)"))
  throw new Error("crashes popup");
box.checked = false; draw();
if (map.hasLayer(crashesLayer)) throw new Error("crashes layer not removed");
"""
)


@pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to open the map in")
def test_the_recorded_crashes_layer_toggles_with_a_legend_and_popups(pages: list[Path]) -> None:
    page = next(p for p in pages if p.parent.name == "school")
    text = page.read_text()
    assert '"crashes":[]' in text
    points = (
        '[[0.0002,0.0002,[[2019,"complaint of pain","adult"]]],'
        '[0.0004,0.0004,[[2023,"killed","child (under 13)"],[2021,"other visible injury","teen (13 to 17)"]]]]'
    )
    page.with_name("crashes.html").write_text(
        text.replace('"crashes":[]', f'"crashes":{points}', 1)
    )
    assert _result(page.with_name("crashes.html"), CRASHES) == ['data-ok="ok"']
