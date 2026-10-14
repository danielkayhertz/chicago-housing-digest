import datetime as dt

from digest.models import CHI
from digest.sources.legistar import LegistarAdapter
from fakes import FakeFetcher

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=CHI)
CFG = {"id": "cha", "client": "thecha", "bodies": {
    138: "CHA Board of Commissioners", 188: "CHA Finance & Audit Committee",
    189: "CHA Real Estate Operations Development Committee", 190: "CHA Tenant Services Committee"}}


def fetcher():
    return FakeFetcher([
        ("/events/1146/eventitems", "legistar/cha_eventitems.json"),
        ("/eventitems", "common/empty_list.json"),
        ("/thecha/events?", "legistar/cha_events.json"),
    ])


def test_events_skip_hidden_and_map_bodies():
    f = fetcher()
    res = LegistarAdapter(CFG).fetch(f, NOW)
    assert sorted(m.id for m in res.meetings) == ["legistar:thecha:1116", "legistar:thecha:1146", "legistar:thecha:1147"]
    m = next(m for m in res.meetings if m.id == "legistar:thecha:1146")
    assert m.body == "CHA Real Estate Operations Development Committee"
    assert m.start.isoformat() == "2026-09-15T08:40:00-05:00"
    assert m.status == "held"
    assert m.detail_url.startswith("https://thecha.legistar.com/MeetingDetail.aspx")
    assert any("EventDate ge datetime'2026-09-06'" in c for c in f.calls)


def test_items_only_matters_with_attachments():
    res = LegistarAdapter(CFG).fetch(fetcher(), NOW)
    assert [i.source_ref for i in res.items] == ["26-57", "26-96", "26-112"]
    iga = res.items[0]
    assert iga.id == "legistar:thecha:1146#26-57"
    assert iga.title.startswith("Authorization to execute an Intergovernmental Agreement")
    assert iga.attachments[0].label.startswith("Approved IGA with City of Chicago")
    assert iga.attachments[0].url.endswith(".pdf")
    assert iga.outcome is None
