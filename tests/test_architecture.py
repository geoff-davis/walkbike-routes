"""The routing core stays destination-neutral and free of drawing libraries."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "walkbike_routes"
CORE = ("network", "routing", "destinations", "demand", "analysis", "costs", "outputs", "choice")
FORBIDDEN = ("matplotlib", "folium", "contextily")
# The core must not reach up into the command, config, demo town or study runner.
TOP_LEVEL = (
    "walkbike_routes.__main__",
    "walkbike_routes.config",
    "walkbike_routes.demo",
    "walkbike_routes.study",
)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_the_core_imports_no_studies_drawing_libraries_or_command_code() -> None:
    files = [p for part in CORE for p in (PACKAGE / part).rglob("*.py")]
    assert len(files) > 20
    bad = sorted(
        f"{path.relative_to(PACKAGE)} imports {name}"
        for path in files
        for name in _imports(path)
        if name.split(".")[0] in FORBIDDEN or name.startswith(TOP_LEVEL)
    )
    assert not bad, bad


def test_no_relative_imports_hide_from_the_check() -> None:
    relative = [
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    assert not relative, relative
