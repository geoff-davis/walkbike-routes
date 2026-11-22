"""Readers for public US data: OpenStreetMap, the Census Bureau and USGS.

Each reader caches what it fetches under a folder the caller chooses (usually
``data/raw/<study>``), so a second run reads from disk and works offline.
"""
