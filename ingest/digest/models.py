"""The items.json contract. These models ARE the schema (see digest.schema)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

CHI = ZoneInfo("America/Chicago")

MeetingStatus = Literal["scheduled", "cancelled", "rescheduled", "held", "unknown"]
NoticeKind = Literal["comment_period", "hearing", "funding_round", "report", "public_notice", "page_change"]
EventType = Literal[
    "meeting_new", "agenda_posted", "item_added", "meeting_cancelled",
    "meeting_rescheduled", "outcome_recorded", "notice_new",
]


class Attachment(BaseModel):
    label: str
    url: str


class Outcome(BaseModel):
    action: str
    date: dt.date
    tally: str | None = None


class Geography(BaseModel):
    address: str | None = None
    ward: int | None = None
    community_area: str | None = None


class Figures(BaseModel):
    units: int | None = None
    affordable_units: int | None = None


class CategoryHit(BaseModel):
    id: str
    via: Literal["model", "body", "keyword", "override"]
    detail: str = ""


class Enrichment(BaseModel):
    status: Literal["ok", "failed", "screened_out"]
    housing_relevant: bool = False
    relevance_reason: str = ""
    summary: str = ""
    categories: list[CategoryHit] = Field(default_factory=list)
    geography: Geography = Field(default_factory=Geography)
    entities: list[str] = Field(default_factory=list)
    figures: Figures = Field(default_factory=Figures)
    amount: int | None = None  # the dollar figure the item turns on, in whole dollars
    amount_kind: str | None = None  # what that figure is: "sale price", "conduit loan"
    zoning_from: list[str] = Field(default_factory=list)  # a rezoning's current districts, as written
    zoning_to: str | None = None  # and the requested one
    source_quality: Literal["full", "partial", "scanned", "none"] = "none"
    model: str = ""
    content_hash: str = ""
    overridden: bool = False


class Meeting(BaseModel):
    id: str
    source_id: str
    body: str
    body_id: str
    jurisdiction: str = "chicago"
    start: dt.datetime
    time_known: bool = True
    status: MeetingStatus = "scheduled"
    location: str | None = None
    agenda_url: str | None = None
    video_url: str | None = None
    minutes_url: str | None = None
    detail_url: str | None = None
    needs_ai_extraction: bool = False
    agenda_text: str | None = Field(default=None, exclude=True)
    item_count_total: int = 0
    item_count_relevant: int = 0
    first_seen_at: dt.datetime | None = None
    last_changed_at: dt.datetime | None = None


class Item(BaseModel):
    id: str
    meeting_id: str
    source_ref: str
    title: str
    attachments: list[Attachment] = Field(default_factory=list)
    outcome: Outcome | None = None
    extraction: Literal["structured", "ai_extracted"] = "structured"
    geography_hint: Geography | None = None
    enrichment: Enrichment | None = None
    first_seen_at: dt.datetime | None = None
    last_changed_at: dt.datetime | None = None


class Notice(BaseModel):
    id: str
    source_id: str
    body: str
    kind: NoticeKind
    title: str
    url: str
    detail: str = ""
    new_link: bool = False  # a page_change notice whose url is a newly linked document (not a text diff)
    posted_at: dt.datetime | None = None
    deadline: dt.datetime | None = None
    enrichment: Enrichment | None = None
    first_seen_at: dt.datetime | None = None
    last_changed_at: dt.datetime | None = None


class SourceStatus(BaseModel):
    source_id: str
    last_success_at: dt.datetime | None = None
    status: Literal["ok", "stale", "error"]
    message: str = ""


class ChangeEvent(BaseModel):
    type: EventType
    ref_id: str
    at: dt.datetime


class Digest(BaseModel):
    generated_at: dt.datetime
    sources: list[SourceStatus] = Field(default_factory=list)
    meetings: list[Meeting] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    notices: list[Notice] = Field(default_factory=list)
    events: list[ChangeEvent] = Field(default_factory=list)


class SourceResult(BaseModel):
    """What one adapter returns from one fetch. Not part of items.json."""
    meetings: list[Meeting] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    notices: list[Notice] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # The date range (Chicago dates, inclusive) the adapter queried; None when the source has no such window.
    window_start: dt.date | None = None
    window_end: dt.date | None = None


def load_digest(path: Path) -> Digest | None:
    if not path.exists():
        return None
    return Digest.model_validate_json(path.read_text(encoding="utf-8"))


def dump_digest(digest: Digest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(digest.model_dump_json(indent=1) + "\n", encoding="utf-8")
