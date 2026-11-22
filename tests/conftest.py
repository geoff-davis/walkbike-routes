"""Shared test setup."""

from __future__ import annotations

import pytest

from walkbike_routes import __main__ as cli


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's .env (and its keys) out of tests that call the CLI."""
    monkeypatch.setattr(cli, "load_env_file", lambda *args, **kwargs: [])
