"""How the meeting page is laid out: color families, type groups, cards vs. table rows, headlines and fact chips.

Everything here is computed when the page is built and never stored, so a changed rule, threshold or color needs no
AI re-run. The outline it builds is what the page template renders, and what the email will render later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .enrich import title_address
from .models import CHI, Attachment, Item, Meeting
from .sources.base import slugify
from .text import safe_url, short_title, trim_summary

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "layout.yaml"
OTHER = "Other"


@dataclass(frozen=True)
class Family:
    id: str
    label: str


@dataclass(frozen=True)
class Rule:
    label: str
    prefixes: tuple[str, ...]
    pattern: re.Pattern | None


@dataclass
class Layout:
    families: dict[str, Family]  # body -> family
    grouping: dict[str, list[Rule]]  # body -> ordered rules; bodies not here are flat
    always_card: set[str]
    min_units: int
    min_amount: int
    address_groups: set[str]
    name_groups: set[str]
    address_bodies: set[str] = field(default_factory=set)  # flat bodies whose headline is the address
    important_groups: set[str] = field(default_factory=set)
    important_categories: set[str] = field(default_factory=set)
    outcome_sources: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "Layout":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        families = {body: Family(fid, f["label"])
                    for fid, f in (raw.get("families") or {}).items() for body in f.get("bodies") or []}
        grouping = {body: [Rule(r["label"], tuple(r.get("title_prefix") or ()),
                                re.compile(r["pattern"]) if r.get("pattern") else None) for r in rules or []]
                    for body, rules in (raw.get("grouping") or {}).items()}
        return cls(families=families, grouping=grouping, always_card=set(raw.get("always_card_groups") or []),
                   min_units=int(raw.get("card_min_units", 10)), min_amount=int(raw.get("card_min_amount", 1_000_000)),
                   address_groups=set(raw.get("address_groups") or []), name_groups=set(raw.get("name_groups") or []),
                   address_bodies=set(raw.get("address_bodies") or []),
                   important_groups=set(raw.get("important_groups") or []),
                   important_categories=set(raw.get("important_categories") or []),
                   outcome_sources=set(raw.get("outcome_sources") or []))


@dataclass(frozen=True)
class Seg:
    """One run of a fact chip's text; numbers are bold."""
    text: str
    bold: bool = False


def fact_text(fact: tuple[Seg, ...]) -> str:
    return "".join(s.text for s in fact)


def group_for(layout: Layout, body: str, title: str, summary: str) -> str | None:
    """The item's type group in a grouped body, Other when no rule matches; None when the body is flat."""
    if body not in layout.grouping:
        return None
    for r in layout.grouping[body]:
        if r.prefixes and not title.startswith(r.prefixes):
            continue
        if r.pattern and not r.pattern.search(f"{title}\n{summary}"):
            continue
        return r.label
    return OTHER


def is_card(layout: Layout, group: str | None, units: int | None, amount: int | None) -> bool:
    if group is None or group in layout.always_card:
        return True
    return (units or 0) >= layout.min_units or (amount or 0) >= layout.min_amount


def important(layout: Layout, item: Item, group: str | None) -> bool:
    """A hand flag wins; otherwise big items, the listed groups, and the listed categories (not body-wide tags)."""
    e = item.enrichment
    if e is not None and e.important is not None:
        return e.important
    if group in layout.important_groups:
        return True
    if e is None:
        return False
    if (e.figures.units or 0) >= layout.min_units or (e.amount or 0) >= layout.min_amount:
        return True
    return any(c.id in layout.important_categories and c.via != "body" for c in e.categories)


_APPOINTEE = re.compile(r"^Appointment of (.+?) as\b")


def headline(layout: Layout, item: Item, group: str | None, body: str = "") -> str:
    """The site address for land sales and rezonings, the person for appointments; otherwise the (shortened) title."""
    if group in layout.name_groups and (m := _APPOINTEE.match(item.title)):
        return m.group(1).strip()
    if group in layout.address_groups or (group is None and body in layout.address_bodies):
        e = item.enrichment
        addr = (e.geography.address if e else None) or title_address(item.title)
        if addr:
            return addr
    return short_title(item.title)


def format_amount(n: int) -> str:
    return f"${n / 1_000_000:.1f}M" if n >= 1_000_000 else f"${n:,}"


# Entities that are public bodies or officials, never the buyer or applicant.
_PUBLIC = re.compile(r"\b(?:City|Council|Committee|Department|Dept|Authority|Commission|Board|Mayor|Alderman"
                     r"|Alderperson|Ald|IHDA|State of Illinois|County)\b", re.I)


def facts(item: Item, head: str) -> list[tuple[Seg, ...]]:
    """Fact chips in display order: ward, homes, dollars, zoning change, buyer or applicant. Only known facts.

    The buyer or applicant must be named in the agenda title: entities found only in documents are as often the
    sponsoring alderperson, a presenter or the appointing mayor.
    """
    e = item.enrichment
    if e is None:
        return []
    out: list[tuple[Seg, ...]] = []
    if e.geography.ward:
        out.append((Seg("Ward "), Seg(str(e.geography.ward), True)))
    units, affordable = e.figures.units, e.figures.affordable_units
    if units:
        f = [Seg(str(units), True), Seg(" home" if units == 1 else " homes")]
        if affordable:
            f += [Seg(" · "), Seg(str(affordable), True), Seg(" affordable")]
        out.append(tuple(f))
    if e.amount:
        out.append((Seg(format_amount(e.amount), True),) + ((Seg(f" {e.amount_kind}"),) if e.amount_kind else ()))
    if e.zoning_to:
        lead = ", ".join(e.zoning_from) + " → " if e.zoning_from else "to "
        out.append((Seg(lead), Seg(e.zoning_to, True)))
    title = item.title.casefold()
    party = next((x for x in e.entities if not _PUBLIC.search(x) and x.casefold() in title
                  and x.casefold() not in head.casefold()), None)
    if party:
        out.append((Seg(party),))
    return out


