from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from ..http import Fetcher
from ..models import CHI, SourceResult


class ImplausibleResult(Exception):
    """The source answered, but with something that cannot be right (e.g. zero meetings)."""


@dataclass(frozen=True)
class Window:
    start: dt.date
    end: dt.date

    def contains(self, d: dt.date) -> bool:
        return self.start <= d <= self.end


def window_for(now: dt.datetime, days: tuple[int, int]) -> Window:
    today = now.astimezone(CHI).date()
    return Window(today + dt.timedelta(days=days[0]), today + dt.timedelta(days=days[1]))


def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "x"


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def html_to_text(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True).replace("\xa0", " ")


_AMPM = re.compile(r"(\d{1,2})(?::(\d\d))?\s*([ap])\.?\s*m\.?", re.I)


def parse_ampm(text: str | None) -> dt.time | None:
    m = _AMPM.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return dt.time(hour, int(m.group(2) or 0))


class BaseAdapter:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.source_id: str = cfg["id"]
        self.days: tuple[int, int] = tuple(cfg.get("window_days", (-14, 60)))
        self.min_meetings: int = cfg.get("min_meetings", 0)

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        raise NotImplementedError
