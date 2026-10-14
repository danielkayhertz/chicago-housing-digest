"""One shared enrichment per item/notice: relevance screen, summary, grounding checks, categories, geography."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from .cache import JsonCache
from .categories import Categories, merge_hits
from .http import FetchError, Fetcher
from .models import CategoryHit, Digest, Enrichment, Figures, Geography, Item, Meeting, Notice
from .pdftext import has_text_layer, pdf_text
from .sources.base import html_to_text

PROMPT_VERSION = "1"  # bump to re-enrich everything after a prompt change
MAX_FAILED_ATTEMPTS = 3  # a record that keeps failing is retried on this many runs, then left failed
log = logging.getLogger("digest.enrich")


class Screen(BaseModel):
    housing_relevant: bool
    reason: str


class Summary(BaseModel):
    housing_relevant: bool
    relevance_reason: str
    summary: str
    categories: list[str]
    address: str | None
    entities: list[str]
    units: int | None
    affordable_units: int | None


SCREEN_SYSTEM = """You triage Chicago City Council committee agenda items for a digest about housing.

An item is housing_relevant if it bears on homes: building, financing, preserving, regulating, taxing, or demolishing them, or the rights of the people who live in them. Zoning changes that add or remove homes count, as do city land sales for housing and housing-related tax incentives.
Signs, driveways, sidewalk cafes, honorary street names, business licenses, and purely commercial or industrial projects do not.
When you cannot tell, mark the item relevant. Give a one-sentence reason."""

SUMMARY_SYSTEM = """You write one entry in an email digest of Chicago housing public meetings, read by housing advocates and researchers.

You get one agenda item or public notice: the body that will act on it, its title, and whatever document text could be retrieved.

