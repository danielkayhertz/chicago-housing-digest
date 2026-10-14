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
    assert (res.window_start, res.window_end) == (dt.date(2026, 9, 21), dt.date(2026, 12, 4))


def _pages():
    import copy
    import json
    from fakes import FIX
    page1 = json.loads((FIX / "clihtf/events.json").read_text(encoding="utf-8"))
    board = next(e for e in page1["events"] if "board meeting" in e["title"].lower())
    later = copy.deepcopy(board)
    later.update(id=999001, start_date="2026-11-06 08:30:00", url="https://clihtf.org/event/board-meeting-12-4/")
    page1["total_pages"] = 2
    page2 = {"events": [later], "total_pages": 2}
    return page1, page2


def test_board_meetings_past_the_first_page_are_read():
    import json
    page1, page2 = _pages()
    f = FakeFetcher([("&page=1", json.dumps(page1).encode()), ("&page=2", json.dumps(page2).encode())])
    res = ClihtfAdapter({"id": "clihtf"}).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    assert [m.id for m in res.meetings] == ["clihtf:319068", "clihtf:999001"]
    assert len(f.calls) == 2 and res.warnings == []


def test_event_paging_is_capped_with_a_warning():
    from digest.sources import clihtf
    page1, _ = _pages()
    page1["total_pages"] = 99
    ids = iter(range(1000, 5000))
    calls = []

    class Endless:
        def get_json(self, url, params=None):
            calls.append(params["page"])
            page = {**page1, "events": [{**e, "id": next(ids)} for e in page1["events"]]}  # always new events
            return page
    res = ClihtfAdapter({"id": "clihtf"}).fetch(Endless(), dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    assert calls == list(range(1, clihtf.MAX_PAGES + 1))
    assert any("more than" in w for w in res.warnings)
