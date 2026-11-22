"""Checks on single values read from a study's YAML files, shared by the config and the corrections.

Each takes the raw value and where it was written, and returns it checked or
raises :class:`ConfigError` naming the place.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any, cast

LonLat = tuple[float, float]


class ConfigError(ValueError):
    """A study config that cannot be used, with the reason."""


def keys(
    raw: Mapping[str, Any], kind: type, where: str, skip: frozenset[str] | set[str] = frozenset()
) -> dict[str, Any]:
    """The settings, after checking none is unknown and every required one is given."""
    known = {f.name for f in fields(kind)} - set(skip)
    required = {
        f.name
        for f in fields(kind)
        if f.name in known and f.default is MISSING and f.default_factory is MISSING
    }
    unknown = sorted(set(raw) - known, key=str)  # a YAML key may be a number or a date
    if unknown:
        raise ConfigError(f"{where}: unknown setting {unknown[0]!r}")
    missing = sorted(required - set(raw))
    if missing:
        raise ConfigError(f"{where}: missing setting {missing[0]!r}")
    return dict(raw)


def mapping(raw: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a mapping")
    return raw


def listed(top: Mapping[str, Any], key: str, default: list[Any] | None = None) -> list[Any]:
    value = top[key] if default is None else top.get(key, default)
    if not isinstance(value, list):
        raise ConfigError(f"{key}: expected a list")
    return value


def text(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{where}: expected text")
    return raw


def names(raw: Any, where: str) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{where}: name at least one preset")
    names = tuple(text(v, where) for v in raw)
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ConfigError(f"{where}: {twice[0]!r} is listed twice")
    return names


def choice(raw: Any, where: str, allowed: tuple[str, ...]) -> str:
    if raw not in allowed:
        raise ConfigError(f"{where}: {raw!r} is not one of {', '.join(allowed)}")
    return cast(str, raw)


def number(raw: Any, where: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ConfigError(f"{where}: expected a number")
    return float(raw)


def positive(raw: Any, where: str) -> float:
    try:
        value = number(raw, where)
    except ConfigError:
        value = 0.0
    if value <= 0:
        raise ConfigError(f"{where}: expected a positive number")
    return value


def age(raw: Any, where: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 120:
        raise ConfigError(f"{where}: expected a whole number of years")
    return raw


def flag(raw: Any, where: str) -> bool:
    if not isinstance(raw, bool):
        raise ConfigError(f"{where}: expected true or false")
    return raw


def count(raw: Any, where: str) -> int:
    found = number(raw, where)
    if found < 0 or found != int(found):
        raise ConfigError(f"{where}: expected a whole number, 0 or more")
    return int(found)


def lonlat(raw: Any, where: str) -> LonLat:
    if (
        not isinstance(raw, list | tuple)
        or len(raw) != 2
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in raw)
    ):
        raise ConfigError(f"{where}: expected [longitude, latitude]")
    lon, lat = float(raw[0]), float(raw[1])
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ConfigError(f"{where}: [{lon}, {lat}] is not a longitude and latitude")
    return lon, lat


def path(raw: Any, base: Path) -> Path | None:
    if raw is None:
        return None
    return (base / text(raw, "file name")).resolve()
