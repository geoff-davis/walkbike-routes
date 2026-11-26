"""The study area: a US place's boundary, or a boundary file, plus a buffer.

Places are looked up in the Census Bureau's TIGERweb service (2020 vintage, to
match the 2020 block counts), first among incorporated places and then census
designated places.  The working projection is the configured one, or else the
UTM zone containing the area.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from pyproj import CRS
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info

from walkbike_routes.config import Area
from walkbike_routes.download.http import Fetcher

TIGERWEB = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
PLACE_LAYERS = {
    "incorporated place": f"{TIGERWEB}/Places_CouSub_ConCity_SubMCD/MapServer/25",
    "census designated place": f"{TIGERWEB}/Places_CouSub_ConCity_SubMCD/MapServer/26",
}

STATES = {
    "AL": ("01", "Alabama"), "AK": ("02", "Alaska"), "AZ": ("04", "Arizona"),
    "AR": ("05", "Arkansas"), "CA": ("06", "California"), "CO": ("08", "Colorado"),
    "CT": ("09", "Connecticut"), "DE": ("10", "Delaware"),
    "DC": ("11", "District of Columbia"), "FL": ("12", "Florida"),
    "GA": ("13", "Georgia"), "HI": ("15", "Hawaii"), "ID": ("16", "Idaho"),
    "IL": ("17", "Illinois"), "IN": ("18", "Indiana"), "IA": ("19", "Iowa"),
    "KS": ("20", "Kansas"), "KY": ("21", "Kentucky"), "LA": ("22", "Louisiana"),
    "ME": ("23", "Maine"), "MD": ("24", "Maryland"), "MA": ("25", "Massachusetts"),
    "MI": ("26", "Michigan"), "MN": ("27", "Minnesota"), "MS": ("28", "Mississippi"),
    "MO": ("29", "Missouri"), "MT": ("30", "Montana"), "NE": ("31", "Nebraska"),
    "NV": ("32", "Nevada"), "NH": ("33", "New Hampshire"), "NJ": ("34", "New Jersey"),
    "NM": ("35", "New Mexico"), "NY": ("36", "New York"),
    "NC": ("37", "North Carolina"), "ND": ("38", "North Dakota"), "OH": ("39", "Ohio"),
    "OK": ("40", "Oklahoma"), "OR": ("41", "Oregon"), "PA": ("42", "Pennsylvania"),
    "RI": ("44", "Rhode Island"), "SC": ("45", "South Carolina"),
    "SD": ("46", "South Dakota"), "TN": ("47", "Tennessee"), "TX": ("48", "Texas"),
    "UT": ("49", "Utah"), "VT": ("50", "Vermont"), "VA": ("51", "Virginia"),
    "WA": ("53", "Washington"), "WV": ("54", "West Virginia"),
    "WI": ("55", "Wisconsin"), "WY": ("56", "Wyoming"), "PR": ("72", "Puerto Rico"),
}  # fmt: skip


class AreaError(ValueError):
    """The study area cannot be found or used."""


@dataclass(frozen=True)
class StudyArea:
    """The boundary, the buffered extent that data is read for, and the projection."""

    name: str
    boundary: gpd.GeoDataFrame  # in ``crs``
    extent: gpd.GeoDataFrame  # boundary plus buffer, in ``crs``
    crs: str
    source: str

    def bbox_4326(self) -> tuple[float, float, float, float]:
        """West, south, east, north of the extent, in degrees."""
        west, south, east, north = self.extent.to_crs(4326).total_bounds
        return float(west), float(south), float(east), float(north)


def state_fips(state: str) -> tuple[str, str]:
    """The FIPS code and name of a state given by name or postal abbreviation."""
    wanted = state.strip().lower()
    for abbreviation, (fips, name) in STATES.items():
        if wanted in {abbreviation.lower(), name.lower()}:
            return fips, name
    raise AreaError(f"{state!r} is not a US state or its abbreviation")


def split_place(place: str) -> tuple[str, str]:
    """``"Boulder, Colorado"`` becomes ``("Boulder", "08")``."""
    name, comma, state = place.rpartition(",")
    if not comma or not name.strip():
        raise AreaError(f"place {place!r} should read 'Name, State'")
    return name.strip(), state_fips(state)[0]


def find_place(place: str, fetcher: Fetcher) -> tuple[gpd.GeoDataFrame, str]:
    """The boundary of a named place, in degrees, and which kind of place it is."""
    name, fips = split_place(place)
    quoted = name.replace("'", "''")
    for kind, layer in PLACE_LAYERS.items():
        answer = fetcher.json(
            f"{layer}/query",
            {
                "where": f"BASENAME='{quoted}' AND STATE='{fips}'",
                "outFields": "NAME,BASENAME,STATE,PLACE,GEOID",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
            },
            name="place",
            check=_place_problem,
        )
        features = answer.get("features", []) if isinstance(answer, dict) else []
        if len(features) > 1:
            names = sorted(str(f["properties"]["NAME"]) for f in features)
            raise AreaError(f"{place!r} matches several {kind}s: {', '.join(names)}")
        if features:
            frame = gpd.GeoDataFrame.from_features(features, crs=4326)
            return frame, kind
    raise AreaError(f"no Census place named {name!r} in that state")


def utm_crs(frame: gpd.GeoDataFrame) -> str:
    """The UTM zone (NAD83) containing the middle of an area."""
    west, south, east, north = frame.to_crs(4326).total_bounds
    middle_x, middle_y = (west + east) / 2, (south + north) / 2
    zones = query_utm_crs_info(
        datum_name="NAD83",
        area_of_interest=AreaOfInterest(middle_x, middle_y, middle_x, middle_y),
    )
    if not zones:
        raise AreaError("no UTM zone covers this area")
    return f"{zones[0].auth_name}:{zones[0].code}"


def resolve(
    area: Area, fetcher: Fetcher, crs: str | None = None, shapes: gpd.GeoDataFrame | None = None
) -> StudyArea:
    """The study area for a config's ``area`` section; ``shapes`` are the
    destinations' own areas, for ``source: destinations``."""
    if area.source == "destinations":
        if shapes is None or shapes.empty:
            raise AreaError("source 'destinations' needs the destinations' areas")
        boundary, name, source = shapes, "the destinations' own areas", "the destinations' areas"
    elif area.source == "place":
        assert area.place is not None  # the config requires it
        boundary, kind = find_place(area.place, fetcher)
        name, source = area.place, f"Census TIGERweb 2020 {kind}"
    elif area.source == "boundary_file":
        assert area.boundary_file is not None
        boundary = _read_boundary(area.boundary_file)
        name, source = area.boundary_file.stem, f"boundary file {area.boundary_file.name}"
    else:
        raise AreaError("the demo town has no real area to look up")
    target = crs or utm_crs(boundary)
    if not CRS.from_user_input(target).is_projected:
        raise AreaError(f"{target} is not a projected coordinate system")
    projected = boundary.to_crs(target)
    outline = gpd.GeoDataFrame(geometry=[projected.union_all()], crs=target)
    extent = gpd.GeoDataFrame(geometry=outline.buffer(area.buffer_m), crs=target)
    return StudyArea(name=name, boundary=outline, extent=extent, crs=target, source=source)


def _read_boundary(path: Path) -> gpd.GeoDataFrame:
    if not path.exists():
        raise AreaError(f"boundary file {path} does not exist")
    frame = gpd.read_file(path)
    if frame.empty or frame.crs is None:
        raise AreaError(f"boundary file {path} is empty or has no coordinate system")
    if not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise AreaError(f"boundary file {path} must hold polygons")
    return frame


def _place_problem(answer: object) -> str | None:
    if isinstance(answer, dict) and "error" in answer:
        return f"TIGERweb refused the place query: {answer['error']}"
    if not isinstance(answer, dict) or not isinstance(answer.get("features"), list):
        return "TIGERweb sent no features"
    return None
