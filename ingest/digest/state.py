"""Merge this run's adapter results into the previous items.json and record what changed."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .models import ChangeEvent, Digest, Item, Meeting, Notice, SourceResult, SourceStatus

EVENT_DAYS = 14
MEETING_PRUNE_DAYS = 45
NOTICE_PRUNE_DAYS = 90
VOLATILE = {"first_seen_at", "last_changed_at", "enrichment", "item_count_total", "item_count_relevant"}


def _content(obj) -> dict:
    return obj.model_dump(mode="json", exclude=VOLATILE)


def _stamp(new, old, now: dt.datetime) -> None:
    if old is None:
        new.first_seen_at = new.last_changed_at = now
    else:
        new.first_seen_at = old.first_seen_at or now
        new.last_changed_at = now if _content(new) != _content(old) else (old.last_changed_at or now)


def merge(prev: Digest | None, results: dict[str, SourceResult | Exception], now: dt.datetime) -> Digest:
    prev = prev or Digest(generated_at=now)
    old_status = {s.source_id: s for s in prev.sources}
    pm = {m.id: m for m in prev.meetings}
    pi = {i.id: i for i in prev.items}
    pn = {n.id: n for n in prev.notices}
    meetings: dict[str, Meeting] = dict(pm)
    items: dict[str, Item] = dict(pi)
    notices: dict[str, Notice] = dict(pn)
    statuses = dict(old_status)
    events = [e for e in prev.events if e.at >= now - dt.timedelta(days=EVENT_DAYS)]
    meeting_cutoff = now - dt.timedelta(days=MEETING_PRUNE_DAYS)
    notice_cutoff = now - dt.timedelta(days=NOTICE_PRUNE_DAYS)
    returned_notices: set[str] = set()  # notices a source returned this run are never pruned

    for sid, res in results.items():
        before = old_status.get(sid)
        if isinstance(res, Exception):
            statuses[sid] = SourceStatus(source_id=sid, status="error",
                                         last_success_at=before.last_success_at if before else None,
                                         message=f"{type(res).__name__}: {res}"[:500])
            continue
        statuses[sid] = SourceStatus(source_id=sid, status="stale" if res.warnings else "ok", last_success_at=now,
                                     message="; ".join(res.warnings)[:1000])
        # No baseline yet (never seen, or only ever errored): this success is the baseline, so stay quiet.
        quiet = before is None or before.last_success_at is None

        def emit(kind: str, ref: str) -> None:
            if not quiet:
                events.append(ChangeEvent(type=kind, ref_id=ref, at=now))

        fresh_by_meeting: dict[str, list[Item]] = defaultdict(list)
        for it in res.items:
            fresh_by_meeting[it.meeting_id].append(it)

        for m in res.meetings:
            if m.start < meeting_cutoff:
                continue  # already pruned (or about to be): a long lookback must not re-add and re-announce it
            old = pm.get(m.id)
            if old is None:
                emit("meeting_new", m.id)
            else:
                if m.status == "cancelled" and old.status != "cancelled":
                    emit("meeting_cancelled", m.id)
                elif m.start != old.start and m.status != "cancelled":
                    emit("meeting_rescheduled", m.id)
                if m.agenda_url and not old.agenda_url:
                    emit("agenda_posted", m.id)
            _stamp(m, old, now)
            meetings[m.id] = m

            fresh = fresh_by_meeting.get(m.id)
            if not fresh:
                continue  # agenda unreadable this run, or extraction skipped: keep what we had
            for iid in [k for k, v in items.items() if v.meeting_id == m.id]:
                del items[iid]
            for it in fresh:
                o = pi.get(it.id)
                if o is None:
                    if old is not None:
                        emit("item_added", it.id)  # a brand-new meeting's items are covered by meeting_new
                else:
                    if it.outcome is not None and it.outcome != o.outcome:
                        emit("outcome_recorded", it.id)
                    if it.enrichment is None:
                        it.enrichment = o.enrichment
                _stamp(it, o, now)
                items[it.id] = it

        for n in res.notices:
            returned_notices.add(n.id)
            o = pn.get(n.id)
            if o is None:
                emit("notice_new", n.id)
            elif n.enrichment is None:
                n.enrichment = o.enrichment
            _stamp(n, o, now)
            notices[n.id] = n

    kept_meetings = sorted((m for m in meetings.values() if m.start >= meeting_cutoff), key=lambda m: (m.start, m.id))
    order = {m.id: i for i, m in enumerate(kept_meetings)}
    kept_items = sorted((i for i in items.values() if i.meeting_id in order), key=lambda i: order[i.meeting_id])
    kept_notices = sorted(
        (n for n in notices.values()
         if n.id in returned_notices
         or not (n.first_seen_at and n.first_seen_at < notice_cutoff and not (n.deadline and n.deadline > now))),
        key=lambda n: (n.first_seen_at or now, n.id),
    )
    return Digest(generated_at=now, sources=sorted(statuses.values(), key=lambda s: s.source_id),
                  meetings=kept_meetings, items=kept_items, notices=kept_notices, events=events)


def is_shown(record) -> bool:
    """True when an Item or Notice should appear: not yet enriched, enrichment failed, or housing-relevant."""
    e = record.enrichment
    return e is None or e.status == "failed" or e.housing_relevant


def count_items(digest: Digest) -> None:
    for m in digest.meetings:
        its = [i for i in digest.items if i.meeting_id == m.id]
        m.item_count_total = len(its)
        m.item_count_relevant = sum(1 for i in its if is_shown(i))
