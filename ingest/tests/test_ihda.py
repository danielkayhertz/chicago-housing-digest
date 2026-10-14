import datetime as dt
from collections import Counter

from digest.models import CHI
from digest.sources.ihda import IhdaAdapter, parse_page
from fakes import FIX, FakeFetcher

URL = "https://www.ihda.org/about-ihda/public-meetings-and-notices/"


def page():
    return parse_page((FIX / "ihda/meetings_page.html").read_text(encoding="utf-8"), URL, "ihda")


def test_meetings_parsed_and_skips_schedule_and_livestream():
    meetings, _ = page()
    assert {(m.body, m.date) for m in meetings} == {
        ("IHDA Board", dt.date(2026, 9, 18)), ("IHDA Finance Committee", dt.date(2026, 9, 18)),
        ("IHDA Audit Committee", dt.date(2026, 9, 18)), ("IHDA Board", dt.date(2026, 7, 24)),
        ("IHDA Finance Committee", dt.date(2026, 7, 24)), ("IHDA Audit Committee", dt.date(2025, 6, 26)),
        ("IHDA Asset Management Committee", dt.date(2026, 5, 15)),
    }
    board = next(m for m in meetings if m.body == "IHDA Board" and m.date == dt.date(2026, 9, 18))
    assert board.time == dt.time(11, 0)
    assert board.agenda_url.endswith("0918-Agenda-Final.pdf")
    assert board.notice_url is not None
    finance = next(m for m in meetings if m.body == "IHDA Finance Committee" and m.date == dt.date(2026, 9, 18))
    assert finance.time == dt.time(10, 0) and finance.agenda_url.endswith("Finance-Committee-Agenda.pdf")
    july = next(m for m in meetings if m.body == "IHDA Board" and m.date == dt.date(2026, 7, 24))
    assert july.minutes_url is not None and july.agenda_url is None


def test_notices_by_section():
    _, notices = page()
    assert Counter(n.kind for n in notices) == {"hearing": 2, "comment_period": 4, "public_notice": 1}
    tefra = notices[0]
    assert tefra.title.startswith("Pheasant Ridge Hunter Apartments")
    assert tefra.deadline.isoformat() == "2026-10-15T10:00:00-05:00"
    assert tefra.id.startswith("ihda:notice:") and len(tefra.id) == len("ihda:notice:") + 12
    assert any("Qualified Allocation Plan" in n.title for n in notices if n.kind == "comment_period")
    assert next(n for n in notices if n.kind == "public_notice").title.startswith("Notice to Terminate Subsidy")


def test_adapter_window_and_extraction_flag():
    f = FakeFetcher([("public-meetings-and-notices", "ihda/meetings_page.html")])
    cfg = {"id": "ihda", "url": URL, "window_days": [-60, 90]}
    res = IhdaAdapter(cfg).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    assert sorted(m.id for m in res.meetings) == [
        "ihda:ihda-audit-committee:2026-09-18", "ihda:ihda-board:2026-09-18", "ihda:ihda-finance-committee:2026-09-18"]
    board = next(m for m in res.meetings if m.id == "ihda:ihda-board:2026-09-18")
    assert board.start.isoformat() == "2026-09-18T11:00:00-05:00"
    assert board.jurisdiction == "state" and board.needs_ai_extraction and board.status == "held"
    assert len(res.notices) == 7 and res.items == []
