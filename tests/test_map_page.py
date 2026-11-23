"""The map page runs in a real browser: every mode and layer draws without a script error.

Skipped where no Chrome or Chromium is installed.
"""

from __future__ import annotations

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

# Runs in the page once it has drawn: every mode in turn, with every layer on, a
# list row clicked, zoomed in and out, then set for printing and back.
EXERCISE = """
for (const mode of [...$("mode").options].map(o => o.value)) {
  $("mode").value = mode; $("mode").dispatchEvent(new Event("change"));
  for (const box of document.querySelectorAll("#bar input[type=checkbox]")) box.checked = true;
  draw();
  const row = document.querySelector("tr[data-go]"); if (row) row.click();
  map.setZoom(16); map.setZoom(12);
  toPrint(); fromPrint();
  for (const box of document.querySelectorAll("#bar input[type=checkbox]")) box.checked = false;
  draw();
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
    """The demo town's maps: one for each destination and one for all together."""
    out = tmp_path_factory.mktemp("maps")
    cli.build(config.load(DEMO), out)
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
