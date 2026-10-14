import datetime as dt

from digest.http import FetchError
from digest.models import CHI, ChangeEvent, Digest, Enrichment, Item, Meeting, Notice, Outcome, SourceResult, SourceStatus
from digest.state import count_items, is_shown, merge

T0 = dt.datetime(2026, 10, 5, 6, 20, tzinfo=CHI)
T1 = T0 + dt.timedelta(hours=6)
OCT15 = dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI)


def mtg(mid="cpc:2026-10-15", start=OCT15, **kw):
    return Meeting(id=mid, source_id=kw.pop("source_id", "cpc"), body="Chicago Plan Commission", body_id="cpc",
                   start=start, **kw)


def itm(iid, mid="cpc:2026-10-15", **kw):
    return Item(id=iid, meeting_id=mid, source_ref=iid.split("#")[-1], title=kw.pop("title", f"Item {iid}"), **kw)


def res(meetings=(), items=(), notices=(), warnings=()):
    return SourceResult(meetings=list(meetings), items=list(items), notices=list(notices), warnings=list(warnings))


def baseline():
    return merge(None, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")])}, T0)


def types(d):
    return [(e.type, e.ref_id) for e in d.events]


def test_first_run_of_a_source_is_quiet():
    d = baseline()
    assert d.events == []
    assert d.meetings[0].first_seen_at == T0 and d.items[0].first_seen_at == T0
    assert [(s.source_id, s.status, s.last_success_at) for s in d.sources] == [("cpc", "ok", T0)]


def test_first_success_after_only_errors_is_quiet():
    prev = Digest(generated_at=T0, sources=[SourceStatus(source_id="cpc", status="error", last_success_at=None, message="boom")])
    d = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")])}, T1)
    assert d.events == []
    assert len(d.meetings) == 1 and d.sources[0].status == "ok"


def test_new_meeting_emits_meeting_new_only():
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a"), mtg("cpc:2026-11-19", dt.datetime(2026, 11, 19, 10, 0, tzinfo=CHI))],
                                      [itm("cpc:2026-10-15#a"), itm("cpc:2026-11-19#x", "cpc:2026-11-19")])}, T1)
    assert types(d) == [("meeting_new", "cpc:2026-11-19")]


def test_reschedule_same_id_emits_rescheduled():
    moved = mtg(start=dt.datetime(2026, 10, 22, 10, 0, tzinfo=CHI), agenda_url="https://x/a")
    d = merge(baseline(), {"cpc": res([moved], [itm("cpc:2026-10-15#a")])}, T1)
    assert types(d) == [("meeting_rescheduled", "cpc:2026-10-15")]
    assert len(d.meetings) == 1 and d.meetings[0].start.day == 22
    assert d.meetings[0].first_seen_at == T0 and d.meetings[0].last_changed_at == T1


def test_cancel_emits_cancelled():
    d = merge(baseline(), {"cpc": res([mtg(status="cancelled", agenda_url="https://x/a")])}, T1)
    assert types(d) == [("meeting_cancelled", "cpc:2026-10-15")]


def test_agenda_posted_and_item_added():
    prev = merge(None, {"cpc": res([mtg()])}, T0)
    d = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")])}, T1)
    assert types(d) == [("agenda_posted", "cpc:2026-10-15"), ("item_added", "cpc:2026-10-15#a")]


def test_outcome_recorded():
    done = itm("cpc:2026-10-15#a", outcome=Outcome(action="Approved", date=dt.date(2026, 10, 15)))
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a")], [done])}, T1)
    assert types(d) == [("outcome_recorded", "cpc:2026-10-15#a")]


def test_failed_source_carries_forward():
    prev = merge(None, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")]),
                        "clihtf": res([mtg("clihtf:1", source_id="clihtf")])}, T0)
    d = merge(prev, {"cpc": FetchError("https://www.chicago.gov/x", 503), "clihtf": res([mtg("clihtf:1", source_id="clihtf")])}, T1)
    assert [m.id for m in d.meetings if m.source_id == "cpc"] == ["cpc:2026-10-15"]
    assert [i.id for i in d.items] == ["cpc:2026-10-15#a"]
    status = {s.source_id: s for s in d.sources}
    assert status["cpc"].status == "error" and status["cpc"].last_success_at == T0 and "503" in status["cpc"].message
    assert status["clihtf"].status == "ok" and status["clihtf"].last_success_at == T1
    assert d.events == []


