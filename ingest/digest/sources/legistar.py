"""Generic Legistar Web API adapter (CHA in v1; suburban clients in v1.5)."""
from __future__ import annotations

import datetime as dt

from ..http import Fetcher
from ..models import CHI, Attachment, Item, Meeting, Outcome, SourceResult
from .base import BaseAdapter, window_for

API = "https://webapi.legistar.com/v1"


def _parse_time(s: str | None) -> dt.time | None:
    try:
        return dt.datetime.strptime((s or "").strip(), "%I:%M %p").time()
    except ValueError:
        return None


class LegistarAdapter(BaseAdapter):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.client: str = cfg["client"]
        self.bodies: dict[int, str] = {int(k): v for k, v in cfg["bodies"].items()}
        self.jurisdiction: str = cfg.get("jurisdiction", "chicago")

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        win = window_for(now, self.days)
        res = SourceResult()
        events = fetcher.get_json(f"{API}/{self.client}/events", {
            "$filter": f"EventDate ge datetime'{win.start.isoformat()}' and EventDate le datetime'{win.end.isoformat()}'",
        })
        for ev in events:
            if ev.get("EventBodyId") not in self.bodies:
                continue
            if (ev.get("EventAgendaStatusName") or "").lower() == "hidden":
                continue  # placeholder until the agenda is published
            m = self._meeting(ev, now)
            res.meetings.append(m)
            rows = fetcher.get_json(f"{API}/{self.client}/events/{ev['EventId']}/eventitems", {"Attachments": 1})
            res.items += [self._item(m, r) for r in rows if r.get("EventItemMatterFile")]
        return res

    def _meeting(self, ev: dict, now: dt.datetime) -> Meeting:
        day = dt.date.fromisoformat(ev["EventDate"][:10])
        t = _parse_time(ev.get("EventTime"))
        start = dt.datetime.combine(day, t or dt.time(0, 0), tzinfo=CHI)
        return Meeting(
            id=f"legistar:{self.client}:{ev['EventId']}", source_id=self.source_id,
            body=self.bodies[ev["EventBodyId"]], body_id=str(ev["EventBodyId"]),
            jurisdiction=self.jurisdiction, start=start, time_known=t is not None,
            status="held" if start < now else "scheduled", location=ev.get("EventLocation") or None,
            agenda_url=ev.get("EventAgendaFile") or None, minutes_url=ev.get("EventMinutesFile") or None,
            video_url=ev.get("EventVideoPath") or None, detail_url=ev.get("EventInSiteURL") or None,
        )

    def _item(self, m: Meeting, r: dict) -> Item:
        action = r.get("EventItemActionName") or r.get("EventItemPassedFlagName")
        title = (r.get("EventItemTitle") or r.get("EventItemMatterName") or r["EventItemMatterFile"]).strip()
        return Item(
            id=f"{m.id}#{r['EventItemMatterFile']}", meeting_id=m.id, source_ref=r["EventItemMatterFile"],
            title=title,
            attachments=[
                Attachment(label=a["MatterAttachmentName"], url=a["MatterAttachmentHyperlink"])
                for a in r.get("EventItemMatterAttachments") or [] if a.get("MatterAttachmentHyperlink")
            ],
            outcome=Outcome(action=action, date=m.start.date(), tally=r.get("EventItemTally") or None) if action else None,
        )
