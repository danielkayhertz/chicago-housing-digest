import datetime as dt

from digest.models import CHI
from digest.sources.clihtf import ClihtfAdapter
from fakes import FakeFetcher


def test_board_meetings_only_with_agenda_text():
    f = FakeFetcher([("tribe/events/v1/events", "clihtf/events.json")])
    res = ClihtfAdapter({"id": "clihtf"}).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    assert [m.id for m in res.meetings] == ["clihtf:319068"]
    m = res.meetings[0]
    assert m.body == "CLIHTF Board of Directors"
    assert m.start.isoformat() == "2026-10-13T08:30:00-05:00"
    assert m.status == "scheduled" and m.needs_ai_extraction
    assert m.agenda_url == "https://clihtf.org/event/board-meeting-11-7/"
    assert "I. Roll Call" in m.agenda_text and "Allocations Committee" in m.agenda_text
    assert "start_date=2026-09-21" in f.calls[0]
