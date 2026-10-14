import datetime as dt
import json

from digest.http import NotFound
from digest.models import CHI, Outcome
from digest.pdftext import pdf_text
from digest.sources import elms
from digest.sources.elms import ElmsAdapter, agenda_file, parse_agenda
from fakes import FIX, FakeFetcher

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=CHI)
CFG = {"id": "elms-zoning", "body": "Committee on Zoning, Landmarks and Building Standards",
       "display_body": "City Council Committee on Zoning"}
SEP17 = "elms:A44FB4F3-45B0-F111-AAAC-001DD80B58E5"


def routes(**over):
    base = [
        ("recordNumber/O2026-0025391", "elms/matter_by_record.json"),
        ("recordNumber/O2026-0027202", NotFound("O2026-0027202", 404)),
        ("recordNumber/SO2026-0027202", "elms/matter_SO2026-0027202.json"),
        ("meetingattachmentspublic/", "elms/agenda_zoning.pdf"),
        ("skip=0", "elms/meetings_zoning.json"),
        ("/meeting?", b'{"data": []}'),  # the page after: only an empty page ends the listing
    ]
    return [(k, over.get(k, v)) for k, v in base]


def test_parse_agenda_records_and_titles():
    text = pdf_text((FIX / "elms/agenda_zoning.pdf").read_bytes())
    assert parse_agenda(text) == [
        ("O2026-0025391", "Amendment of Municipal Code Chapter 17-3 by adding Section 17-3-0600 regarding "
                          "transient-oriented development (TOD) streets"),
        ("O2026-0027202", "Amendment of Municipal Code Chapter 5-12 empowering Department of Buildings to "
                          "strengthen standards and enforcement mechanisms for residential buildings"),
    ]


def test_agenda_file_picks_highest_sort():
    rows = json.loads((FIX / "elms/meetings_zoning.json").read_text(encoding="utf-8"))["data"]
    sep17 = next(r for r in rows if r["meetingId"] == SEP17.split(":")[1])
    assert agenda_file(sep17["files"])["path"].endswith("a3e478c9-5f66-457d-8499-0cb46d2cfdec.pdf")


def test_fetch_meetings_items_outcomes():
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes()), NOW)
    by_id = {m.id: m for m in res.meetings}
    assert len(res.meetings) == 6
    m = by_id[SEP17]
    assert m.body == "City Council Committee on Zoning"
    assert m.start.isoformat() == "2026-09-17T10:00:00-05:00"
    assert m.status == "held"
    assert m.agenda_url.endswith("a3e478c9-5f66-457d-8499-0cb46d2cfdec.pdf")
    assert m.video_url == "https://vimeo.com/manage/videos/1227517217"

    items = [i for i in res.items if i.meeting_id == SEP17]
    assert [i.id for i in items] == [f"{SEP17}#O2026-0025391", f"{SEP17}#O2026-0027202"]
    tod, bldg = items
    assert tod.title.startswith("Amendment of Municipal Code Chapter 17-3")
    assert tod.outcome is None
    assert [a.label for a in tod.attachments] == ["O2026-0025391 Ordinance", "O2026-0025391 Mayoral Letter"]
    assert bldg.source_ref == "SO2026-0027202"
    assert bldg.outcome == Outcome(action="Recommended to Pass", date=dt.date(2026, 9, 17), tally=None)

    cancelled = {x.id for x in res.meetings if x.status == "cancelled"}
    assert len(cancelled) == 3
    assert not any(i.meeting_id in cancelled for i in res.items)


def test_missing_matter_falls_back_to_agenda_title():
    gone = NotFound("x", 404)
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes(**{"recordNumber/SO2026-0027202": gone})), NOW)
    bldg = next(i for i in res.items if i.id == f"{SEP17}#O2026-0027202")
    assert bldg.source_ref == "O2026-0027202"
    assert bldg.title.startswith("Amendment of Municipal Code Chapter 5-12")
    assert bldg.attachments == []


def test_agenda_text_without_record_numbers_goes_to_ai_extraction(monkeypatch):
    monkeypatch.setattr(elms, "pdf_text", lambda data: "COMMITTEE ON ZONING AGENDA\n" * 20)
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes()), NOW)
    assert res.items == [] and res.warnings == []  # no "no record numbers" stale warning
    with_agenda = {m.id for m in res.meetings if m.status != "cancelled" and m.agenda_url}
    assert with_agenda and {m.id for m in res.meetings if m.needs_ai_extraction} == with_agenda


def test_parse_agenda_numbered_parenthetical_records():
    # Layout of the Committee on Housing and Real Estate agenda for October 8, 2026 (text layer as extracted).
    text = """AGENDA
Thursday, October 8, 2026
Mayoral Appointments

1. (A2026-0029000) Appointment of Vernell Givens as Commissioner of Chicago Housing
Authority for a term effective immediately and expiring August 31, 2031.

Department of Planning and Development

4. (O2026-0029008) Sale of City-owned property at 11414 S Halsted St to Far South CDC
under Low-Income Housing Tax Credits program for redevelopment of Morgan Park
Commons (repeal and replacement of O2026-0026567 in entirety).
(21st Ward)
Sale Price $2.00

10. (O2026-0029011) Sale of City-owned property at 4549 S Indiana Ave to Click
Development LLC 4549 Indiana under ChiBlockBuilder.
(3rd Ward)
"""
    assert parse_agenda(text) == [
        ("A2026-0029000", "Appointment of Vernell Givens as Commissioner of Chicago Housing Authority for a term "
                          "effective immediately and expiring August 31, 2031."),
        ("O2026-0029008", "Sale of City-owned property at 11414 S Halsted St to Far South CDC under Low-Income "
                          "Housing Tax Credits program for redevelopment of Morgan Park Commons (repeal and "
                          "replacement of O2026-0026567 in entirety)."),
        ("O2026-0029011", "Sale of City-owned property at 4549 S Indiana Ave to Click Development LLC 4549 Indiana "
                          "under ChiBlockBuilder."),
    ]


