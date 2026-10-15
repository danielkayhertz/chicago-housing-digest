"""Static page + per-body .ics feeds from data/items.json: python -m digest.site"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .categories import Categories
from .models import CHI, Digest, Item, Meeting, load_digest
from .sources.base import slugify
from .state import is_shown
from .text import LONG_TITLE, safe_url, short_title, trim_summary  # noqa: F401  (tests import these from here)

ROOT = Path(__file__).resolve().parents[2]
UID_DOMAIN = "housingdigest.danielkayhertz.com"
OUTCOME_DAYS = 14
VTIMEZONE = [
    "BEGIN:VTIMEZONE", "TZID:America/Chicago",
    "BEGIN:STANDARD", "DTSTART:19701101T020000", "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
    "TZOFFSETFROM:-0500", "TZOFFSETTO:-0600", "TZNAME:CST", "END:STANDARD",
    "BEGIN:DAYLIGHT", "DTSTART:19700308T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
    "TZOFFSETFROM:-0600", "TZOFFSETTO:-0500", "TZNAME:CDT", "END:DAYLIGHT",
    "END:VTIMEZONE",
]


def _ics_url(url: str | None) -> str | None:
    url = safe_url(url)
    return url.replace("\r", "").replace("\n", "") if url else None


def ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets, never splitting a UTF-8 character."""
    data = line.encode("utf-8")
    parts = []
    while len(data) > 75:
        cut = 75
        while data[cut] & 0xC0 == 0x80:
            cut -= 1
        parts.append(data[:cut].decode("utf-8"))
        data = b" " + data[cut:]
    parts.append(data.decode("utf-8"))
    return "\r\n".join(parts)


def _event(m: Meeting, now: dt.datetime) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{slugify(m.id, 120)}@{UID_DOMAIN}",
             f"DTSTAMP:{now.astimezone(dt.timezone.utc):%Y%m%dT%H%M%SZ}"]
    if m.time_known:
        lines += [f"DTSTART;TZID=America/Chicago:{m.start:%Y%m%dT%H%M%S}", "DURATION:PT2H"]
    else:
        lines.append(f"DTSTART;VALUE=DATE:{m.start:%Y%m%d}")
    lines.append(f"SUMMARY:{ics_escape(m.body + (' (cancelled)' if m.status == 'cancelled' else ''))}")
    if m.location:
        lines.append(f"LOCATION:{ics_escape(m.location)}")
    agenda = _ics_url(m.agenda_url)
    url = _ics_url(m.detail_url) or agenda
    if url:
        lines.append(f"URL:{url}")
    desc = []
    if agenda:
        desc.append(f"Agenda: {agenda}")
    elif not m.agenda_url:
        desc.append("Agenda not posted yet.")
    if m.item_count_total:
        desc.append(f"{m.item_count_relevant} of {m.item_count_total} items are about housing.")
    lines += [f"DESCRIPTION:{ics_escape(chr(10).join(desc))}",
              f"STATUS:{'CANCELLED' if m.status in ('cancelled', 'unknown') else 'CONFIRMED'}", "END:VEVENT"]
    return lines


def ics_calendar(meetings: list[Meeting], name: str, now: dt.datetime) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Chicago Housing Digest//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{ics_escape(name)}", *VTIMEZONE]
    for m in meetings:
        lines += _event(m, now)
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


def build_site(digest: Digest, out_dir: Path, items_json: Path, categories: Categories, now: dt.datetime) -> None:
    today = now.astimezone(CHI).date()
    items_by_meeting: dict[str, list[Item]] = defaultdict(list)
    for it in digest.items:
        items_by_meeting[it.meeting_id].append(it)
    meetings_by_id = {m.id: m for m in digest.meetings}

    cutoff = today - dt.timedelta(days=OUTCOME_DAYS)
    upcoming, recent = [], []
    for m in digest.meetings:
        day = m.start.astimezone(CHI).date()
        if day < cutoff or m.status == "unknown":
            continue  # unknown: moved or dropped by its source; not worth showing
        its = items_by_meeting.get(m.id, [])
        visible = [i for i in its if is_shown(i)]
        (upcoming if day >= today else recent).append({"m": m, "items": visible, "hidden": len(its) - len(visible)})
    recent.sort(key=lambda u: u["m"].start, reverse=True)
    notices = [n for n in digest.notices
               if (n.deadline is None or n.deadline >= now)
               and is_shown(n)]
    outcomes = sorted(
        ({"i": i, "m": meetings_by_id.get(i.meeting_id)} for i in digest.items
         if i.outcome and i.outcome.date >= cutoff and is_shown(i)),
        key=lambda r: r["i"].outcome.date, reverse=True,
    )
    bodies = sorted({m.body for m in digest.meetings})

    env = Environment(loader=PackageLoader("digest", "templates"), autoescape=select_autoescape(["html", "j2"]))
    env.filters["anchor"] = lambda s: slugify(s, 120)
    env.filters["safe_url"] = safe_url
    env.filters["short_title"] = short_title
    env.filters["trim_summary"] = trim_summary
    env.tests["safe_url"] = lambda u: safe_url(u) is not None
    env.globals["LONG_TITLE"] = LONG_TITLE
    html = env.get_template("index.html.j2").render(
        digest=digest, upcoming=upcoming, recent=recent, notices=notices, outcomes=outcomes, bodies=bodies,
        categories=categories.items, cat_labels={c.id: c.label for c in categories.items},
        ics={b: f"ics/{slugify(b)}.ics" for b in bodies}, generated=digest.generated_at.astimezone(CHI),
    )
    (out_dir / "ics").mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(items_json, out_dir / "items.json")
    by_body: dict[str, list[Meeting]] = defaultdict(list)
    for m in digest.meetings:
        by_body[m.body].append(m)
    for body, ms in by_body.items():
        (out_dir / "ics" / f"{slugify(body)}.ics").write_bytes(ics_calendar(ms, body, now).encode("utf-8"))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m digest.site")
    p.add_argument("--root", type=Path, default=ROOT)
    args = p.parse_args(argv)
    src = args.root / "data" / "items.json"
    digest = load_digest(src)
    if digest is None:
        raise SystemExit(f"{src} not found; run python -m digest.run first")
    out = args.root / "site"
    build_site(digest, out, src, Categories(args.root / "config" / "categories.yaml"), dt.datetime.now(CHI))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
