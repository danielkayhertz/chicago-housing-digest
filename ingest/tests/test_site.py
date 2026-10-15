import datetime as dt
import json
import re
from pathlib import Path

from digest.categories import Categories
from digest.models import (CHI, CategoryHit, Digest, Enrichment, Figures, Geography, Item, Meeting, Notice, Outcome,
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


HRE = "City Council Committee on Housing and Real Estate"


def render(d, tmp_path):
    src = tmp_path / "items.json"
    dump_digest(d, src)
    build_site(d, tmp_path / "site", src, CATS, NOW)
    return (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


def council_digest():
    d = sample()
    h = Meeting(id="elms:H1", source_id="elms-housing", body=HRE, body_id="h",
                start=dt.datetime(2026, 10, 8, 10, 30, tzinfo=CHI))

    def sale(n, addr, units, buyer):
        return Item(id=f"elms:H1#{n}", meeting_id=h.id, source_ref=f"O2026-{n}",
                    title=f"Sale of City-owned property at {addr} to {buyer}",
                    enrichment=Enrichment(status="ok", housing_relevant=True, summary=f"Sale of land for {units} homes.",
                                          categories=[CategoryHit(id="ahpaa", via="body")],
                                          geography=Geography(address=addr, ward=27), figures=Figures(units=units),
                                          amount=315315, amount_kind="sale price", entities=[buyer]))
    d.meetings.append(h)
    d.items += [sale(1, "2054 W Warren Blvd", 20, "EGP Warren Apartments LLC"), sale(2, "6 S Elm St", 2, "<b>Evil</b> LLC")]
    return d


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
    upcoming, recent = html.split('<h2 id="recent">Recent</h2>')
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


def test_long_titles_get_a_short_headline_with_the_full_text_in_a_disclosure(tmp_path):
    from digest.site import short_title
    d = sample()
    long = "A proposed Planned Development, submitted by MKR Owner LLC for the property generally located at " \
           "the Kinzie Green Line site. The Applicant requests a rezoning from M2-3 to DX-5."
    d.items[0].title = long
    src = tmp_path / "items.json"
    dump_digest(d, src)
    build_site(d, tmp_path / "site", src, CATS, NOW)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    head = short_title(long)
    assert len(head) <= 101 and head.endswith("…") and not head[:-1].endswith(" ")
    assert f"<strong>{head}</strong>" in html
    assert "<summary>Full agenda text</summary>" + long in html
    assert short_title("Multifamily Update") == "Multifamily Update"


def test_meeting_band_names_family_and_body_and_documents_are_separated(tmp_path):
    from digest.models import Attachment
    d = sample()
    d.items[0].attachments = [Attachment(label="Plan", url="https://x/p.pdf"), Attachment(label="Report", url="https://x/r.pdf")]
    html = render(d, tmp_path)
    sec = html.split('id="cpc-2026-10-15"')[1].split("</section>")[0]
    assert '<h3><span class="fam">Planning</span> Chicago Plan Commission</h3>' in sec
    assert "10:00 AM · " in sec and "1 housing item · " in sec
    assert 'Documents: <a href="https://x/p.pdf">Plan</a> · <a href="https://x/r.pdf">Report</a>' in html
    assert "<main>" in html and 'role="status"' in html and "Updated October 5, 2026, 12:00 PM" in html
    assert '<h2 id="day-2026-10-15">Thursday, October 15</h2>' in html


def test_summaries_drop_sentences_that_only_say_what_the_agenda_leaves_out():
    from digest.site import trim_summary
    assert trim_summary("IHDA will consider bonds, presented by M. Babcock. The agenda gives no bond amount, purpose, or project details.")         == "IHDA will consider bonds, presented by M. Babcock."
    assert trim_summary("Standing update. The provided text is only the meeting agenda and gives no details on the update's content.")         == "Standing update."
    assert trim_summary("A $51,500,000 loan. The material gives no unit counts; it appears under Multifamily Finance.")         == "A $51,500,000 loan. It appears under Multifamily Finance."
    assert trim_summary("Adds 120 homes near the Kinzie Green Line stop.") == "Adds 120 homes near the Kinzie Green Line stop."
    assert trim_summary("The agenda gives no further detail.") == "The agenda gives no further detail."  # never empty a summary


def test_grouped_body_renders_group_heading_cards_and_a_table(tmp_path):
    html = render(council_digest(), tmp_path)
    sec = html.split('id="elms-h1"')[1].split("</section>")[0]
    assert 'class="meeting fam-council"' in html and '<span class="fam">City Council</span>' in sec
    assert '<h4>Sales of city land <span class="n">· 2</span></h4>' in sec
    cards, table = sec.split('<table class="rows">')
    assert "<strong>2054 W Warren Blvd</strong>" in cards and "6 S Elm St" not in cards
    assert "<b>20</b> homes" in cards and "<b>$315,315</b> sale price" in cards and "EGP Warren Apartments LLC" in cards
    assert '<th scope="col">Item</th><th scope="col">Facts</th>' in table
    assert "<summary><strong>6 S Elm St</strong></summary>" in table
    assert '<td class="facts">Ward 27 · 2 homes · $315,315 sale price · &lt;b&gt;Evil&lt;/b&gt; LLC</td>' in table
    assert "<b>Evil</b>" not in html                                   # model text is escaped
    assert 'id="elms-h1-2"' in table and 'data-categories="ahpaa"' in table and 'data-ward="27"' in table


def test_flat_body_renders_only_cards(tmp_path):
    sec = render(council_digest(), tmp_path).split('id="cpc-2026-10-15"')[1].split("</section>")[0]
    assert '<li id="cpc-2026-10-15-kinzie" class="row card"' in sec and "<table" not in sec and "<h4>" not in sec


def test_index_and_panel_list_every_shown_meeting_in_page_order(tmp_path):
    html = render(council_digest(), tmp_path)
    toc = html.split('<nav class="toc"')[1].split("</nav>")[0]
    panel = html.split('<nav id="panel" aria-label="Meetings"')[1].split("</nav>")[0]
    order = ["elms-h1", "cpc-2026-10-15", "cdc-2026-11-10", "elms-z1"]
    for nav in (toc, panel):
        assert re.findall(r'href="#([^"]+)"', nav) == order
        assert re.findall(r'data-target="([^"]+)"', nav) == order
    assert '<span class="n">2 housing items</span>' in toc and '<span class="n">1 housing item</span>' in toc
    assert '<span class="n">0 housing items</span>' in toc                 # the cancelled CDC meeting
    assert '<h2 id="toc-h">Meetings</h2>' in html and "This week" not in html
    for nav in (toc, panel):                                               # upcoming, then a Recent divider
        assert nav.index('href="#cdc-2026-11-10"') < nav.index('<li class="split">Recent</li>')             < nav.index('href="#elms-z1"')
    assert html.index('<h2 id="day-2026-10-08">') < html.index('<h2 id="day-2026-10-15">')         < html.index('<h2 id="day-2026-11-10">') < html.index('<h2 id="recent">Recent</h2>')
    assert html.index('<nav class="toc"') < html.index('<h2 id="day-2026-10-08">')


def test_no_upcoming_meetings_says_so(tmp_path):
    d = sample()
    d.meetings = [m for m in d.meetings if m.id == "elms:Z1"]
    html = render(d, tmp_path)
    assert "No upcoming meetings found." in html and '<h2 id="recent">Recent</h2>' in html


def test_card_does_not_repeat_an_address_headline_as_its_agenda_number(tmp_path):
    d = sample()
    d.items[0].enrichment = d.items[0].enrichment.model_copy(
        update={"geography": Geography(address="1133 W. Kinzie St.", ward=27)})
    sec = render(d, tmp_path).split('id="cpc-2026-10-15-kinzie"')[1].split("</li>")[0]
    assert "<strong>1133 W. Kinzie St.</strong>" in sec and '<span class="src">' not in sec


def test_each_day_is_a_section_the_filters_can_hide(tmp_path):
    html = render(council_digest(), tmp_path)
    for day in ("day-2026-10-08", "day-2026-10-15", "day-2026-11-10", "recent"):
        assert f'<section class="day"><h2 id="{day}">' in html
    first = html.split('<section class="day"><h2 id="day-2026-10-08">')[1].split('<section class="day">')[0]
    assert 'id="elms-h1"' in first and 'id="cpc-2026-10-15"' not in first   # a day holds only its own meetings


SOURCES = [
    {"id": "cha", "adapter": "legistar", "label": "CHA Board of Commissioners and committees"},
    {"id": "cpc", "adapter": "dpd", "body": "Chicago Plan Commission"},
    {"id": "ihda", "adapter": "ihda", "label": "IHDA", "window_days": [-60, 90]},
    {"id": "watch", "adapter": "watcher"},
]


def test_watched_sources_with_nothing_on_the_page_say_so(tmp_path):
    src = tmp_path / "items.json"
    d = sample()
    dump_digest(d, src)
    build_site(d, tmp_path / "site", src, CATS, NOW, sources=SOURCES)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    quiet = html.split('<ul class="quiet"')[1].split("</ul>")[0]
    assert "<li>CHA Board of Commissioners and committees: no meeting posted from September 21 to December 4</li>" in quiet
    assert "<li>IHDA: no meeting posted from September 21 to January 3</li>" in quiet   # its own look-ahead
    assert "Chicago Plan Commission" not in quiet                                     # it has meetings shown
    assert "watch" not in quiet
    assert html.index('<ul class="quiet"') < html.index('<section class="day">')


def test_no_quiet_list_when_every_source_has_a_meeting(tmp_path):
    src = tmp_path / "items.json"
    d = sample()
    dump_digest(d, src)
    build_site(d, tmp_path / "site", src, CATS, NOW, sources=[SOURCES[1]])
    assert 'class="quiet"' not in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
