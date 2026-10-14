"""IHDA's single WordPress page of meeting notices, agendas, minutes, and public notices."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..http import Fetcher
from ..models import CHI, Meeting, Notice, NoticeKind, SourceResult
from .base import BaseAdapter, ImplausibleResult, clean_text, parse_ampm, slugify, window_for

# Committees first: "Finance Committee of the Illinois Housing Development Authority" must not match the board.
BODY_PATTERNS = [
    ("finance committee", "IHDA Finance Committee"),
    ("audit committee", "IHDA Audit Committee"),
    ("asset management committee", "IHDA Asset Management Committee"),
    ("meeting of the members", "IHDA Board"),
    ("ihda board meeting", "IHDA Board"),
]
SKIP = ("meeting dates", "livestream")
TIME = r"\d{1,2}(?::\d\d)?\s*[ap]\.m\."
DT_RE = re.compile(rf"(?:({TIME})\s+on\s+\w+,\s+)?([A-Z][a-z]+ \d{{1,2}}, 20\d\d)(?:\s+at\s+({TIME}))?")


@dataclass
class IhdaMeeting:
    body: str
    date: dt.date
    time: dt.time | None = None
    agenda_url: str | None = None
    minutes_url: str | None = None
    notice_url: str | None = None


def _sections(soup: BeautifulSoup) -> list[tuple[str, list[Tag]]]:
    """Each h3 heading with the leaf <li> elements that follow it, up to the next h3."""
    out = []
    for h3 in soup.find_all("h3"):
        lis: list[Tag] = []
        node = h3.next_sibling
        while node is not None and getattr(node, "name", None) != "h3":
            if isinstance(node, Tag):
                lis += [node] if node.name == "li" else node.find_all("li")
            node = node.next_sibling
        out.append((clean_text(h3.get_text(" ", strip=True)).upper(), [li for li in lis if not li.find("li")]))
    return out


def _notice_kind(section: str) -> NoticeKind | None:
    if section.startswith("TEFRA"):
        return "hearing"
    if "PUBLIC NOTICES" in section:
        return "comment_period"
    if "SUBSIDY" in section:
        return "public_notice"
    return None


def _day(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%B %d, %Y").date()


def parse_page(html: str, base: str, source_id: str) -> tuple[list[IhdaMeeting], list[Notice]]:
    soup = BeautifulSoup(html, "html.parser")
    meetings: dict[tuple[str, dt.date], IhdaMeeting] = {}
    notices: list[Notice] = []
    for section, lis in _sections(soup):
        kind = _notice_kind(section)
        if section != "IHDA BOARD" and kind is None:
            continue
        for li in lis:
            a = li.find("a", href=True)
            if a is None:
                continue
            text = clean_text(li.get_text(" ", strip=True))
            low = text.lower()
            url = urljoin(base, a["href"])
            m = DT_RE.search(text)
            if section == "IHDA BOARD":
                body = next((b for k, b in BODY_PATTERNS if k in low), None)
                if body is None or m is None or any(s in low for s in SKIP):
                    continue
                day = _day(m.group(2))
                rec = meetings.setdefault((body, day), IhdaMeeting(body, day))
                rec.time = rec.time or parse_ampm(m.group(1) or m.group(3))
                if "minutes" in low:
                    rec.minutes_url = url
                elif "agenda" in low:
                    rec.agenda_url = url
                elif "notice" in low:
                    rec.notice_url = url
            else:
                deadline = None
                if kind == "hearing" and m:
                    t = parse_ampm(m.group(3) or m.group(1)) or dt.time(0, 0)
                    deadline = dt.datetime.combine(_day(m.group(2)), t, tzinfo=CHI)
                notices.append(Notice(
                    id=f"{source_id}:notice:{hashlib.sha1(url.encode()).hexdigest()[:12]}",
                    source_id=source_id, body="IHDA", kind=kind, title=text, url=url, deadline=deadline,
                ))
    return list(meetings.values()), notices


class IhdaAdapter(BaseAdapter):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.url: str = cfg["url"]

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        win = window_for(now, self.days)
        raw, notices = parse_page(fetcher.get_text(self.url), self.url, self.source_id)
        if not raw:
            raise ImplausibleResult(f"{self.source_id}: no meetings parsed from the page")
        res = SourceResult(notices=notices)
        for r in raw:
            if not win.contains(r.date):
                continue
            start = dt.datetime.combine(r.date, r.time or dt.time(0, 0), tzinfo=CHI)
            res.meetings.append(Meeting(
                id=f"{self.source_id}:{slugify(r.body)}:{r.date.isoformat()}", source_id=self.source_id,
                body=r.body, body_id=slugify(r.body), jurisdiction="state", start=start,
                time_known=r.time is not None, status="held" if start < now else "scheduled",
                agenda_url=r.agenda_url, minutes_url=r.minutes_url, detail_url=self.url,
                needs_ai_extraction=bool(r.agenda_url),
            ))
        return res
