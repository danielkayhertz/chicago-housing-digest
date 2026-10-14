import datetime as dt

from digest.models import CHI
from digest.sources.dpd import DpdAdapter, parse_hearing, parse_schedule
from fakes import FIX, FakeFetcher

BASE = "https://www.chicago.gov/content/city/en/depts/dcd/x.html"


def read(name):
    return (FIX / "dpd" / name).read_text(encoding="utf-8")


def entries_2026(name):
    return {e.date: e for e in parse_schedule(read(name), BASE) if e.date.year == 2026}


def test_parse_schedule_cpc():
    es = entries_2026("cpc_listing.html")
    assert len(es) == 12
    assert es[dt.date(2026, 10, 15)].agenda_url.endswith("October_2026_Plan_Commission_Hearing.html")
    assert es[dt.date(2026, 11, 19)].agenda_url is None


def test_parse_schedule_cdc_cancellations():
    es = entries_2026("cdc_listing.html")
    assert len(es) == 12
    assert {d for d, e in es.items() if e.cancelled} == {dt.date(2026, 3, 10), dt.date(2026, 9, 8)}
    assert es[dt.date(2026, 10, 13)].agenda_url.endswith("/agendas/2026/october.html")


def test_parse_schedule_handles_nbsp_and_abbreviations():
    html = """<h2>2027 Meeting Schedule</h2><table><tr>
      <td><a href="/a/jan.html">Jan.\xa012</a><br>Sept.&nbsp;14<br>Canceled</td>
      <td>October 12<p><a href="/a/oct.html">Agenda</a></p></td></tr></table>"""
    es = {e.date: e for e in parse_schedule(html, BASE)}
    assert es[dt.date(2027, 1, 12)].agenda_url == "https://www.chicago.gov/a/jan.html"
    assert es[dt.date(2027, 9, 14)].cancelled is True
    assert es[dt.date(2027, 10, 12)].agenda_url is None  # the Agenda link is in a separate <p> segment


def test_parse_hearing_cpc():
    t, rows = parse_hearing(read("cpc_2026_10.html"), BASE)
    assert t is None
    assert [r.address for r in rows] == ["1133 W. Kinzie St.", "3700-3846 N. Seminary Ave.", ""]
    assert [r.ward for r in rows] == [27, 44, 10]
    assert [r.community_area for r in rows] == ["Near West Side", "Lake View", None]
    assert [a.label for a in rows[0].attachments] == ["PD Application", "Presentation"]
    assert rows[2].attachments[0].label == "South Chicago Land Use Framework Plan (Informational webpage)"
    assert rows[0].description.startswith("A proposed Planned Development, submitted by MKR Owner LLC")


def test_parse_hearing_cdc_time_and_rows():
    t, rows = parse_hearing(read("cdc_2026_10.html"), BASE)
    assert t == dt.time(13, 30)
    assert [(r.address, r.ward) for r in rows] == [("1207 W. Leland Ave.", 46), ("758 E. 111th St.", 9)]
    assert rows[0].attachments[0].label == "Staff Report"


def test_adapter_cdc_window_and_items():
    cfg = {"id": "cdc", "body": "Community Development Commission", "default_time": "13:30",
           "listing_url": "https://www.chicago.gov/content/city/en/depts/dcd/community-development-commission/meetings--agendas-and-video-archives.html"}
    f = FakeFetcher([
        ("agendas/2026/october.html", "dpd/cdc_2026_10.html"),
        ("meetings--agendas-and-video-archives.html", "dpd/cdc_listing.html"),
    ])
    res = DpdAdapter(cfg).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    assert [m.id for m in res.meetings] == ["cdc:2026-10-13", "cdc:2026-11-10"]
    oct13, nov10 = res.meetings
    assert oct13.start.isoformat() == "2026-10-13T13:30:00-05:00" and oct13.time_known
    assert nov10.time_known is False and nov10.agenda_url is None
    assert [i.id for i in res.items] == ["cdc:2026-10-13#1207-w-leland-ave", "cdc:2026-10-13#758-e-111th-st"]
    assert res.items[0].geography_hint.ward == 46


def test_cpc_item_without_address_uses_position():
    cfg = {"id": "cpc", "body": "Chicago Plan Commission", "default_time": "10:00",
           "listing_url": "https://www.chicago.gov/content/city/en/depts/dcd/chicago-plan-commission/meetings--agendas---video-archives.html"}
    f = FakeFetcher([
        ("October_2026_Plan_Commission_Hearing", "dpd/cpc_2026_10.html"),
        ("meetings--agendas---video-archives", "dpd/cpc_listing.html"),
    ])
    res = DpdAdapter(cfg).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    ids = [i.id for i in res.items]
    assert ids == ["cpc:2026-10-15#1133-w-kinzie-st", "cpc:2026-10-15#3700-3846-n-seminary-ave", "cpc:2026-10-15#item-3"]
    assert res.meetings[0].time_known is False  # the CPC page states no time
