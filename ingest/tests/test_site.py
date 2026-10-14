import datetime as dt
import json
from pathlib import Path

from digest.categories import Categories
from digest.models import (CHI, CategoryHit, Digest, Enrichment, Geography, Item, Meeting, Notice, Outcome,
                           dump_digest)
from digest.site import build_site, fold, ics_calendar, ics_escape

CATS = Categories(Path(__file__).resolve().parents[2] / "config" / "categories.yaml")
NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)


def sample():
    up = Meeting(id="cpc:2026-10-15", source_id="cpc", body="Chicago Plan Commission", body_id="cpc",
                 start=dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI), location="City Hall, Room 201A",
                 agenda_url="https://x/agenda.pdf")
    gone = Meeting(id="cdc:2026-11-10", source_id="cdc", body="Community Development Commission", body_id="cdc",
                   start=dt.datetime(2026, 11, 10, 0, 0, tzinfo=CHI), time_known=False, status="cancelled")
    past = Meeting(id="elms:Z1", source_id="elms-zoning", body="City Council Committee on Zoning", body_id="z",
                   start=dt.datetime(2026, 9, 30, 10, 0, tzinfo=CHI))
    ok = Enrichment(status="ok", housing_relevant=True, summary="Adds 120 homes near the Kinzie Green Line stop.",
                    categories=[CategoryHit(id="ahpaa", via="body")], geography=Geography(ward=27))
    items = [
        Item(id="cpc:2026-10-15#kinzie", meeting_id=up.id, source_ref="1133 W. Kinzie St.", title="Kinzie Planned Development",
             enrichment=ok),
        Item(id="cpc:2026-10-15#sign", meeting_id=up.id, source_ref="x", title="Billboard on Ogden Ave",
             enrichment=Enrichment(status="screened_out")),
        Item(id="elms:Z1#O2026-1", meeting_id=past.id, source_ref="O2026-1", title="Rezoning at 4400 N. Broadway",
             outcome=Outcome(action="Recommended to Pass", date=dt.date(2026, 9, 30)), enrichment=ok),
    ]
    notice = Notice(id="ihda:notice:abc", source_id="ihda", body="IHDA", kind="hearing", title="TEFRA hearing, Pheasant Ridge",
                    url="https://x/tefra.pdf", deadline=dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI))
    for m, n in ((up, 2), (gone, 0), (past, 1)):
        m.item_count_total = n
    return Digest(generated_at=NOW, meetings=[past, up, gone], items=items, notices=[notice])


def test_index_shows_relevant_hides_rest(tmp_path):
    src = tmp_path / "items.json"
    dump_digest(sample(), src)
    build_site(sample(), tmp_path / "site", src, CATS, NOW)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Kinzie Planned Development" in html and "Billboard on Ogden Ave" not in html
    assert "+1 item not about housing" in html
    assert "AI summary" in html and "Adds 120 homes" in html
    assert "Zoning &amp; housing supply" in html                  # category label, escaped
    assert "TEFRA hearing, Pheasant Ridge" in html
    assert "Recommended to Pass" in html                           # recent outcome
    assert "Cancelled" in html
    assert 'data-categories="ahpaa"' in html and 'data-ward="27"' in html
    assert json.loads((tmp_path / "site" / "items.json").read_text(encoding="utf-8"))["generated_at"]


def test_ics_feeds(tmp_path):
    src = tmp_path / "items.json"
    dump_digest(sample(), src)
    build_site(sample(), tmp_path / "site", src, CATS, NOW)
    cpc = (tmp_path / "site" / "ics" / "chicago-plan-commission.ics").read_bytes().decode("utf-8")
    assert cpc.startswith("BEGIN:VCALENDAR\r\n") and cpc.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;TZID=America/Chicago:20261015T100000\r\n" in cpc
    assert "UID:cpc-2026-10-15@housingdigest.danielkayhertz.com" in cpc
    assert "LOCATION:City Hall\\, Room 201A" in cpc
    cdc = (tmp_path / "site" / "ics" / "community-development-commission.ics").read_bytes().decode("utf-8")
    assert "DTSTART;VALUE=DATE:20261110" in cdc and "STATUS:CANCELLED" in cdc
    assert all(len(line.encode("utf-8")) <= 75 for line in cpc.split("\r\n"))


