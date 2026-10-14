"""Claude reads agendas that have no structured items (IHDA PDFs, CLIHTF event text) and lists the business items."""
from __future__ import annotations

import base64
import hashlib

from pydantic import BaseModel

from .cache import JsonCache
from .http import Fetcher
from .models import Attachment, Item, Meeting
from .sources.base import slugify, unique_ref


class ExtractedItem(BaseModel):
    number: str  # as printed ("3.", "B", "IV."); empty string when the agenda does not number it
    title: str


class AgendaExtraction(BaseModel):
    items: list[ExtractedItem]


SYSTEM = """You list the items of business on a public meeting agenda.

Return each item of business in agenda order: resolutions, loans, contracts, bond issues, allocations, budgets, policies, and reports or presentations on a named subject.
Skip procedural entries: call to order, roll call, approval of minutes, public comment, executive or closed session, adjournment, and headings that only group other items.
Copy each item's number and wording from the agenda. Do not add anything the agenda does not say.
If the agenda lists no items of business, return an empty list."""


def title_ref(title: str, max_len: int = 80) -> str:
    """A stable id fragment from an item title. Long titles that share a prefix end in a hash of the whole title."""
    full = slugify(title, 10_000)
    if len(full) <= max_len:
        return full
    return f"{full[:max_len - 8].rstrip('-')}-{hashlib.sha1(full.encode()).hexdigest()[:7]}"


def extract_items(meeting: Meeting, fetcher: Fetcher, llm, cache: JsonCache) -> list[Item]:
    when = f"{meeting.start:%B} {meeting.start.day}, {meeting.start.year}"
    if meeting.agenda_text:
        source = meeting.agenda_text.encode("utf-8")
        content = f"Agenda for {meeting.body}, {when}:\n\n{meeting.agenda_text}"
    else:
        source = fetcher.get_bytes(meeting.agenda_url)
        content = [
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(source).decode("ascii")}},
            {"type": "text", "text": f"This is the agenda for {meeting.body}, {when}. List its items of business."},
        ]
    h = hashlib.sha256(source).hexdigest()[:16]
    cached = cache.get(meeting.id)
    rows = None
    if cached and cached.get("hash") == h:
        try:
            rows = [ExtractedItem(**r) for r in cached["items"]]
        except (TypeError, KeyError, ValueError):  # stale or corrupt entry: treat as a miss
            rows = None
    if rows is None:
        rows = llm.parse(purpose="extract", model_key="extract", system=SYSTEM, content=content,
                         schema=AgendaExtraction, max_tokens=16000).items
        cache.set(meeting.id, {"hash": h, "items": [r.model_dump() for r in rows]})

    attachments = [Attachment(label="Agenda", url=meeting.agenda_url)] if meeting.agenda_url else []
    used: set[str] = set()
    items = []
    for i, r in enumerate(rows, 1):
        ref = r.number.strip().rstrip(".").strip() or str(i)
        # The id comes from the title, not the number: a revised agenda that drops an item renumbers the rest.
        slug = unique_ref(title_ref(r.title), used)
        items.append(Item(id=f"{meeting.id}#{slug}", meeting_id=meeting.id, source_ref=ref, title=r.title.strip(),
                          extraction="ai_extracted", attachments=list(attachments)))
    return items
