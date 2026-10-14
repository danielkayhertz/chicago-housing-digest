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
        ("/meeting?", "elms/meetings_zoning.json"),
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


def test_agenda_without_record_numbers_warns(monkeypatch):
    monkeypatch.setattr(elms, "pdf_text", lambda data: "COMMITTEE ON ZONING AGENDA\n" * 20)
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes()), NOW)
    assert res.items == []
    assert any("no record numbers" in w for w in res.warnings)