def test_non_http_urls_never_reach_href_or_ics(tmp_path):
    from digest.models import Attachment
    from digest.site import safe_url
    d = sample()
    bad = " JavaScript:alert(1)"
    up = next(m for m in d.meetings if m.id == "cpc:2026-10-15")
    up.agenda_url, up.detail_url = bad, "jAvAsCrIpT:alert(2)"
    d.items[0].attachments = [Attachment(label="Evil", url=bad), Attachment(label="Fine", url="https://x/ok.pdf")]
    d.notices[0].url = "JAVASCRIPT:alert(3)"
    src = tmp_path / "items.json"
    dump_digest(d, src)
    build_site(d, tmp_path / "site", src, CATS, NOW)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    cpc = (tmp_path / "site" / "ics" / "chicago-plan-commission.ics").read_bytes().decode("utf-8")
    assert "javascript" not in html.lower() and "javascript" not in cpc.lower()
    assert 'href="https://x/ok.pdf"' in html and "TEFRA hearing, Pheasant Ridge" in html  # notice title kept, unlinked
    assert "URL:" not in cpc and "Agenda" not in cpc
    assert safe_url(" HTTPS://x/a ") == "HTTPS://x/a" and safe_url("data:text/html,x") is None and safe_url(None) is None


def test_ics_urls_lose_cr_and_lf():
    m = Meeting(id="cpc:2026-10-15", source_id="cpc", body="Chicago Plan Commission", body_id="cpc",
                start=dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI), agenda_url="https://x/a.pdf\r\nX-EVIL:1")
    cal = ics_calendar([m], "Chicago Plan Commission", NOW)
    assert "\r\nX-EVIL" not in cal and "URL:https://x/a.pdfX-EVIL:1" in cal


def test_escape_and_fold():
    assert ics_escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    folded = fold("DESCRIPTION:" + "é" * 80)
    lines = folded.split("\r\n")
    assert all(len(line.encode("utf-8")) <= 75 for line in lines)
    assert all(line.startswith(" ") for line in lines[1:])
    assert "".join(line[1:] if i else line for i, line in enumerate(lines)) == "DESCRIPTION:" + "é" * 80


def test_calendar_has_timezone_block():
    cal = ics_calendar(sample().meetings[1:2], "Chicago Plan Commission", NOW)
    assert "BEGIN:VTIMEZONE\r\nTZID:America/Chicago" in cal


def test_meetings_from_the_last_two_weeks_show_under_recent_with_their_items(tmp_path):
    d = sample()
    old = Meeting(id="elms:Z0", source_id="elms-zoning", body="City Council Committee on Zoning", body_id="z",
                  start=dt.datetime(2026, 9, 1, 10, 0, tzinfo=CHI), location="Old Room 1A")
    d.meetings.append(old)
    src = tmp_path / "items.json"
    dump_digest(d, src)
    build_site(d, tmp_path / "site", src, CATS, NOW)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    upcoming, recent = html.split("<h2>Upcoming meetings</h2>")[1].split("<h2>Recent meetings</h2>")
    recent = recent.split("<h2>Open notices</h2>")[0]
    assert "Rezoning at 4400 N. Broadway" in recent and "Rezoning at 4400 N. Broadway" not in upcoming
    assert "Wednesday, September 30" in recent and "Old Room 1A" not in html  # older than two weeks: not shown


def test_unknown_meeting_is_hidden_from_upcoming_and_cancelled_in_ics(tmp_path):
    d = sample()
    stale = Meeting(id="cpc:2026-10-22", source_id="cpc", body="Chicago Plan Commission", body_id="cpc",
                    start=dt.datetime(2026, 10, 22, 10, 0, tzinfo=CHI), status="unknown", location="Stale Room 9Z")
    d.meetings.append(stale)
    src = tmp_path / "items.json"
    dump_digest(d, src)
    build_site(d, tmp_path / "site", src, CATS, NOW)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Stale Room 9Z" not in html and "City Hall, Room 201A" in html
    cpc = (tmp_path / "site" / "ics" / "chicago-plan-commission.ics").read_bytes().decode("utf-8")
    assert "UID:cpc-2026-10-22@housingdigest.danielkayhertz.com" in cpc
    stale_event = cpc.split("UID:cpc-2026-10-22")[1].split("END:VEVENT")[0]
    assert "STATUS:CANCELLED" in stale_event
