"""Address → (ward, community area). Census geocoder first, Nominatim fallback, point-in-polygon on city boundaries."""
from __future__ import annotations

import json
import re
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.prepared import prep

from .cache import JsonCache
from .http import FetchError, Fetcher
from .models import Geography

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _load(path: Path, prop: str) -> list[tuple[object, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(prep(shape(f["geometry"])), str(f["properties"][prop])) for f in data["features"] if f.get("geometry")]


def _which(polys: list[tuple[object, str]], pt: Point) -> str | None:
    return next((value for poly, value in polys if poly.contains(pt)), None)


def normalize_address(address: str) -> str:
    a = address.strip().rstrip(",").strip()
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
                         community_area=area.title() if area else None)

    def geocode(self, address: str) -> tuple[float, float] | None:
        q = f"{normalize_address(address)}, Chicago, IL"
        if self.cache is not None and (hit := self.cache.get(q)) is not None:
            return tuple(hit) if hit else None
        pt = self._census(q) or self._nominatim(q)
        if self.cache is not None:
            self.cache.set(q, list(pt) if pt else [])
        return pt

    def _census(self, q: str) -> tuple[float, float] | None:
        try:
            data = self.fetcher.get_json(CENSUS, {"address": q, "benchmark": "Public_AR_Current", "format": "json"})
        except FetchError:
            return None
        matches = data.get("result", {}).get("addressMatches") or []
        return (matches[0]["coordinates"]["x"], matches[0]["coordinates"]["y"]) if matches else None

    def _nominatim(self, q: str) -> tuple[float, float] | None:
        try:
            data = self.fetcher.get_json(NOMINATIM, {"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "us"})
        except FetchError:
            return None
        return (float(data[0]["lon"]), float(data[0]["lat"])) if data else None
