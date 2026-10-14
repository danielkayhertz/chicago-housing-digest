"""City Council committees via the Clerk's eLMS API. Agenda items come from the agenda PDF's record numbers."""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

from ..http import Fetcher, NotFound
from ..models import CHI, Attachment, Item, Meeting, MeetingStatus, Outcome, SourceResult
from ..pdftext import has_text_layer, pdf_text
from .base import BaseAdapter, window_for

ELMS = "https://api.chicityclerkelms.chicago.gov"
# Two agenda layouts: "DOCUMENT # O2026-0025391" headers (Zoning), and numbered lines that open with the
# record in parentheses, "4. (O2026-0029008) Sale of City-owned property ..." (Housing, Finance).
_REC = r"[A-Z][A-Za-z]{0,2}\d{4}-\d{4,8}"
RECORD_RE = re.compile(rf"DOCUMENT\s*#\s*({_REC})|^[ \t]*\d+\.[ \t]*\(({_REC})\)", re.MULTILINE)
STATUS_WORDS: dict[str, MeetingStatus] = {
    "cancel": "cancelled", "reschedul": "rescheduled",
    "recess": "held", "adjourn": "held", "reconven": "held",
}


def _local(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso).astimezone(CHI)


def map_status(raw: str | None, start: dt.datetime, now: dt.datetime) -> MeetingStatus:
    s = (raw or "").lower()
    for word, status in STATUS_WORDS.items():
        if word in s:
            return status
    return "held" if start < now else "scheduled"


def agenda_file(files: list[dict] | None) -> dict | None:
    """The newest agenda: highest sort among Agenda attachments (Amended Agenda outranks Agenda)."""
    agendas = [f for f in files or [] if f.get("attachmentType") == "Agenda" and f.get("path")]
    return max(agendas, key=lambda f: f.get("sort") or 0) if agendas else None


def _block_title(block: str) -> str:
    groups: list[list[str]] = []
    cur: list[str] = []
    for line in block.splitlines()[1:]:  # line 0 is the rest of the "DOCUMENT #" header
        if line.strip():
            cur.append(line.strip())
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    keep = [
        g for g in groups
        if not g[0].startswith(("Sponsor", "Co-Sponsor"))
        and not " ".join(g).isupper()
        and not " ".join(g).isdigit()
    ]
    return " ".join(keep[0]) if keep else ""


def _inline_title(block: str) -> str:
    """Numbered layout: the title starts on the record's line and runs to a blank line or a "(21st Ward)" line."""
    lines: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line or (lines and line.startswith("(")):
            break
        lines.append(line)
    return " ".join(lines)


def parse_agenda(text: str) -> list[tuple[str, str]]:
    """(record number, title as printed) in agenda order, first occurrence only."""
    matches = list(RECORD_RE.finditer(text))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, m in enumerate(matches):
        rec = m.group(1) or m.group(2)
        if rec in seen:
            continue
        seen.add(rec)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end():end]
        out.append((rec, _block_title(block) if m.group(1) else _inline_title(block)))
    return out


def _tally(votes: list[dict]) -> str | None:
    if not votes:
        return None
    counts = Counter(v.get("vote") for v in votes)
    return ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))


def committee_outcome(matter: dict, body: str, day: dt.date) -> Outcome | None:
    """Committee actions often have an empty meetingId, so join on (acting body, Chicago date)."""
    for a in matter.get("actions") or []:
        if a.get("actionByName") == body and a.get("actionDate") and _local(a["actionDate"]).date() == day:
            return Outcome(action=a["actionName"], date=day, tally=_tally(a.get("votes") or []))
    return None


class ElmsAdapter(BaseAdapter):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.body: str = cfg["body"]
        self.display: str = cfg.get("display_body", self.body)
        self.page_size: int = cfg.get("page_size", 20)

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        win = window_for(now, self.days)
        res = SourceResult(window_start=win.start, window_end=win.end)
        for raw in self._meetings(fetcher, win):
            m = self._meeting(raw, now)
            res.meetings.append(m)
            if m.status == "cancelled" or not m.agenda_url:
                continue
            items, warnings = self._items(fetcher, m)
            res.items += items
            res.warnings += warnings
        return res

    def _meetings(self, fetcher: Fetcher, win) -> list[dict]:
        out = []
        for page in range(10):  # newest (furthest future) first
            data = fetcher.get_json(f"{ELMS}/meeting", {
                "filter": f"body eq '{self.body}'", "top": self.page_size, "skip": page * self.page_size,
            })
            rows = data.get("data") or []
            out += [r for r in rows if win.contains(_local(r["date"]).date())]
            if len(rows) < self.page_size or _local(rows[-1]["date"]).date() < win.start:
                break
        return out

    def _meeting(self, r: dict, now: dt.datetime) -> Meeting:
        start = _local(r["date"])
        af = agenda_file(r.get("files"))
        return Meeting(
            id=f"elms:{r['meetingId']}", source_id=self.source_id, body=self.display,
            body_id=r.get("bodyId") or self.body, start=start,
            status=map_status(r.get("status"), start, now),
            location=r.get("location") or None, agenda_url=af["path"] if af else None,
            video_url=r.get("videoLink") or None,
        )

    def _items(self, fetcher: Fetcher, m: Meeting) -> tuple[list[Item], list[str]]:
        text = pdf_text(fetcher.get_bytes(m.agenda_url))
        records = parse_agenda(text)
        if not records:
            if has_text_layer(text):
                m.needs_ai_extraction = True  # an unfamiliar layout: run.py's _extract lists the items instead
                return [], []
            return [], [f"{m.id}: agenda has no text layer"]
        return [self._item(m, rec, title, self._matter(fetcher, rec)) for rec, title in records], []

    def _matter(self, fetcher: Fetcher, rec: str) -> dict | None:
        # A substituted record (O… → SO…) can 404 under its original number.
        candidates = (rec, "S" + rec) if rec[0] in "OR" else (rec,)
        for candidate in candidates:
            try:
                return fetcher.get_json(f"{ELMS}/matter/recordNumber/{candidate}")
            except NotFound:
                continue
        return None

    def _item(self, m: Meeting, rec: str, agenda_title: str, matter: dict | None) -> Item:
        item_id = f"{m.id}#{rec}"
        if matter is None:
            return Item(id=item_id, meeting_id=m.id, source_ref=rec, title=agenda_title or rec)
        attachments = [
            Attachment(label=a.get("fileName", "").removesuffix(".pdf") or a.get("attachmentType") or "Attachment",
                       url=a["path"])
            for a in matter.get("attachments") or [] if a.get("path")
        ]
        return Item(
            id=item_id, meeting_id=m.id, source_ref=matter.get("recordNumber") or rec,
            title=matter.get("title") or agenda_title or rec, attachments=attachments,
            outcome=committee_outcome(matter, self.body, m.start.date()),
        )
