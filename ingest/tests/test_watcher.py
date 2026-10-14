import datetime as dt
import json

from digest.http import NotFound
from digest.models import CHI
from digest.sources.watcher import WatcherAdapter, snapshot
from fakes import FIX, FakeFetcher

URL = "https://www.chicago.gov/content/city/en/depts/doh.html"
PAGE = {"id": "doh-home", "title": "DOH home", "body": "Chicago Department of Housing", "url": URL}
NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)
HTML = (FIX / "watch/doh_home.html").read_text(encoding="utf-8")


def run(tmp_path, html):
    adapter = WatcherAdapter({"id": "watch"}, [PAGE], tmp_path / "snapshots")
    return adapter.fetch(FakeFetcher([("doh.html", html.encode("utf-8"))]), NOW)


def test_snapshot_has_main_text_and_absolute_links():
    snap = snapshot(HTML, URL)
    assert "Housing" in snap["text"]
    assert "https://www.chicago.gov/content/city/en/depts/doh/supp_info/2023-qualified-allocation-plan.html" in snap["links"]
    assert not any(u.startswith(("mailto:", "#")) for u in snap["links"])


def test_first_run_is_baseline(tmp_path):
    res = run(tmp_path, HTML)
    assert res.notices == []
    assert (tmp_path / "snapshots" / "doh-home.json").exists()


def test_new_link_becomes_notice(tmp_path):
    run(tmp_path, HTML)
    changed = HTML.replace("</main>", '<a href="/content/dam/city/depts/doh/new-rfp.pdf">2027 Multifamily RFP</a></main>', 1)
    res = run(tmp_path, changed)
    assert len(res.notices) == 1
    n = res.notices[0]
    assert n.kind == "page_change" and n.title == "New on DOH home: 2027 Multifamily RFP"
    assert n.url == "https://www.chicago.gov/content/dam/city/depts/doh/new-rfp.pdf"
    assert n.id.startswith("watch:doh-home:") and n.new_link
    assert run(tmp_path, changed).notices == []  # no repeat once the snapshot is updated


def test_text_only_change_describes_added_lines(tmp_path):
    run(tmp_path, HTML)
    res = run(tmp_path, HTML.replace("</main>", "<p>Comment period open until November 30</p></main>", 1))
    assert len(res.notices) == 1
    assert res.notices[0].title == "DOH home page updated"
    assert "Comment period open until November 30" in res.notices[0].detail
    assert res.notices[0].url == URL and not res.notices[0].new_link


def test_one_page_failing_is_a_warning(tmp_path):
    pages = [PAGE, {**PAGE, "id": "gone", "url": "https://www.chicago.gov/gone.html"}]
    f = FakeFetcher([("gone.html", NotFound("gone", 404)), ("doh.html", HTML.encode("utf-8"))])
    res = WatcherAdapter({"id": "watch"}, pages, tmp_path / "snapshots").fetch(f, NOW)
    assert any("gone" in w for w in res.warnings)


def test_stub_fetch_after_baseline_is_ignored(tmp_path):
    run(tmp_path, HTML)
    snap = tmp_path / "snapshots" / "doh-home.json"
    before = snap.read_bytes()
    res = run(tmp_path, "<main>Service unavailable</main>")
    assert res.notices == []
    assert any("doh-home" in w and "stub" in w for w in res.warnings)
    assert snap.read_bytes() == before
    changed = HTML.replace("</main>", '<a href="/content/dam/city/depts/doh/new-rfp.pdf">2027 Multifamily RFP</a></main>', 1)
    res = run(tmp_path, changed)
    assert len(res.notices) == 1 and res.notices[0].title == "New on DOH home: 2027 Multifamily RFP"


def test_corrupt_snapshot_is_rebaselined_with_warning(tmp_path):
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "doh-home.json").write_text('{"text": "Chicago', encoding="utf-8")
    res = run(tmp_path, HTML)
    assert res.notices == []
    assert any("doh-home" in w and "unreadable" in w for w in res.warnings)
    assert "links" in json.loads((snap_dir / "doh-home.json").read_text(encoding="utf-8"))
