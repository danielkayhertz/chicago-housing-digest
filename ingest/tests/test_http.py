import httpx
import pytest

from digest.http import BOT_UA, BROWSER_UA, FetchError, HttpFetcher, NotFound, public_url, url_key
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


def test_get_json_non_json_body_raises_fetch_error():
    f = HttpFetcher(delay=0)
    f.client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"<html>Maintenance</html>")))
    with pytest.raises(FetchError, match="invalid JSON"):
        f.get_json("https://webapi.legistar.com/v1/chicago/bodies")
    with pytest.raises(FetchError, match="invalid JSON"):
        FakeFetcher([("bodies", b"<html>Maintenance</html>")]).get_json("https://h/bodies")


def test_redirect_loops_and_invalid_urls_become_fetch_error():
    def loop(req):
        return httpx.Response(302, headers={"Location": str(req.url)})
    f = HttpFetcher(delay=0)
    f.client = httpx.Client(transport=httpx.MockTransport(loop), follow_redirects=True)
    with pytest.raises(FetchError):
        f.get_bytes("https://webapi.legistar.com/v1/chicago/events")
    with pytest.raises(FetchError):
        f.get_text("http://exa" + chr(0) + "mple.com/")  # NUL in host: httpx.InvalidURL
    with pytest.raises(FetchError):
        f.get_json("https://webapi.legistar.com/v1/chicago/events")
