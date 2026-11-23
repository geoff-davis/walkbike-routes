"""A shorter time limit, worked out from a run's routes, matches a run made at that limit."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from walkbike_routes import __main__ as cli
from walkbike_routes import config

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"
SHORT = 5.0
RUNS = ("walk-broach-2016", "bike-broach-2011")
TABLES = ("route_shares", "flows", "exposure_streets", "exposure_crossings")


@pytest.fixture(scope="module")
def both(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The demo run at its own limits with a shorter one, and the demo run at that limit."""
    study = config.load(DEMO)
    with_short = tmp_path_factory.mktemp("with_short")
    cli.build(dataclasses.replace(study, shorter_limits_min=(SHORT,)), with_short)
    at_short = tmp_path_factory.mktemp("at_short")
    caps = dict.fromkeys(study.time_caps_min, SHORT)
    cli.build(dataclasses.replace(study, time_caps_min=caps, time_bands_min=(SHORT,)), at_short)
    return with_short, at_short


def _table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path).drop(columns="geometry", errors="ignore")


@pytest.mark.parametrize("destination", ["school", "downtown"])
@pytest.mark.parametrize("run", RUNS)
def test_the_shorter_limit_matches_a_run_at_that_limit(
    both: tuple[Path, Path], destination: str, run: str
) -> None:
    with_short, at_short = both
    cut = with_short / destination / run / "limit-5"
    for name in TABLES:
        pd.testing.assert_frame_equal(
            _table(cut / f"{name}.parquet"),
            _table(at_short / destination / run / f"{name}.parquet"),
        )
    made = pd.read_csv(cut / "summary.csv").iloc[0]
    mode, preset = run.split("-", 1)
    summary = pd.read_csv(at_short / "summary.csv", dtype={"destination_id": str})
    real = summary[summary.destination_id.eq(destination) & summary["mode"].eq(mode)
                   & summary.preset.eq(preset)].iloc[0]  # fmt: skip
    for column in ("within_cap_weight", "median_minutes_within_cap", "time_cap_min",
                   "expected_weight", "mean_routes_per_origin"):  # fmt: skip
        assert made[column] == pytest.approx(real[column]), column


@pytest.mark.parametrize("destination", ["school", "downtown"])
def test_the_car_overlap_matches_at_the_shorter_limit(
    both: tuple[Path, Path], destination: str
) -> None:
    with_short, at_short = both
    pd.testing.assert_frame_equal(
        _table(with_short / destination / "limit-5" / "overlap.parquet"),
        _table(at_short / destination / "overlap.parquet"),
    )


@pytest.mark.parametrize("run", RUNS)
def test_the_lists_for_every_destination_match_at_the_shorter_limit(
    both: tuple[Path, Path], run: str
) -> None:
    with_short, at_short = both
    for name in ("exposure_streets", "exposure_crossings"):
        pd.testing.assert_frame_equal(
            _table(with_short / "all-destinations" / run / "limit-5" / f"{name}.parquet"),
            _table(at_short / "all-destinations" / run / f"{name}.parquet"),
        )


def test_the_full_limit_is_unchanged_by_a_shorter_one(
    both: tuple[Path, Path], tmp_path: Path
) -> None:
    with_short, _ = both
    plain = tmp_path / "plain"
    cli.build(config.load(DEMO), plain)
    for run in RUNS:
        for name in TABLES:
            pd.testing.assert_frame_equal(
                _table(with_short / "school" / run / f"{name}.parquet"),
                _table(plain / "school" / run / f"{name}.parquet"),
            )
