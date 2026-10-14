"""One shared enrichment per item/notice: relevance screen, summary, grounding checks, categories, geography."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from urllib.parse import urlsplit

import anthropic
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader, PdfWriter

from .cache import JsonCache
from .categories import Categories, merge_hits
from .http import FetchError, Fetcher
from .models import CategoryHit, Digest, Enrichment, Figures, Geography, Item, Meeting, Notice
from .pdftext import has_text_layer, pdf_text
from .sources.base import html_to_text
from .units import parse_units

PROMPT_VERSION = "1"  # bump to re-enrich everything after a prompt change
MAX_FAILED_ATTEMPTS = 3  # a record that keeps failing deterministically is retried this many runs, then left failed
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

You get the item's title and, when available, the start of its document.

An item is housing_relevant if it bears on homes: building, financing, preserving, regulating, taxing, or demolishing them, or the rights of the people who live in them. City land sales for housing, housing-related tax incentives, and changes to the zoning code's rules for residential use count.
A rezoning of one site counts only if it adds, removes, or changes homes there (dwelling units or residential use). A rezoning for a purely commercial, industrial, institutional, or parking use with no dwelling units does not, even if the old or new district would allow homes.
Signs, driveways, sidewalk cafes, honorary street names, landmark fee waivers, and business licenses do not.
When the material does not say and you cannot tell, mark the item relevant. Give a one-sentence reason."""

SUMMARY_SYSTEM = """You write one entry in an email digest of Chicago housing public meetings, read by housing advocates and researchers.

You get one agenda item or public notice: the body that will act on it, its title, and whatever document text could be retrieved.

housing_relevant: whether it bears on homes: building, financing, preserving, regulating, taxing, or demolishing them, or the rights of the people who live in them. Zoning changes that add or remove homes count. Signs, business licenses, and commercial-only projects do not.
relevance_reason: one sentence.
summary: one to three plain sentences, at most about 40 words, on what the item would do and who is behind it, using only facts in the provided material. Do not speculate about effects. If the material says little more than the title, restate the title plainly.
categories: the ids below that the item clearly bears on. An empty list is fine.
{categories}
address: the street address of the site the item concerns, copied as written, or null. Never the agency's office, an address for sending comments, or the meeting location.
entities: developers, applicants, owners, lenders, and agencies named in the material, copied exactly as written.
units, affordable_units: counts of homes stated in the material, or null. Never estimate."""


# Attachments never worth reading: disclosure forms, cover letters, notice affidavits.
BOILERPLATE_LABEL = re.compile(r"\bEDS\b|economic disclosure|mayoral letter|affidavit", re.IGNORECASE)


@dataclass
class Record:
    id: str
    body: str
    title: str
    context: str
    urls: list[str]  # every source url; part of the content hash
    extra_text: str = ""
    hint: Geography | None = None
    fetch_urls: list[str] | None = None  # the urls actually read; defaults to urls

    def __post_init__(self):
        if self.fetch_urls is None:
            self.fetch_urls = list(self.urls)


def record_for_item(item: Item, meeting: Meeting | None) -> Record:
    body = meeting.body if meeting else ""
    when = f"{meeting.start:%B} {meeting.start.day}, {meeting.start.year}" if meeting else "unknown date"
    return Record(id=item.id, body=body, title=item.title, context=f"{body}, {when}; agenda reference {item.source_ref}",
                  urls=[a.url for a in item.attachments], hint=item.geography_hint,
                  fetch_urls=[a.url for a in item.attachments if not BOILERPLATE_LABEL.search(a.label)])


def _is_document_url(url: str) -> bool:
    """A page or PDF worth reading: .pdf, .htm, .html or no extension. Query and fragment are ignored."""
    last = urlsplit(url).path.rsplit("/", 1)[-1]
    ext = last.rsplit(".", 1)[-1].lower() if "." in last else ""
    return ext in ("", "pdf", "htm", "html")