def test_window_is_carried_on_the_result():
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes()), NOW)
    assert (res.window_start, res.window_end) == (dt.date(2026, 9, 6), dt.date(2026, 11, 19))


def test_parse_agenda_inline_title_on_the_following_line():
    text = """AGENDA

4. (O2026-0029008)
Sale of City-owned property at 11414 S Halsted St to Far South CDC
for redevelopment of Morgan Park Commons.
(21st Ward)

5. (O2026-0029011) Sale of City-owned property at 4549 S Indiana Ave.

6. (O2026-0029012)

Department of Planning and Development
"""
    assert parse_agenda(text) == [
        ("O2026-0029008", "Sale of City-owned property at 11414 S Halsted St to Far South CDC for redevelopment of "
                          "Morgan Park Commons."),
        ("O2026-0029011", "Sale of City-owned property at 4549 S Indiana Ave."),
        ("O2026-0029012", ""),  # nothing follows before a blank line: no title rather than a section heading
    ]


def test_parse_agenda_record_number_after_the_title():
    # Layout of the Committee on Finance agenda for October 13, 2026: the record closes the entry.
    text = """AGENDA OF MATTERS
TO BE CONSIDERED BY THE
COMMITTEE ON FINANCE

DEPARTMENT OF LAW

1. A communication transmitting reports of cases in which verdicts, judgments or
settlements were entered into for the month of September 2026.

DEPARTMENT OF HOUSING

2. An ordinance authorizing the issuance of Multi-Family program funds to Harvest Homes
Apartments, LLC for a residential housing project at 3415-3449 W. Jackson Boulevard.      28th Ward
(O2026-0028750)
    Amount not to exceed: $11,000,000. Multi-Family Program Funds

MISCELLANEOUS

3. An ordinance, introduced by Alderman Pat Dowell (3rd Ward), amending Municipal Code Chapter 10-8
regarding charitable solicitation on public way.
(O2026-0026950)
"""
    assert parse_agenda(text) == [
        ("O2026-0028750", "An ordinance authorizing the issuance of Multi-Family program funds to Harvest Homes "
                          "Apartments, LLC for a residential housing project at 3415-3449 W. Jackson Boulevard. 28th Ward"),
        ("O2026-0026950", "An ordinance, introduced by Alderman Pat Dowell (3rd Ward), amending Municipal Code "
                          "Chapter 10-8 regarding charitable solicitation on public way."),
    ]


def test_trailing_layout_ignores_a_cross_reference_ending_a_title_line():
    text = """1. An ordinance amending the sale of 11414 S Halsted St, replacing and repealing the earlier
ordinance authorizing it (O2026-0026567)
for redevelopment of Morgan Park Commons.      9th Ward
(O2026-0029008)

2. An ordinance repealing (O2026-0026567)
"""
    assert parse_agenda(text) == [
        ("O2026-0029008", "An ordinance amending the sale of 11414 S Halsted St, replacing and repealing the earlier "
                          "ordinance authorizing it (O2026-0026567) for redevelopment of Morgan Park Commons. 9th Ward"),
    ]


def test_trailing_layout_warns_about_entries_without_a_record(monkeypatch):
    text = """COMMITTEE ON FINANCE

1. A communication transmitting reports of cases for the month of September 2026.

2. An ordinance authorizing Multi-Family program funds for a project at 3415 W. Jackson Blvd.
(O2026-0025391)

3. A communication from the Department of Law.
"""
    monkeypatch.setattr(elms, "pdf_text", lambda data: text)
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes()), NOW)
    assert any(i.source_ref == "O2026-0025391" for i in res.items)
    assert any("2 numbered agenda entries have no record number" in w for w in res.warnings)
    assert not any(m.needs_ai_extraction for m in res.meetings)


def _row(n, day):
    return {"meetingId": f"M{n}", "date": f"2026-10-{day:02d}T15:00:00Z", "status": "Scheduled"}


def test_paging_continues_past_a_short_page_until_an_empty_one():
    cfg = {**CFG, "page_size": 2}
    f = FakeFetcher([
        ("skip=0", json.dumps({"data": [_row(1, 20)]}).encode()),       # short, but not the last page
        ("skip=2", json.dumps({"data": [_row(2, 14), _row(3, 13)]}).encode()),
        ("/meeting?", b'{"data": []}'),
    ])
    res = ElmsAdapter(cfg).fetch(f, NOW)
    assert sorted(m.id for m in res.meetings) == ["elms:M1", "elms:M2", "elms:M3"]
    assert res.warnings == []


def test_paging_is_capped_with_a_warning():
    cfg = {**CFG, "page_size": 1}
    f = FakeFetcher([("/meeting?", json.dumps({"data": [_row(1, 20)]}).encode())])
    res = ElmsAdapter(cfg).fetch(f, NOW)
    assert len(f.calls) == elms.MAX_MEETING_PAGES
    assert any("not exhausted" in w for w in res.warnings)