@dataclass
class ItemView:
    item: Item
    anchor: str
    group: str | None
    headline: str
    full_title: str | None  # the agenda title, when the headline is not it
    facts: list[tuple[Seg, ...]]
    card: bool
    summary: str
    docs: list[Attachment]
    categories: str  # space-separated category ids, for the filters
    ward: str
    important: bool = False

    @property
    def facts_text(self) -> str:
        return " · ".join(fact_text(f) for f in self.facts)


@dataclass
class GroupView:
    label: str
    cards: list[ItemView] = field(default_factory=list)
    rows: list[ItemView] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.cards) + len(self.rows)


@dataclass
class MeetingView:
    meeting: Meeting
    anchor: str
    family: Family | None
    when: str
    groups: list[GroupView] | None  # None: a flat body, see cards
    cards: list[ItemView]
    count: int
    hidden: int
    ics: str
    outcomes: bool = False  # a past meeting of a body that publishes outcomes
    tally: str = ""  # "2 recommended to pass · 1 not yet posted"


@dataclass
class DayView:
    id: str
    label: str
    meetings: list[MeetingView]


@dataclass
class IndexEntry:
    anchor: str
    date_label: str
    family: Family | None
    body: str
    count: int
    recent: bool = False


@dataclass
class Outline:
    index: list[IndexEntry]
    days: list[DayView]
    has_upcoming: bool


Shown = tuple[Meeting, list[Item], int]  # a meeting, its shown items in agenda order, how many were hidden


def item_view(layout: Layout, body: str, item: Item) -> ItemView:
    e = item.enrichment
    summary = trim_summary(e.summary) if e and e.summary else ""
    group = group_for(layout, body, item.title, summary)
    head = headline(layout, item, group, body)
    return ItemView(
        item=item, anchor=slugify(item.id, 120), group=group, headline=head,
        full_title=item.title if head != item.title else None, facts=facts(item, head),
        card=is_card(layout, group, e.figures.units if e else None, e.amount if e else None), summary=summary,
        docs=[a for a in item.attachments[:3] if safe_url(a.url)],
        categories=" ".join(c.id for c in e.categories) if e else "",
        ward=str(e.geography.ward) if e and e.geography.ward else "",
        important=important(layout, item, group),
    )


def _when(m: Meeting, with_date: bool) -> str:
    start = m.start.astimezone(CHI)
    time = f"{start:%I:%M %p}".lstrip("0") if m.time_known else ""
    if not with_date:
        return time
    date = f"{start:%A, %B} {start.day}"
    return f"{date}, {time}" if time else date


def _tally(items: list[Item]) -> str:
    counts: dict[str, int] = {}
    for i in items:
        key = i.outcome.action.lower() if i.outcome else ""
        counts[key] = counts.get(key, 0) + 1
    pending = counts.pop("", 0)
    parts = [f"{n} {action}" for action, n in counts.items()]
    return " · ".join(parts + ([f"{pending} no outcome posted yet"] if pending else []))


def meeting_view(layout: Layout, shown: Shown, ics: dict[str, str], with_date: bool) -> MeetingView:
    m, items, hidden = shown
    outcomes = with_date and m.source_id in layout.outcome_sources
    views = [item_view(layout, m.body, i) for i in items]
    groups = None
    if m.body in layout.grouping:
        labels = list(dict.fromkeys([r.label for r in layout.grouping[m.body]] + [OTHER]))
        groups = []
        for label in labels:
            vs = [v for v in views if v.group == label]
            if vs:
                groups.append(GroupView(label, [v for v in vs if v.card], [v for v in vs if not v.card]))
    return MeetingView(meeting=m, anchor=slugify(m.id, 120), family=layout.families.get(m.body),
                       when=_when(m, with_date), groups=groups, cards=views if groups is None else [],
                       count=len(views), hidden=hidden, ics=ics.get(m.body, ""), outcomes=outcomes,
                       tally=_tally(items) if outcomes and items else "")


def build_outline(upcoming: list[Shown], recent: list[Shown], layout: Layout, ics: dict[str, str]) -> Outline:
    """The page from the top: the index, then one day per upcoming date, then every recent meeting under Recent."""
    by_day: dict = {}
    for s in sorted(upcoming, key=lambda s: s[0].start):
        by_day.setdefault(s[0].start.astimezone(CHI).date(), []).append(meeting_view(layout, s, ics, False))
    days = [DayView(f"day-{d.isoformat()}", f"{d:%A, %B} {d.day}", mvs) for d, mvs in by_day.items()]
    if recent:
        days.append(DayView("recent", "Recent", [meeting_view(layout, s, ics, True)
                                                 for s in sorted(recent, key=lambda s: s[0].start, reverse=True)]))
    index = []
    for day in days:
        for mv in day.meetings:
            start = mv.meeting.start.astimezone(CHI)
            index.append(IndexEntry(mv.anchor, f"{start:%a %b} {start.day}", mv.family, mv.meeting.body, mv.count,
                                    day.id == "recent"))
    return Outline(index=index, days=days, has_upcoming=bool(by_day))