def record_for_notice(n: Notice) -> Record:
    # A newly linked document is read (HTML or PDF). A text-diff notice's url is the whole watched page: use the
    # diff in extra_text only. Notices saved before new_link existed keep the old rule (PDF urls only).
    fetch = n.kind != "page_change" or (n.new_link and _is_document_url(n.url)) or n.url.lower().endswith(".pdf")
    urls = [n.url] if fetch else []
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
    scanned_page_cap: int = 4  # a scanned PDF goes to the model as its first pages only
    max_doc_bytes: int = 30_000_000  # the API rejects PDFs over 32 MB
    screen_chars: int = 3000  # how much document text the screen sees
    screen_pages: int = 2  # or how many pages of a scanned document
    office_addresses: list[str] = field(default_factory=list)  # agency offices: never an item's site
    halted: str = ""  # set when the API rejects our credentials: every further call would fail the same way


def content_hash(title: str, sources: list[str], fingerprint: str) -> str:
    raw = json.dumps([PROMPT_VERSION, title, sources, fingerprint], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def trim_pdf(data: bytes, pages: int) -> bytes:
    """The first `pages` pages of a PDF; the original bytes if it is already that short or cannot be parsed."""
    try:
        reader = PdfReader(BytesIO(data))
        if len(reader.pages) <= pages:
            return data
        w = PdfWriter()
        for page in reader.pages[:pages]:
            w.add_page(page)
        buf = BytesIO()
        w.write(buf)
        return buf.getvalue()
    except Exception:
        return data


def doc_block(data: bytes) -> dict:
    return {"type": "document", "source": {
        "type": "base64", "media_type": "application/pdf", "data": base64.b64encode(data).decode("ascii")}}


def gather_sources(fetcher: Fetcher, urls: list[str], char_cap: int, max_attachments: int, page_cap: int = 4,
                   max_doc_bytes: int = 30_000_000) -> tuple[str, list[bytes], str]:
    """Text of the sources, the scanned PDFs (trimmed to page_cap pages) to send as documents, and source_quality."""
    if not urls:
        return "", [], "none"
    texts: list[str] = []
    scans: list[bytes] = []
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
                continue
            data = trim_pdf(data, page_cap)
            if len(data) > max_doc_bytes:
                log.info("skipping a scanned PDF of %d bytes even after trimming: %s", len(data), url)
                quality = "partial"
                continue
            scans.append(data)
            quality = "scanned" if quality == "full" else quality
        else:
            texts.append(html_to_text(data.decode("utf-8", errors="replace")))
    joined = "\n\n---\n\n".join(t for t in texts if t.strip())
    if len(joined) > char_cap:
        joined = joined[:char_cap]
        quality = "partial" if quality == "full" else quality
    return joined, scans, quality


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
NUMBER_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
                     "ninety": "90", "hundred": "100"})


