"""Chicago Low-Income Housing Trust Fund via The Events Calendar REST API."""
from __future__ import annotations

import datetime as dt

from ..http import Fetcher
from ..models import CHI, Meeting, SourceResult
from .base import BaseAdapter, html_to_text, window_for

API = "https://clihtf.org/wp-json/tribe/events/v1/events"


class ClihtfAdapter(BaseAdapter):
    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        win = window_for(now, self.days)
        data = fetcher.get_json(API, {
            "start_date": f"{win.start.isoformat()} 00:00:00",
            "end_date": f"{win.end.isoformat()} 23:59:59",
            "per_page": 50,
        })
        res = SourceResult()
        for ev in data.get("events") or []:
            title = ev.get("title") or ""
            if "board meeting" not in title.lower():
                continue
            start = dt.datetime.fromisoformat(ev["start_date"]).replace(tzinfo=CHI)
            text = html_to_text(ev.get("description") or "")
            cancelled = "cancel" in title.lower()
            res.meetings.append(Meeting(
                id=f"clihtf:{ev['id']}", source_id=self.source_id, body="CLIHTF Board of Directors",
                body_id="clihtf-board", start=start,
                status="cancelled" if cancelled else ("held" if start < now else "scheduled"),
                agenda_url=ev.get("url"), detail_url=ev.get("url"),
                needs_ai_extraction=bool(text.strip()) and not cancelled, agenda_text=text or None,
            ))
        return res