def test_warnings_make_a_source_stale():
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a")], warnings=["agenda has no text layer"])}, T1)
    s = d.sources[0]
    assert s.status == "stale" and s.message == "agenda has no text layer"


def test_refreshed_meeting_without_items_keeps_previous_items():
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a")], [])}, T1)
    assert [i.id for i in d.items] == ["cpc:2026-10-15#a"]


def test_items_missing_from_refreshed_meeting_are_dropped():
    prev = merge(None, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a"), itm("cpc:2026-10-15#b")])}, T0)
    d = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#b")])}, T1)
    assert [i.id for i in d.items] == ["cpc:2026-10-15#b"]


def test_last_changed_only_moves_on_content_change():
    prev = baseline()
    prev.items[0].enrichment = Enrichment(status="ok", summary="s")
    same = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")])}, T1)
    assert same.items[0].last_changed_at == T0
    assert same.items[0].enrichment.summary == "s"  # carried over when the new item has none
    edited = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a", title="Amended")])}, T1)
    assert edited.items[0].last_changed_at == T1 and edited.items[0].first_seen_at == T0


def test_notice_new_and_pruning():
    old_notice = Notice(id="ihda:notice:old", source_id="ihda", body="IHDA", kind="comment_period", title="Old", url="https://x/o")
    open_notice = Notice(id="ihda:notice:open", source_id="ihda", body="IHDA", kind="hearing", title="Open", url="https://x/p",
                         deadline=T0 + dt.timedelta(days=200))
    long_ago = T0 - dt.timedelta(days=100)
    prev = merge(None, {"ihda": res([mtg("ihda:b:2026-07-01", dt.datetime(2026, 7, 1, 10, 0, tzinfo=CHI), source_id="ihda")],
                                    [itm("ihda:b:2026-07-01#1", "ihda:b:2026-07-01")], [old_notice, open_notice])}, long_ago)
    prev.events = [ChangeEvent(type="meeting_new", ref_id="x", at=T0 - dt.timedelta(days=15)),
                   ChangeEvent(type="meeting_new", ref_id="y", at=T0 - dt.timedelta(days=13))]
    new_notice = Notice(id="ihda:notice:new", source_id="ihda", body="IHDA", kind="hearing", title="New", url="https://x/n")
    d = merge(prev, {"ihda": res(notices=[new_notice])}, T0)
    assert d.meetings == [] and d.items == []                       # meeting held more than 45 days ago
    assert sorted(n.id for n in d.notices) == ["ihda:notice:new", "ihda:notice:open"]
    assert types(d) == [("meeting_new", "y"), ("notice_new", "ihda:notice:new")]


def test_count_items():
    d = baseline()
    d.items.append(itm("cpc:2026-10-15#b", enrichment=Enrichment(status="screened_out")))
    d.items.append(itm("cpc:2026-10-15#c", enrichment=Enrichment(status="ok", housing_relevant=True)))
    count_items(d)
    assert (d.meetings[0].item_count_total, d.meetings[0].item_count_relevant) == (3, 2)


def test_is_shown():
    assert is_shown(itm("cpc:2026-10-15#a"))
    assert is_shown(itm("cpc:2026-10-15#a", enrichment=Enrichment(status="failed")))
    assert is_shown(itm("cpc:2026-10-15#a", enrichment=Enrichment(status="ok", housing_relevant=True)))
    assert not is_shown(itm("cpc:2026-10-15#a", enrichment=Enrichment(status="screened_out")))


def test_meeting_past_prune_cutoff_is_not_re_added_or_announced():
    stale = mtg("cpc:2026-08-16", T1 - dt.timedelta(days=50), source_id="cpc")
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a"), stale],
                                      [itm("cpc:2026-10-15#a"), itm("cpc:2026-08-16#1", "cpc:2026-08-16")])}, T1)
    assert d.events == []
    assert [m.id for m in d.meetings] == ["cpc:2026-10-15"]
    assert [i.id for i in d.items] == ["cpc:2026-10-15#a"]


