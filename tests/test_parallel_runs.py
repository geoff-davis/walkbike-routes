"""Destinations routed in parallel give the same outputs as one after another."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from walkbike_routes import __main__ as cli
from walkbike_routes import config

DEMO = Path(__file__).resolve().parents[1] / "config" / "demo.yaml"


def _study() -> config.Study:
    """The demo town, with a shorter limit: the first destination is routed in this
    process, the second in a worker."""
    return dataclasses.replace(config.load(DEMO), shorter_limits_min=(5.0,))


def test_parallel_workers_write_the_same_outputs(tmp_path: Path) -> None:
    alone, together = tmp_path / "alone", tmp_path / "together"
    cli.build(_study(), alone)
    cli.build(_study(), together, workers=2)
    found = sorted(p.relative_to(alone) for p in alone.rglob("*.parquet"))
    assert found == sorted(p.relative_to(together) for p in together.rglob("*.parquet"))
    assert {p.parts[0] for p in found} >= {"school", "downtown", "all-destinations"}
    for path in found:
        pd.testing.assert_frame_equal(
            pd.read_parquet(alone / path), pd.read_parquet(together / path), obj=str(path)
        )
    pd.testing.assert_frame_equal(
        pd.read_csv(alone / "summary.csv"), pd.read_csv(together / "summary.csv")
    )


def test_jobs_below_one_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["run", "--config", str(DEMO), "--out", str(tmp_path), "--jobs", "0"])
    assert "--jobs: expected 1 or more" in capsys.readouterr().err
