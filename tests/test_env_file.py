from __future__ import annotations

import os
from pathlib import Path

import pytest

from walkbike_routes import config as settings
from walkbike_routes.__main__ import load_env_file


def test_an_env_file_sets_what_the_environment_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("WB_PLAIN", "WB_QUOTED", "WB_SINGLE", "WB_EXPORTED", "WB_SET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WB_SET", "from the shell")
    env = tmp_path / ".env"
    env.write_text(
        '# a comment\n\nWB_PLAIN=abc123\nWB_QUOTED="two words"\n'
        "WB_SINGLE='x=y'\nexport WB_EXPORTED = spaced \nWB_SET=from the file\n"
    )
    assert load_env_file(env) == ["WB_PLAIN", "WB_QUOTED", "WB_SINGLE", "WB_EXPORTED"]
    assert os.environ["WB_PLAIN"] == "abc123"
    assert os.environ["WB_QUOTED"] == "two words"
    assert os.environ["WB_SINGLE"] == "x=y"
    assert os.environ["WB_EXPORTED"] == "spaced"
    assert os.environ["WB_SET"] == "from the shell"  # the environment wins
    for name in ("WB_PLAIN", "WB_QUOTED", "WB_SINGLE", "WB_EXPORTED"):
        monkeypatch.delenv(name)


def test_no_env_file_is_fine_and_a_bad_line_hides_its_value(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / ".env") == []
    bad = tmp_path / ".env"
    bad.write_text("GOOD=1\nsecret-value-without-a-name\n")
    with pytest.raises(settings.ConfigError, match="line 2") as raised:
        load_env_file(bad)
    assert "secret-value" not in str(raised.value)
    os.environ.pop("GOOD", None)
