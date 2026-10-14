import pytest

from digest.http import BOT_UA, BROWSER_UA, HttpFetcher, NotFound, public_url, url_key
from fakes import FakeFetcher


def test_url_key_encodes_params():
    key = url_key("https://api.example.org/meeting", {"filter": "body eq 'X'", "top": 5})
    assert key == "https://api.example.org/meeting?filter=body+eq+%27X%27&top=5"


def test_user_agent_by_host():
    f = HttpFetcher()
    assert f._headers("www.chicago.gov")["User-Agent"] == BROWSER_UA
    assert f._headers("webapi.legistar.com")["User-Agent"] == BOT_UA
    assert "@" not in BOT_UA


def test_fake_fetcher_routes_first_match_and_raises():
    f = FakeFetcher([("recordNumber/O2026-1", NotFound("x", 404)), ("recordNumber/", "common/empty_list.json")])
    with pytest.raises(NotFound):
        f.get_json("https://h/matter/recordNumber/O2026-1")
    assert f.get_json("https://h/matter/recordNumber/SO2026-1") == []
    with pytest.raises(AssertionError, match="no route"):
        f.get_text("https://elsewhere/")


def test_public_url_rewrites_only_chicago_content_city_prefix():
    assert public_url("https://www.chicago.gov/content/city/en/depts/doh.html") == (
        "https://www.chicago.gov/city/en/depts/doh.html"
    )
    assert public_url("https://www.chicago.gov/content/dam/city/depts/doh/new-rfp.pdf") == (
        "https://www.chicago.gov/content/dam/city/depts/doh/new-rfp.pdf"
    )
    assert public_url("https://clihtf.org/content/city/en/x.html") == "https://clihtf.org/content/city/en/x.html"