housing_relevant: whether it bears on homes: building, financing, preserving, regulating, taxing, or demolishing them, or the rights of the people who live in them. Zoning changes that add or remove homes count. Signs, business licenses, and commercial-only projects do not.
relevance_reason: one sentence.
summary: one to three plain sentences, at most about 40 words, on what the item would do and who is behind it, using only facts in the provided material. Do not speculate about effects. If the material says little more than the title, restate the title plainly.
categories: the ids below that the item clearly bears on. An empty list is fine.
{categories}
address: the street address of the site, copied as written, or null.
entities: developers, applicants, owners, lenders, and agencies named in the material, copied exactly as written.
units, affordable_units: counts of homes stated in the material, or null. Never estimate."""


@dataclass
class Record:
    id: str
    body: str
    title: str
    context: str
    urls: list[str]
    extra_text: str = ""
    hint: Geography | None = None


def record_for_item(item: Item, meeting: Meeting | None) -> Record:
    body = meeting.body if meeting else ""
    when = f"{meeting.start:%B} {meeting.start.day}, {meeting.start.year}" if meeting else "unknown date"
    return Record(id=item.id, body=body, title=item.title, context=f"{body}, {when}; agenda reference {item.source_ref}",
                  urls=[a.url for a in item.attachments], hint=item.geography_hint)


def record_for_notice(n: Notice) -> Record:
    urls = [] if n.kind == "page_change" and not n.url.lower().endswith(".pdf") else [n.url]
    deadline = f"; deadline {n.deadline:%B %d, %Y}" if n.deadline else ""
    return Record(id=n.id, body=n.body, title=n.title, context=f"{n.body} public notice ({n.kind}){deadline}",
                  urls=urls, extra_text=n.detail)


@dataclass
class EnrichContext:
    llm: object
    fetcher: Fetcher
    categories: Categories
    geo: object | None
    cache: JsonCache
    overrides: dict
    screen_bodies: set[str]
    models: dict[str, str]
    char_cap: int = 12000
    max_attachments: int = 2


def content_hash(title: str, sources: list[str], fingerprint: str) -> str:
    raw = json.dumps([PROMPT_VERSION, title, sources, fingerprint], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def gather_sources(fetcher: Fetcher, urls: list[str], char_cap: int, max_attachments: int) -> tuple[str, list[dict], str]:
    if not urls:
        return "", [], "none"
    texts: list[str] = []
    docs: list[dict] = []
    quality = "full"
    for url in urls[:max_attachments]:
        try:
            data = fetcher.get_bytes(url)
        except FetchError:
            quality = "partial"
            continue
        if data[:5] == b"%PDF-":
            text = pdf_text(data)
            if has_text_layer(text):
                texts.append(text)
            else:
                docs.append({"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf", "data": base64.b64encode(data).decode("ascii")}})
                quality = "scanned" if quality == "full" else quality
        else:
            texts.append(html_to_text(data.decode("utf-8", errors="replace")))
    joined = "\n\n---\n\n".join(t for t in texts if t.strip())
    if len(joined) > char_cap:
        joined = joined[:char_cap]
        quality = "partial" if quality == "full" else quality
    return joined, docs, quality


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _numbers(corpus: str) -> set[str]:
    return {tok.replace(",", "") for tok in re.findall(r"\d[\d,]*", corpus)}


def _grounded_number(n: int | None, numbers: set[str]) -> int | None:
    return n if n is not None and str(n) in numbers else None


def _grounded_address(address: str | None, corpus: str) -> str | None:
    if not address:
        return None
    tokens = re.findall(r"[a-z0-9]+", _norm(address))
    if not any(t.isdigit() for t in tokens):
        return None
    return address if all(t in corpus for t in tokens if len(t) >= 3) else None


def resolve_geography(hint: Geography | None, address: str | None, geo) -> Geography:
    if hint and hint.ward and hint.community_area:
        return hint
    addr = (hint.address if hint and hint.address else None) or address
    if not addr or geo is None:
        return hint or Geography()
    g = geo.locate(addr)
    if hint:
        g = Geography(address=hint.address or g.address, ward=hint.ward or g.ward,
                      community_area=hint.community_area or g.community_area)
    return g


def apply_overrides(e: Enrichment, ov: dict | None) -> Enrichment:
    if not ov:
        return e
    e = e.model_copy(deep=True)
    for f in ("housing_relevant", "summary", "relevance_reason"):
        if f in ov:
            setattr(e, f, ov[f])
    if "categories" in ov:
        e.categories = [CategoryHit(id=c, via="override") for c in ov["categories"]]
    if "geography" in ov:
        e.geography = Geography(**{**e.geography.model_dump(), **ov["geography"]})
    if e.status == "failed" and ov.get("summary"):
        e.status = "ok"
    if e.status == "screened_out" and ov.get("housing_relevant"):
        e.status = "ok"
    e.overridden = True
    return e


def _prompt(rec: Record, text: str) -> str:
    parts = [f"Body: {rec.body}", f"Context: {rec.context}", f"Title: {rec.title}"]
    if rec.extra_text:
        parts.append(f"Notice text:\n{rec.extra_text}")
    parts.append(f"Document text:\n{text}" if text else "No document text was available.")
    return "\n\n".join(parts)


def _fresh(rec: Record, ctx: EnrichContext, h: str) -> Enrichment:
    rules = ctx.categories.rule_hits(rec.body, [rec.title, rec.extra_text])
    if rec.body in ctx.screen_bodies:
        sc = ctx.llm.parse(purpose="screen", model_key="screen", system=SCREEN_SYSTEM,
                           content=f"Committee: {rec.body}\nItem: {rec.title}", schema=Screen, max_tokens=8000)
        if not sc.housing_relevant:
            return Enrichment(status="screened_out", relevance_reason=sc.reason, categories=rules,
                              geography=rec.hint or Geography(), model=ctx.models["screen"], content_hash=h)
    text, docs, quality = gather_sources(ctx.fetcher, rec.urls, ctx.char_cap, ctx.max_attachments)
    s = ctx.llm.parse(purpose="summarize", model_key="summarize",
                      system=SUMMARY_SYSTEM.format(categories=ctx.categories.prompt_block()),
                      content=[*docs, {"type": "text", "text": _prompt(rec, text)}], schema=Summary, max_tokens=16000)
    corpus = _norm("\n".join([rec.title, rec.extra_text, text]))
    numbers = _numbers(corpus)
    entities = [e.strip() for e in s.entities if e.strip() and _norm(e) in corpus]
    dropped = len(s.entities) - len(entities)
    if dropped:
        log.info("%s: dropped %d ungrounded entities", rec.id, dropped)
    figures = {}
    for name in ("units", "affordable_units"):
        value = getattr(s, name)
        figures[name] = _grounded_number(value, numbers)
        if value is not None and figures[name] is None:
            log.info("%s: dropped ungrounded figure %s=%s", rec.id, name, value)
    late_rules = ctx.categories.rule_hits(None, [s.summary, *entities])  # keywords also match summary and entities
    model_hits = [CategoryHit(id=c, via="model") for c in s.categories if c in ctx.categories.by_id]
    return Enrichment(
        status="ok", housing_relevant=s.housing_relevant, relevance_reason=s.relevance_reason, summary=s.summary,
        categories=merge_hits(rules, late_rules, model_hits),
        geography=resolve_geography(rec.hint, _grounded_address(s.address, corpus), ctx.geo),
        entities=entities,
        figures=Figures(**figures),
        source_quality=quality, model=ctx.models["summarize"], content_hash=h,
    )


def _failed(rec: Record, ctx: EnrichContext, h: str, reason: str) -> Enrichment:
    """A failed enrichment keeps the rule-based category hits so the record still shows up sensibly."""
    return Enrichment(status="failed", relevance_reason=reason[:300],
                      categories=ctx.categories.rule_hits(rec.body, [rec.title, rec.extra_text]),
                      geography=rec.hint or Geography(), model=ctx.models["summarize"], content_hash=h)


def enrich_record(rec: Record, ctx: EnrichContext) -> Enrichment:
    h = content_hash(rec.title, rec.urls + [rec.extra_text], ctx.categories.fingerprint())
    cached = ctx.cache.get(rec.id)
    e = None
    attempts = 0
    if isinstance(cached, dict) and cached.get("content_hash") == h:
        if "failed_attempts" in cached:  # failure marker, never a valid Enrichment
            n = cached["failed_attempts"]
            attempts = n if isinstance(n, int) else 0
        else:
            try:
                e = Enrichment.model_validate(cached)
            except ValidationError as ex:  # stale or corrupt entry: treat as a miss
                log.warning("%s: ignoring invalid enrich cache entry: %s", rec.id, ex)
            if e is not None and e.geography.address and e.geography.ward is None:
                # The geocoder may have failed transiently last time; the geocode cache makes a clean no-match free.
                geography = resolve_geography(rec.hint, e.geography.address, ctx.geo)
                if geography != e.geography:
                    e.geography = geography
                    ctx.cache.set(rec.id, e.model_dump(mode="json"))
    if e is None:
        if attempts >= MAX_FAILED_ATTEMPTS:
            e = _failed(rec, ctx, h, f"enrichment failed {attempts} times; not retrying until the content changes")
        else:
            try:
                e = _fresh(rec, ctx, h)
            except Exception as ex:  # one bad record must never abort the run
                log.warning("%s: enrichment failed: %s: %s", rec.id, type(ex).__name__, ex)
                e = _failed(rec, ctx, h, f"enrichment failed: {ex}")
                ctx.cache.set(rec.id, {"content_hash": h, "failed_attempts": attempts + 1})
            else:
                ctx.cache.set(rec.id, e.model_dump(mode="json"))
    return apply_overrides(e, ctx.overrides.get(rec.id))


def enrich_digest(digest: Digest, ctx: EnrichContext) -> Digest:
    meetings = {m.id: m for m in digest.meetings}
    for item in digest.items:
        item.enrichment = enrich_record(record_for_item(item, meetings.get(item.meeting_id)), ctx)
    for notice in digest.notices:
        notice.enrichment = enrich_record(record_for_notice(notice), ctx)
    return digest
