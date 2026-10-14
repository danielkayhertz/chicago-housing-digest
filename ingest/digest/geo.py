"""Address → (ward, community area). Census geocoder first, Nominatim fallback, point-in-polygon on city boundaries."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.prepared import prep

from .cache import JsonCache
from .http import FetchError, Fetcher
from .models import Geography

log = logging.getLogger(__name__)

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

# A provider body that parses as JSON but has the wrong shape is an error, not a no-match.
_MALFORMED = (KeyError, IndexError, TypeError, ValueError, AttributeError)


# Official community-area names that str.title() gets wrong (checked against all 77 in community_areas.geojson).
AREA_NAMES = {"MCKINLEY PARK": "McKinley Park", "OHARE": "O'Hare"}


def area_name(raw: str) -> str:
    return AREA_NAMES.get(raw.strip().upper(), raw.title())


def _load(path: Path, prop: str) -> list[tuple[object, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(prep(shape(f["geometry"])), str(f["properties"][prop])) for f in data["features"] if f.get("geometry")]


def _which(polys: list[tuple[object, str]], pt: Point) -> str | None:
    return next((value for poly, value in polys if poly.contains(pt)), None)


_HAS_CITY = re.compile(r",\s*(IL|Illinois)\b|\b\d{5}(-\d{4})?\s*$", re.IGNORECASE)


def names_city(address: str) -> bool:
    """True when the address already carries a state or ZIP, so the geocoder must not be told it is in Chicago."""
    return bool(_HAS_CITY.search(address))


def normalize_address(address: str) -> str:
    a = address.strip().rstrip(",").strip()
    if names_city(a):
        return a
    a = re.sub(r"^(\d+)\s+(?:and|&)\s+\d+\b", r"\1", a)  # "4125 and 4127 W 5th Ave" → "4125 W 5th Ave"
    a = re.split(r",\s*(?=\d)|\s+(?:and|&)\s+(?=\d)|\s*/\s*", a)[0].strip()  # first of several addresses
    return re.sub(r"^(\d+)-\d+", r"\1", a)  # "3700-3846 N. Seminary" → "3700 N. Seminary"


class Geo:
    def __init__(self, wards_path: Path, areas_path: Path, fetcher: Fetcher, cache: JsonCache | None = None):
        self.wards = _load(wards_path, "ward")
        self.areas = _load(areas_path, "community")
        self.fetcher = fetcher
        self.cache = cache

    def locate(self, address: str) -> Geography:
        pt = self.geocode(address)
        if pt is None:
            return Geography(address=address)
        p = Point(pt)
        ward = _which(self.wards, p)
        area = _which(self.areas, p)
        return Geography(address=address, ward=int(ward) if ward else None,
                         community_area=area_name(area) if area else None)

    def geocode(self, address: str) -> tuple[float, float] | None:
        a = normalize_address(address)
        q = a if names_city(a) else f"{a}, Chicago, IL"
        if self.cache is not None and (hit := self.cache.get(q)) is not None:
            return tuple(hit) if hit else None
        errored = False
        pt, ok = self._census(q)
        errored |= not ok
        if pt is None:
            pt, ok = self._nominatim(q)
            errored |= not ok
        if pt is None and errored:
            # Transient failure: return nothing and leave the cache alone so the next run retries.
            return None
        if self.cache is not None:
            self.cache.set(q, list(pt) if pt else [])  # hit, or a clean no-match from both providers
        return pt

    def _census(self, q: str) -> tuple[tuple[float, float] | None, bool]:
        """Returns (point, ok). ok=False means the provider errored; (None, True) is a clean no-match."""
        try:
            data = self.fetcher.get_json(CENSUS, {"address": q, "benchmark": "Public_AR_Current", "format": "json"})
        except FetchError as e:
            log.warning("census geocoder failed for %r: %s", q, e)
            return None, False
        try:
            matches = data["result"]["addressMatches"]
            if matches == []:
                return None, True
            c = matches[0]["coordinates"]
            return (float(c["x"]), float(c["y"])), True
        except _MALFORMED as e:
            log.warning("census geocoder returned a malformed body for %r: %s", q, e)
            return None, False

    def _nominatim(self, q: str) -> tuple[tuple[float, float] | None, bool]:
        """Returns (point, ok). ok=False means the provider errored; (None, True) is a clean no-match."""
        try:
            data = self.fetcher.get_json(NOMINATIM, {"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "us"})
        except FetchError as e:
            log.warning("nominatim failed for %r: %s", q, e)
            return None, False
        try:
            if data == []:
                return None, True
            return (float(data[0]["lon"]), float(data[0]["lat"])), True
        except _MALFORMED as e:
            log.warning("nominatim returned a malformed body for %r: %s", q, e)
            return None, False
