"""How the meeting page is laid out: color families, type groups, cards vs. table rows, headlines and fact chips.

Everything here is computed when the page is built and never stored, so a changed rule, threshold or color needs no
AI re-run. The outline it builds is what the page template renders, and what the email will render later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .enrich import title_address
from .models import Item
from .text import short_title

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
                   address_groups=set(raw.get("address_groups") or []), name_groups=set(raw.get("name_groups") or []))


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


_APPOINTEE = re.compile(r"^Appointment of (.+?) as\b")


def headline(layout: Layout, item: Item, group: str | None) -> str:
    """The site address for land sales and rezonings, the person for appointments; otherwise the (shortened) title."""
    if group in layout.name_groups and (m := _APPOINTEE.match(item.title)):
        return m.group(1).strip()
    if group in layout.address_groups:
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
    """Fact chips in display order: ward, homes, dollars, zoning change, buyer or applicant. Only known facts."""
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
    party = next((x for x in e.entities if not _PUBLIC.search(x) and x.casefold() not in head.casefold()), None)
    if party:
        out.append((Seg(party),))
    return out
