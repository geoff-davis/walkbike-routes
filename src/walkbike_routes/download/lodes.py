"""Jobs by workplace block from the Census Bureau's LEHD LODES 8.

The workplace area characteristics file (WAC) counts the jobs located in
each 2020 Census block, one file per state and year; ``C000`` is all jobs.
LODES 8 is keyed to 2020 blocks, so it joins directly to the TIGERweb blocks
in :mod:`walkbike_routes.download.census`.  It needs no key.

LODES counts jobs covered by unemployment insurance and federal employment,
with noise added to protect confidentiality; the self-employed are not in it.
See https://lehd.ces.census.gov/data/ (LODES Technical Documentation 8).
"""

from __future__ import annotations

import pandas as pd

from walkbike_routes.download.http import DownloadError, Fetcher

BASE = "https://lehd.ces.census.gov/data/lodes/LODES8"
YEAR = 2023
CREDIT = "Jobs: US Census Bureau, LEHD Origin-Destination Employment Statistics (LODES 8)"
# State FIPS code to the postal abbreviation LODES uses in its paths.
STATES = {
    "01": "al", "02": "ak", "04": "az", "05": "ar", "06": "ca", "08": "co", "09": "ct",
    "10": "de", "11": "dc", "12": "fl", "13": "ga", "15": "hi", "16": "id", "17": "il",
    "18": "in", "19": "ia", "20": "ks", "21": "ky", "22": "la", "23": "me", "24": "md",
    "25": "ma", "26": "mi", "27": "mn", "28": "ms", "29": "mo", "30": "mt", "31": "ne",
    "32": "nv", "33": "nh", "34": "nj", "35": "nm", "36": "ny", "37": "nc", "38": "nd",
    "39": "oh", "40": "ok", "41": "or", "42": "pa", "44": "ri", "45": "sc", "46": "sd",
    "47": "tn", "48": "tx", "49": "ut", "50": "vt", "51": "va", "53": "wa", "54": "wv",
    "55": "wi", "56": "wy",
}  # fmt: skip


def workplace_jobs(states: list[str], fetcher: Fetcher, year: int = YEAR) -> pd.DataFrame:
    """All jobs (``C000``) by 2020 block for the states given by FIPS code.

    Returns ``block_id`` (15-digit GEOID text) and ``jobs``.
    """
    frames = []
    for state in sorted(set(states)):
        if state not in STATES:
            raise DownloadError(f"LODES has no state with FIPS code {state!r}")
        abbr = STATES[state]
        name = f"{abbr}_wac_S000_JT00_{year}.csv.gz"
        path = fetcher.download(f"{BASE}/{abbr}/wac/{name}", f"lodes-{name}")
        try:
            frame = pd.read_csv(path, usecols=["w_geocode", "C000"], dtype={"w_geocode": str})
        except (ValueError, OSError, EOFError) as error:
            path.unlink(missing_ok=True)  # so the next run downloads it again
            raise DownloadError(f"{path.name}: not a LODES workplace file ({error})") from error
        frames.append(frame)
    if not frames:
        return pd.DataFrame({"block_id": pd.Series(dtype=str), "jobs": pd.Series(dtype=int)})
    jobs = pd.concat(frames, ignore_index=True)
    jobs = jobs.rename(columns={"w_geocode": "block_id", "C000": "jobs"})
    jobs["block_id"] = jobs.block_id.str.zfill(15)
    jobs["jobs"] = pd.to_numeric(jobs.jobs, errors="raise").astype(int)
    return jobs.groupby("block_id").jobs.sum().reset_index()
