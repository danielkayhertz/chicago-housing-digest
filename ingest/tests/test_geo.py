import json

from digest.cache import JsonCache
from digest.geo import Geo
from digest.http import FetchError
from fakes import FakeFetcher


def square(x0, y0, props):
    ring = [[x0, y0], [x0 + 1, y0], [x0 + 1, y0 + 1], [x0, y0 + 1], [x0, y0]]
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def make_geo(tmp_path, routes):
    w = tmp_path / "w.geojson"
    a = tmp_path / "a.geojson"
    w.write_text(json.dumps({"type": "FeatureCollection", "features": [square(0, 0, {"ward": "27"}), square(1, 0, {"ward": "44"})]}))
    a.write_text(json.dumps({"type": "FeatureCollection", "features": [square(0, 0, {"community": "NEAR WEST SIDE"})]}))
    f = FakeFetcher(routes)
    return Geo(w, a, f, JsonCache(tmp_path / "geocode.json")), f


CENSUS_HIT = json.dumps({"result": {"addressMatches": [{"coordinates": {"x": 0.5, "y": 0.5}}]}}).encode()
CENSUS_MISS = json.dumps({"result": {"addressMatches": []}}).encode()
NOMINATIM_HIT = json.dumps([{"lat": "0.5", "lon": "1.5"}]).encode()


def test_census_hit_assigns_ward_and_area(tmp_path):
    geo, _ = make_geo(tmp_path, [("geocoding.geo.census.gov", CENSUS_HIT)])
    g = geo.locate("1133 W. Kinzie St.")
    assert (g.address, g.ward, g.community_area) == ("1133 W. Kinzie St.", 27, "Near West Side")


def test_census_miss_falls_back_to_nominatim_and_caches(tmp_path):
    geo, f = make_geo(tmp_path, [("geocoding.geo.census.gov", CENSUS_MISS), ("nominatim.openstreetmap.org", NOMINATIM_HIT)])
    assert geo.locate("3700-3846 N. Seminary Ave.").ward == 44
    assert any("3700 N. Seminary Ave., Chicago, IL" in c for c in f.calls)  # address range reduced to its first number
    n = len(f.calls)
    geo.locate("3700-3846 N. Seminary Ave.")
    assert len(f.calls) == n


def test_no_geocode_returns_address_only(tmp_path):
    geo, _ = make_geo(tmp_path, [("geocoding.geo.census.gov", CENSUS_MISS), ("nominatim.openstreetmap.org", b"[]")])
    g = geo.locate("1 Nowhere Pl.")
    assert (g.address, g.ward, g.community_area) == ("1 Nowhere Pl.", None, None)


def test_both_providers_error_returns_none_and_is_not_cached(tmp_path):
    geo, f = make_geo(tmp_path, [
        ("geocoding.geo.census.gov", FetchError("census", 503)),
        ("nominatim.openstreetmap.org", FetchError("nominatim", 429)),
    ])
    assert geo.geocode("1133 W. Kinzie St.") is None
    assert geo.cache.get("1133 W. Kinzie St., Chicago, IL") is None  # transient error must not become a permanent miss
    n = len(f.calls)
    assert geo.geocode("1133 W. Kinzie St.") is None
    assert len(f.calls) == n + 2  # second call re-fetches from both providers


def test_malformed_nominatim_body_with_census_miss_is_not_cached(tmp_path):
    geo, f = make_geo(tmp_path, [
        ("geocoding.geo.census.gov", CENSUS_MISS),
        ("nominatim.openstreetmap.org", json.dumps({"error": "x"}).encode()),
    ])
    assert geo.geocode("1133 W. Kinzie St.") is None  # no KeyError/IndexError escapes
    assert geo.cache.get("1133 W. Kinzie St., Chicago, IL") is None
    n = len(f.calls)
    geo.geocode("1133 W. Kinzie St.")
    assert len(f.calls) == n + 2


def test_census_error_then_nominatim_hit_is_cached(tmp_path):
    geo, f = make_geo(tmp_path, [
        ("geocoding.geo.census.gov", FetchError("census", 503)),
        ("nominatim.openstreetmap.org", NOMINATIM_HIT),
    ])
    assert geo.geocode("1133 W. Kinzie St.") == (1.5, 0.5)
    assert geo.cache.get("1133 W. Kinzie St., Chicago, IL") == [1.5, 0.5]


def test_non_json_provider_body_returns_none_and_is_not_cached(tmp_path):
    geo, f = make_geo(tmp_path, [
        ("geocoding.geo.census.gov", b"<html>Service Unavailable</html>"),
        ("nominatim.openstreetmap.org", b"[]"),
    ])
    assert geo.geocode("1133 W. Kinzie St.") is None  # JSON decode failure is a provider error, not an exception
    assert geo.cache.get("1133 W. Kinzie St., Chicago, IL") is None
    n = len(f.calls)
    geo.geocode("1133 W. Kinzie St.")
    assert len(f.calls) == n + 2


def test_community_area_names_use_official_spelling():
    from digest.geo import area_name
    assert area_name("MCKINLEY PARK") == "McKinley Park"
    assert area_name("OHARE") == "O'Hare"
    assert area_name("NEAR WEST SIDE") == "Near West Side"
