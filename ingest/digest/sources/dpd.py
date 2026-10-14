"""Chicago Plan Commission and Community Development Commission (chicago.gov HTML pages)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from ..http import Fetcher
from ..models import CHI, Attachment, Geography, Item, Meeting, SourceResult
from .base import BaseAdapter, ImplausibleResult, clean_text, parse_ampm, slugify, window_for

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
DATE_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})\b", re.I)
YEAR_RE = re.compile(r"(20\d\d)\s+Meeting Schedule", re.I)
WARD_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+Ward", re.I)
CA_RE = re.compile(r"\(([^,()]+),\s*\d{1,2}(?:st|nd|rd|th)\s+Ward\)", re.I)
TIME_RE = re.compile(r"held at (\d{1,2}(?::\d\d)?\s*[ap]\.m\.)", re.I)


@dataclass
class ScheduleEntry:
    date: dt.date
    agenda_url: str | None
    cancelled: bool


@dataclass
class HearingRow:
    address: str
    ward: int | None
    community_area: str | None
    description: str
    attachments: list[Attachment] = field(default_factory=list)


def _segments(td: Tag) -> list[dict]:
    """Split a table cell into lines at <br> and <p>, keeping the links that fall in each line."""
    segs = [{"text": "", "links": []}]
    for el in td.descendants:
        if isinstance(el, Tag) and el.name in ("br", "p"):
            segs.append({"text": "", "links": []})
        elif isinstance(el, Tag) and el.name == "a" and el.get("href"):
            segs[-1]["links"].append((el.get_text(" ", strip=True), el["href"]))
        elif isinstance(el, NavigableString):
            segs[-1]["text"] += str(el)
    return [s for s in segs if s["text"].strip() or s["links"]]


def parse_schedule(html: str, base: str) -> list[ScheduleEntry]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[ScheduleEntry] = []
    for node in soup.find_all(string=YEAR_RE):
        year = int(YEAR_RE.search(node).group(1))
        table = node.find_next("table")
        if table is None:
            continue
        for td in table.find_all("td"):
            for seg in _segments(td):
                text = seg["text"].replace("\xa0", " ")
                m = DATE_RE.search(text)
                if not m:
                    if "cancel" in text.lower() and out and out[-1].date.year == year:
                        out[-1].cancelled = True
                    continue
                link = next(
                    (urljoin(base, h) for t, h in seg["links"]
                     if "agenda" in t.lower() or DATE_RE.search(t.replace("\xa0", " "))),
                    None,
                )
                out.append(ScheduleEntry(dt.date(year, MONTHS[m.group(1).lower()], int(m.group(2))),
                                         link, "cancel" in text.lower()))
    first: dict[dt.date, ScheduleEntry] = {}
    for e in out:
        first.setdefault(e.date, e)
    return list(first.values())


def parse_hearing(html: str, base: str) -> tuple[dt.time | None, list[HearingRow]]:
    soup = BeautifulSoup(html, "html.parser")
    tm = TIME_RE.search(clean_text(soup.get_text(" ", strip=True)))
    rows: list[HearingRow] = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs or "address" not in trs[0].get_text(" ", strip=True).lower():
            continue
        for tr in trs[1:]:
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            block = tds[0].get_text("\n", strip=True).replace("\xa0", " ")
            address = block.split("\n")[0].split("(")[0].strip().rstrip(",").strip()
            if not re.match(r"\d", address):
                address = ""
            w = WARD_RE.search(block)
            ca = CA_RE.search(block.replace("\n", " "))
            rows.append(HearingRow(
                address=address,
                ward=int(w.group(1)) if w else None,
                community_area=ca.group(1).strip() if ca else None,
                description=clean_text(tds[1].get_text(" ", strip=True)),
                attachments=[
                    Attachment(label=clean_text(a.get_text(" ", strip=True)), url=urljoin(base, a["href"]))
                    for td in tds[2:] for a in td.find_all("a", href=True)
                ],
            ))
    return (parse_ampm(tm.group(1)) if tm else None), rows


class DpdAdapter(BaseAdapter):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.listing_url: str = cfg["listing_url"]
        self.body: str = cfg["body"]
        h, mnt = cfg.get("default_time", "10:00").split(":")
        self.default_time = dt.time(int(h), int(mnt))

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        win = window_for(now, self.days)
        res = SourceResult()
        entries = [e for e in parse_schedule(fetcher.get_text(self.listing_url), self.listing_url)
                   if win.contains(e.date)]
        if not entries:
            raise ImplausibleResult(f"{self.source_id}: no meetings in window on the listing page")
        for e in entries:
            mid = f"{self.source_id}:{e.date.isoformat()}"
            t, rows = None, []
            if e.agenda_url and not e.cancelled:
                t, rows = parse_hearing(fetcher.get_text(e.agenda_url), e.agenda_url)
                if not rows:
                    res.warnings.append(f"{mid}: hearing page has no project table")
            start = dt.datetime.combine(e.date, t or self.default_time, tzinfo=CHI)
            m = Meeting(
                id=mid, source_id=self.source_id, body=self.body, body_id=self.source_id, start=start,
                time_known=t is not None,
                status="cancelled" if e.cancelled else ("held" if start < now else "scheduled"),
                agenda_url=e.agenda_url, detail_url=e.agenda_url or self.listing_url,
            )
            res.meetings.append(m)
            used: set[str] = set()
            res.items += [self._item(m, n, row, used) for n, row in enumerate(rows, 1)]
        return res

    def _item(self, m: Meeting, n: int, row: HearingRow, used: set[str]) -> Item:
        base = slugify(row.address) if row.address else f"item-{n}"
        ref, k = base, 1
        while ref in used:
            k += 1
            ref = f"{base}-{k}"
        used.add(ref)
        return Item(
            id=f"{m.id}#{ref}", meeting_id=m.id, source_ref=row.address or f"Item {n}",
            title=row.description, attachments=row.attachments,
            geography_hint=Geography(address=row.address or None, ward=row.ward, community_area=row.community_area),
        )