def _numbers(corpus: str) -> set[str]:
    digits = {tok.replace(",", "") for tok in re.findall(r"\d[\d,]*", corpus)}
    return digits | {NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", corpus) if w in NUMBER_WORDS}


def _grounded_number(n: int | None, numbers: set[str]) -> int | None:
    return n if n is not None and str(n) in numbers else None


STREET_WORDS = {"avenue": "ave", "street": "st", "boulevard": "blvd", "drive": "dr", "place": "pl", "parkway": "pkwy",
                "road": "rd", "court": "ct", "north": "n", "south": "s", "east": "e", "west": "w"}


def _street_tokens(text: str) -> list[str]:
    return [STREET_WORDS.get(t, t) for t in re.findall(r"[a-z0-9]+", _norm(text))]


def _grounded_address(address: str | None, corpus: str) -> str | None:
    if not address:
        return None
    tokens = _street_tokens(address)
    if not any(t.isdigit() for t in tokens):
        return None
    known = set(_street_tokens(corpus))
    return address if all(t in known for t in tokens if len(t) >= 3) else None


_TITLE_ADDRESS = re.compile(
    r"\bat\s+(\d+(?:-\d+)?(?:\s+(?:and|&)\s+\d+(?:-\d+)?)?\s+[NSEW]\.?\s+[^-]+?)"
    r"(?:\s+(?:a\.?k\.?a\.?|to|under|for|from|by)\s.*)?(?:\s*-\s*App\b.*)?$")


def title_address(title: str) -> str | None:
    """The site address in a title like "Zoning Reclassification Map No. 1-H at 1921 W Chicago Ave - App No. 1"."""
    m = _TITLE_ADDRESS.search(title.strip())
    return m.group(1).strip() if m else None


def is_office(address: str | None, offices: list[str]) -> bool:
    """True when the address is one of the agency offices (number and street match), never a project site."""
    if not address:
        return False
    tokens = set(_street_tokens(address))
    return any(set(_street_tokens(o)) <= tokens for o in offices)


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


OVERRIDABLE = ("housing_relevant", "relevance_reason", "summary", "categories", "geography", "entities", "figures")


def apply_overrides(e: Enrichment, ov: dict | None, rec_id: str = "?") -> Enrichment:
    """Hand corrections win over the model. A bad key or value is logged and skipped; it never aborts the run."""
    if not ov:
        return e
    if not isinstance(ov, dict):
        log.warning("%s: override is not a mapping; ignored", rec_id)
        return e
    data = e.model_dump()
    applied = []
    for key, value in ov.items():
        if key not in OVERRIDABLE:
            log.warning("%s: override key %r is not allowed (allowed: %s); ignored", rec_id, key, ", ".join(OVERRIDABLE))
            continue
        try:
            if key == "categories":
                if not isinstance(value, list):
                    raise TypeError("categories must be a list of ids")
                value = [{"id": c, "via": "override", "detail": ""} for c in value]
            elif key in ("geography", "figures"):
                value = {**data[key], **value}
            Enrichment.model_validate({**data, key: value})
        except (ValidationError, TypeError, ValueError) as ex:
            log.warning("%s: override %r is invalid, ignored: %s", rec_id, key, str(ex).splitlines()[0] if str(ex) else ex)
            continue
        data[key] = value
        applied.append(key)
    if not applied:
        return e
    out = Enrichment.model_validate(data)
    if out.status == "failed" and ov.get("summary") and "summary" in applied:
        out.status = "ok"
    if out.status == "screened_out" and ov.get("housing_relevant") and "housing_relevant" in applied:
        out.status = "ok"
    out.overridden = True
    return out


def _prompt(rec: Record, text: str) -> str:
    parts = [f"Body: {rec.body}", f"Context: {rec.context}", f"Title: {rec.title}"]
    if rec.extra_text:
        parts.append(f"Notice text:\n{rec.extra_text}")
    parts.append(f"Document text:\n{text}" if text else "No document text was available.")
    return "\n\n".join(parts)


def _screen_content(rec: Record, text: str, scans: list[bytes], ctx: EnrichContext):
    head = f"Committee: {rec.body}\nItem: {rec.title}"
    if text.strip():
        return f"{head}\n\nStart of the document:\n{text[:ctx.screen_chars]}"
    if scans:
        return [doc_block(trim_pdf(scans[0], ctx.screen_pages)), {"type": "text", "text": head}]
    return head


def _fresh(rec: Record, ctx: EnrichContext, h: str) -> Enrichment:
    rules = ctx.categories.rule_hits(rec.body, [rec.title, rec.extra_text])
    text, scans, quality = gather_sources(ctx.fetcher, rec.fetch_urls, ctx.char_cap, ctx.max_attachments,
                                          ctx.scanned_page_cap, ctx.max_doc_bytes)
    if rec.body in ctx.screen_bodies:
        sc = ctx.llm.parse(purpose="screen", model_key="screen", system=SCREEN_SYSTEM,
                           content=_screen_content(rec, text, scans, ctx), schema=Screen, max_tokens=8000)
        if not sc.housing_relevant:
            return Enrichment(status="screened_out", relevance_reason=sc.reason, categories=rules,
                              geography=rec.hint or Geography(), model=ctx.models["screen"], content_hash=h)
    s = ctx.llm.parse(purpose="summarize", model_key="summarize",
                      system=SUMMARY_SYSTEM.format(categories=ctx.categories.prompt_block()),
                      content=[*map(doc_block, scans), {"type": "text", "text": _prompt(rec, text)}],
                      schema=Summary, max_tokens=16000)
    # A scanned document has no text to check against. Its facts are accepted where the summary, which the model
    # wrote from that document, states them too: that adds nothing the summary does not already claim.
    corpus = _norm("\n".join([rec.title, rec.extra_text, text] + ([s.summary] if scans else [])))
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
    if figures["units"] is None:  # the permit unit parser, on the summary first (it is about the proposal), then the text
        figures["units"] = _grounded_number(parse_units(s.summary), numbers) or parse_units(text)
    address = _grounded_address(s.address, corpus)
    if not address or is_office(address, ctx.office_addresses):
        address = title_address(rec.title)
    if is_office(address, ctx.office_addresses):
        address = None
    late_rules = ctx.categories.rule_hits(None, [s.summary, *entities])  # keywords also match summary and entities
    model_hits = [CategoryHit(id=c, via="model") for c in s.categories if c in ctx.categories.by_id]
    return Enrichment(
        status="ok", housing_relevant=s.housing_relevant, relevance_reason=s.relevance_reason, summary=s.summary,
        categories=merge_hits(rules, late_rules, model_hits),
        geography=resolve_geography(rec.hint, address, ctx.geo),
        entities=entities,
        figures=Figures(**figures),
        source_quality=quality, model=ctx.models["summarize"], content_hash=h,
    )


def is_transient(ex: BaseException) -> bool:
    """An outage that says nothing about the record: network, rate limit, 5xx, or a failed fetch."""
    if isinstance(ex, (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError, FetchError)):
        return True  # APIConnectionError includes APITimeoutError
    if is_auth_failure(ex):
        return True  # a key or permission problem says nothing about the record
    return isinstance(ex, anthropic.APIStatusError) and (ex.status_code >= 500 or ex.status_code == 408)


def is_auth_failure(ex: BaseException) -> bool:
    return isinstance(ex, (anthropic.AuthenticationError, anthropic.PermissionDeniedError))


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
            if e is not None and is_office(e.geography.address, ctx.office_addresses):
                e.geography = Geography()  # an agency office is never the site; dropped without a new model call
                ctx.cache.set(rec.id, e.model_dump(mode="json"))
            if e is not None and e.status == "ok" and e.figures.units is None and (n := parse_units(e.summary)):
                e.figures.units = n  # free backfill from the stored summary
                ctx.cache.set(rec.id, e.model_dump(mode="json"))
            if (e is not None and e.status == "ok" and (addr := title_address(rec.title)) and e.geography.address
                    and e.geography.address != addr and e.geography.address.startswith(addr)):
                e.geography.address = addr  # an older title parse ran past the address ("... St to Buyer LLC")
                ctx.cache.set(rec.id, e.model_dump(mode="json"))
            if e is not None and e.status == "ok" and not e.geography.address and (addr := title_address(rec.title)):
                e.geography = resolve_geography(rec.hint, addr, ctx.geo)  # free: no model call
                ctx.cache.set(rec.id, e.model_dump(mode="json"))
            if e is not None and e.geography.address and e.geography.ward is None:
                # The geocoder may have failed transiently last time; the geocode cache makes a clean no-match free.
                geography = resolve_geography(rec.hint, e.geography.address, ctx.geo)
                if geography != e.geography:
                    e.geography = geography
                    ctx.cache.set(rec.id, e.model_dump(mode="json"))
    if e is None:
        if ctx.halted:
            e = _failed(rec, ctx, h, f"enrichment skipped this run: {ctx.halted}")
        elif attempts >= MAX_FAILED_ATTEMPTS:
            e = _failed(rec, ctx, h, f"enrichment failed {attempts} times; not retrying until the content changes")
        else:
            try:
                e = _fresh(rec, ctx, h)
            except Exception as ex:  # one bad record must never abort the run
                log.warning("%s: enrichment failed: %s: %s", rec.id, type(ex).__name__, ex)
                e = _failed(rec, ctx, h, f"enrichment failed: {ex}")
                if is_auth_failure(ex) and not ctx.halted:
                    ctx.halted = f"{type(ex).__name__} from the API; check ANTHROPIC_API_KEY"
                    log.error("enrichment halted for the rest of this run: %s", ctx.halted)
                # Only failures a retry cannot fix count toward the cap; an outage must not fail records for good.
                ctx.cache.set(rec.id, {"content_hash": h, "failed_attempts": attempts + (0 if is_transient(ex) else 1)})
            else:
                ctx.cache.set(rec.id, e.model_dump(mode="json"))
    return apply_overrides(e, ctx.overrides.get(rec.id), rec.id)


def enrich_digest(digest: Digest, ctx: EnrichContext) -> Digest:
    meetings = {m.id: m for m in digest.meetings}
    for item in digest.items:
        item.enrichment = enrich_record(record_for_item(item, meetings.get(item.meeting_id)), ctx)
    for notice in digest.notices:
        notice.enrichment = enrich_record(record_for_notice(notice), ctx)
    return digest