def test_notice_still_returned_is_kept_past_age_limit_and_pruned_once_gone():
    n = Notice(id="ihda:notice:long", source_id="ihda", body="IHDA", kind="comment_period", title="Long", url="https://x/l")
    long_ago = T0 - dt.timedelta(days=100)
    prev = merge(None, {"ihda": res(notices=[n])}, long_ago)
    kept = merge(prev, {"ihda": res(notices=[n.model_copy()])}, T0)
    assert [x.id for x in kept.notices] == ["ihda:notice:long"]
    assert kept.notices[0].first_seen_at == long_ago and kept.events == []
    gone = merge(kept, {"ihda": res()}, T1)
    assert gone.notices == []


def _windowed(meetings, start, end, **kw):
    return SourceResult(meetings=list(meetings), window_start=start, window_end=end, **kw)


def test_in_window_future_meeting_not_returned_becomes_unknown_without_event():
    old = mtg("cpc:2026-10-15")
    prev = merge(None, {"cpc": res([old])}, T0)
    moved = mtg("cpc:2026-10-22", dt.datetime(2026, 10, 22, 10, 0, tzinfo=CHI))  # date-keyed id: a move is a new id
    d = merge(prev, {"cpc": _windowed([moved], dt.date(2026, 9, 21), dt.date(2026, 12, 4))}, T1)
    by_id = {m.id: m for m in d.meetings}
    assert by_id["cpc:2026-10-15"].status == "unknown" and by_id["cpc:2026-10-22"].status == "scheduled"
    assert types(d) == [("meeting_new", "cpc:2026-10-22")]


def test_out_of_window_and_past_meetings_are_untouched():
    far = mtg("cpc:2027-03-18", dt.datetime(2027, 3, 18, 10, 0, tzinfo=CHI))
    past = mtg("cpc:2026-10-05", dt.datetime(2026, 10, 5, 10, 0, tzinfo=CHI))  # earlier today than T1 (12:20)
    prev = merge(None, {"cpc": res([far, past, mtg("cpc:2026-10-15")])}, T0)
    d = merge(prev, {"cpc": _windowed([mtg("cpc:2026-10-15")], dt.date(2026, 9, 21), dt.date(2026, 12, 4))}, T1)
    assert {m.id: m.status for m in d.meetings} == {
        "cpc:2027-03-18": "scheduled", "cpc:2026-10-05": "scheduled", "cpc:2026-10-15": "scheduled"}
    assert d.events == []


def test_other_sources_and_windowless_results_are_untouched():
    prev = merge(None, {"cpc": res([mtg("cpc:2026-10-15")]), "cdc": res([mtg("cdc:2026-10-13", source_id="cdc")])}, T0)
    d = merge(prev, {"cpc": res([]), "cdc": _windowed([], dt.date(2026, 9, 21), dt.date(2026, 12, 4))}, T1)
    status = {m.id: m.status for m in d.meetings}
    assert status["cpc:2026-10-15"] == "scheduled"   # no window carried: the rule does not apply
    assert status["cdc:2026-10-13"] == "unknown"     # its own source's window does


def test_unknown_meeting_returned_again_gets_its_real_status_back():
    prev = merge(None, {"cpc": res([mtg()])}, T0)
    win = (dt.date(2026, 9, 21), dt.date(2026, 12, 4))
    gone = merge(prev, {"cpc": _windowed([], *win)}, T1)
    assert gone.meetings[0].status == "unknown"
    back = merge(gone, {"cpc": _windowed([mtg()], *win)}, T1 + dt.timedelta(hours=6))
    assert back.meetings[0].status == "scheduled"
    assert back.events == []


def test_cancelled_meeting_not_returned_stays_cancelled():
    prev = merge(None, {"cpc": res([mtg(status="cancelled")])}, T0)
    d = merge(prev, {"cpc": _windowed([], dt.date(2026, 9, 21), dt.date(2026, 12, 4))}, T1)
    assert d.meetings[0].status == "cancelled"
