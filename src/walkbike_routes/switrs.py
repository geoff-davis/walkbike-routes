"""California police crash records (SWITRS, as TIMS and CCRS exports lay them out).

An optional adapter: the core never needs it.  It reads a crashes table and,
when given, its parties table, and keeps the bike crashes with their year,
severity and the youngest rider's age group, for a map layer.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from walkbike_routes.config_values import ConfigError

SEVERITY = {
    "1": "killed",
    "2": "severe injury",
    "3": "other visible injury",
    "4": "complaint of pain",
    "0": "property damage only",
}
# The worst are drawn last, so on top.
_ORDER = {
    "property damage only": 0,
    "complaint of pain": 1,
    "other visible injury": 2,
    "severe injury": 3,
    "killed": 4,
}
BICYCLIST = "4"  # PARTY_TYPE
AGE_NOT_STATED = 998
NO_AGE = "age not recorded"
COLUMNS = ["year", "severity", "rider", "geometry"]


def bike_crashes(crashes: Path, parties: Path | None = None) -> gpd.GeoDataFrame:
    """The bike crashes in ``crashes``, with points, worst drawn last."""
    read = _points(crashes)
    needed = ["CASE_ID", "ACCIDENT_YEAR", "COLLISION_SEVERITY", "BICYCLE_ACCIDENT"]
    missing = [c for c in needed if c not in read.columns]
    if missing:
        raise ConfigError(f"recorded_crashes: {crashes.name} has no {missing[0]} column")
    read = read[read.BICYCLE_ACCIDENT.astype("string").str.strip().str.upper().eq("Y")]
    kinds = {shape.geom_type for shape in read.geometry if shape is not None}
    if kinds - {"Point"}:
        raise ConfigError(
            f"recorded_crashes: {crashes.name} holds {sorted(kinds - {'Point'})[0]} shapes; "
            "each crash must be a point"
        )
    placed = [shape is not None and not shape.is_empty for shape in read.geometry]
    read = read[pd.Series(placed, index=read.index, dtype=bool)].copy()
    read["year"] = pd.to_numeric(read.ACCIDENT_YEAR, errors="coerce").astype("Int64")
    read["severity"] = _code(read.COLLISION_SEVERITY).map(SEVERITY).fillna("severity not recorded")
    ages = _rider_ages(parties) if parties is not None else pd.Series(dtype="float64")
    read["rider"] = [_age_group(ages.get(case)) or NO_AGE for case in _code(read.CASE_ID)]
    rank = read.severity.map(_ORDER).fillna(-1)
    read = read.assign(_rank=rank).sort_values(["_rank", "year"], kind="stable")
    return gpd.GeoDataFrame(read[COLUMNS], geometry="geometry", crs=read.crs)


def _points(path: Path) -> gpd.GeoDataFrame:
    """Read crash rows with their points: a spatial file, or a table with POINT_X/POINT_Y."""
    if path.suffix.lower() == ".parquet":
        try:
            read = gpd.read_parquet(path)
        except ValueError:  # a plain table without spatial metadata
            return _from_columns(pd.read_parquet(path), path)
    elif path.suffix.lower() == ".csv":
        return _from_columns(pd.read_csv(path, dtype=str, low_memory=False), path)
    else:
        read = gpd.read_file(path)
    if read.crs is None:
        raise ConfigError(f"recorded_crashes: {path.name} gives no coordinate system")
    return read


def _from_columns(table: pd.DataFrame, path: Path) -> gpd.GeoDataFrame:
    if "POINT_X" not in table or "POINT_Y" not in table:
        raise ConfigError(f"recorded_crashes: {path.name} has no points (POINT_X and POINT_Y)")
    x = pd.to_numeric(table.POINT_X, errors="coerce")
    y = pd.to_numeric(table.POINT_Y, errors="coerce")
    known = x.notna() & y.notna() & x.ne(0) & y.ne(0)  # TIMS writes 0 for no point
    table = table[known]
    return gpd.GeoDataFrame(table, geometry=gpd.points_from_xy(x[known], y[known]), crs=4326)


def _rider_ages(path: Path) -> pd.Series:
    """The youngest bicyclist's age in each crash, by CASE_ID."""
    table = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else _csv(path)
    missing = [c for c in ("CASE_ID", "PARTY_TYPE", "PARTY_AGE") if c not in table.columns]
    if missing:
        raise ConfigError(f"recorded_crashes: {path.name} has no {missing[0]} column")
    riders = table[_code(table.PARTY_TYPE).eq(BICYCLIST)]
    age = pd.to_numeric(riders.PARTY_AGE, errors="coerce")
    age = age.where(age.ge(0) & age.lt(AGE_NOT_STATED))
    return age.groupby(_code(riders.CASE_ID)).min().dropna()


def _csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, low_memory=False)


def _code(values: pd.Series) -> pd.Series:
    """A code as text, whether it was read as 1, 1.0 or "1"."""
    text = values.astype("string").str.strip()
    return text.str.replace(r"\.0$", "", regex=True)


def _age_group(age: float | None) -> str | None:
    if age is None or pd.isna(age):
        return None
    if age < 13:
        return "child (under 13)"
    if age < 18:
        return "teen (13 to 17)"
    return "adult"
