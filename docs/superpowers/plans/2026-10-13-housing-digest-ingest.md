# Chicago Housing Digest — Ingest + Site Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python half of the digest: adapters for the eight v1 sources, AI agenda extraction and enrichment, change detection, and the `items.json` contract plus a static web page and `.ics` feeds, run 3×/day by GitHub Actions.

**Architecture:** One adapter per source returns normalized `Meeting`/`Item`/`Notice` records (pydantic models that *are* the contract). The orchestrator isolates each adapter, runs Claude agenda extraction for PDF/text-only agendas, merges into the previous `data/items.json` (stamping `first_seen_at`, emitting `ChangeEvent`s, carrying forward data from failed sources), enriches each record once (cached by content hash), and writes `items.json`. A separate step renders `site/` from `items.json`. Plan 2 (Cloudflare Worker + D1 + Resend, including the admin alert email) consumes `items.json` and is out of scope here.

**Tech Stack:** Python 3.12, httpx, beautifulsoup4, pypdf, pydantic v2, anthropic (Python SDK, `messages.parse`), PyYAML, shapely 2, Jinja2, jsonschema, tzdata, pytest. GitHub Actions + GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-10-13-housing-digest-design.md`

## Global Constraints

- Python `>=3.12`. Every package listed in `ingest/pyproject.toml`; nothing else imported.
- All meeting times are timezone-aware in `America/Chicago` (`digest.models.CHI`); `tzdata` is a dependency because Windows has no system tz database.
- Every chicago.gov request sends a full browser User-Agent. Every other host gets `chicago-housing-digest/0.1 (+https://github.com/danielkayhertz/chicago-housing-digest)`. Never put anyone's email address in a User-Agent.
- Never construct Community Development Commission or Plan Commission hearing URLs; discover them from the listing page.
- Models (configurable in `config/ai.yaml`): relevance screen `claude-haiku-5-5` (City Council committee items only); summaries and agenda extraction `claude-sonnet-5-5`. Structured output via `client.messages.parse(..., output_format=<pydantic model>)`; check `stop_reason == "refusal"`.
- `data/items.json` must validate against `schema/items.schema.json`, which is generated from `digest.models.Digest`. Changing a model means regenerating the schema in the same commit.
- Tests never touch the network or the Claude API: use `FakeFetcher` and `FakeLLM` from `ingest/tests/fakes.py`.
- Source health is reported only through `sources[]` in `items.json`. The admin email (to dhertz@impactforequity.org only) is Plan 2. Nothing in this plan emails anyone.
- ID formats: meetings `elms:<meetingId>`, `legistar:<client>:<EventId>`, `cpc:<YYYY-MM-DD>`, `cdc:<YYYY-MM-DD>`, `ihda:<body-slug>:<YYYY-MM-DD>`, `clihtf:<eventId>`; items `<meeting id>#<ref>`; notices `ihda:notice:<sha1(url)[:12]>`, `watch:<page id>:<sha1[:12]>`.
- Ingest windows (days relative to today in Chicago): default `[-14, 60]`; IHDA `[-60, 90]`. Meetings are pruned 45 days after they happen.
- Git commits use the identity Daniel Kay Hertz <danielkayhertz@gmail.com> and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run every command from the repo root unless the step says otherwise. Tests run from `ingest/`: `cd ingest && python -m pytest ...`.

## Review Focus

1. **Rescheduled meeting with the same ID** (eLMS keeps `meetingId` when a date moves): one `meeting_rescheduled` event is expected, not a new meeting plus a vanished one. Pinned in Task 11 (`test_reschedule_same_id_emits_rescheduled`).
2. **Amended agenda.** When a Council meeting has both `Agenda.pdf` and `Amended Agenda.pdf`, the highest-`sort` Agenda file is used. Pinned in Task 2 (`test_agenda_file_picks_highest_sort`).
3. **A source that is down** (404 or timeout on the listing): its previous meetings, items and notices stay in `items.json`, its status is `error` with the old `last_success_at`, and it emits no events. Other sources are unaffected. Pinned in Task 11 (`test_failed_source_carries_forward`) and Task 12 (`test_run_isolates_failed_source`).
4. **Messy dates on chicago.gov pages:** non-breaking spaces, "Sept." abbreviations, and a "Canceled" line under a date. Pinned in Task 4 (`test_parse_schedule_handles_nbsp_and_abbreviations`).
5. **One Claude call fails (refusal or API error):** that record gets `status: failed`, is not cached (so it retries next run), and the rest of the run continues. Pinned in Task 10 (`test_refusal_marks_failed_and_continues`).

## File Structure

```
ingest/
  pyproject.toml
  digest/
    __init__.py
    models.py        # pydantic contract: Meeting, Item, Notice, Enrichment, Digest, SourceResult; load/dump
    schema.py        # generates schema/items.schema.json from Digest
    http.py          # Fetcher protocol, HttpFetcher (UA, per-host delay, retries), FetchError/NotFound, url_key
    pdftext.py       # pdf_text(), has_text_layer()
    cache.py         # JsonCache (extract, enrich, geocode caches under data/cache/)
    categories.py    # Categories (categories.yaml), keyword/body rules, prompt block, fingerprint
    geo.py           # Geo: Census → Nominatim geocoding, ward/community-area point-in-polygon
    llm.py           # LLM wrapper over messages.parse; usage log; LLMRefused
    extract.py       # AI agenda extraction (IHDA PDFs, CLIHTF text) → Items
    enrich.py        # screen → summarize → grounding → categories → geography → overrides
    state.py         # merge(prev, results, now) → Digest with ChangeEvents
    registry.py      # sources.yaml → adapter instances
    run.py           # orchestrator + CLI (python -m digest.run)
    site.py          # static page + per-body .ics (python -m digest.site)
    eval.py          # labeling sample + precision/recall (python -m digest.eval)
    costs.py         # usage.jsonl → dollars (python -m digest.costs)
    templates/index.html.j2
    sources/
      __init__.py
      base.py        # BaseAdapter, Window, window_for, slugify, html_to_text, ImplausibleResult
      elms.py        # City Council committees (eLMS API + agenda PDF)
      legistar.py    # CHA (generic Legistar client)
      dpd.py         # Plan Commission + Community Development Commission (chicago.gov HTML)
      ihda.py        # IHDA meetings page: meetings + TEFRA/QAP/subsidy notices
      clihtf.py      # CLIHTF Events Calendar API
      watcher.py     # page-change watcher → page_change notices
  scripts/fetch_boundaries.py
  tests/
    fakes.py         # FakeFetcher, FakeLLM
    fixtures/        # moved from tests/fixtures (Task 1)
    test_*.py
config/
  sources.yaml  watch.yaml  categories.yaml  ai.yaml  overrides.yaml
  geo/wards.geojson  geo/community_areas.geojson
schema/items.schema.json
data/                # committed state: items.json, snapshots/, cache/, usage.jsonl
site/                # build output (gitignored)
.github/workflows/ingest.yml
eval/                # to_label.csv, labels.csv
```

---

### Task 1: Scaffold, contract models, fetcher, fakes

**Files:**
- Move: `tests/fixtures/` → `ingest/tests/fixtures/`
- Create: `ingest/pyproject.toml`, `ingest/digest/__init__.py`, `ingest/digest/models.py`, `ingest/digest/schema.py`, `ingest/digest/http.py`, `ingest/digest/cache.py`, `ingest/tests/fakes.py`, `ingest/tests/fixtures/common/empty_list.json`, `ingest/tests/test_models.py`, `ingest/tests/test_http.py`, `schema/items.schema.json`, `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `digest.models`: `CHI`, `Attachment(label, url)`, `Outcome(action, date, tally)`, `Geography(address, ward, community_area)`, `Figures(units, affordable_units)`, `CategoryHit(id, via, detail)`, `Enrichment`, `Meeting`, `Item`, `Notice`, `SourceStatus`, `ChangeEvent`, `Digest`, `SourceResult(meetings, items, notices, warnings)`, `load_digest(path) -> Digest | None`, `dump_digest(digest, path) -> None`.
  - `digest.schema`: `SCHEMA_PATH`, `schema_text() -> str`.
  - `digest.http`: `Fetcher` protocol (`get_bytes/get_text/get_json(url, params=None)`), `HttpFetcher`, `FetchError(url, status, message)`, `NotFound(FetchError)`, `url_key(url, params) -> str`.
  - `digest.cache`: `JsonCache(path)` with `get(key)`, `set(key, value)`, `save()`.
  - `tests/fakes.py`: `FakeFetcher(routes)`, `FakeLLM(responses)`.

- [ ] **Step 1: Move fixtures and create the package skeleton**

```bash
mkdir -p ingest/digest/sources ingest/digest/templates ingest/scripts ingest/tests
git mv tests/fixtures ingest/tests/fixtures
git add ingest/tests/fixtures/elms/matter_SO2026-0027202.json 2>/dev/null || true
mkdir -p ingest/tests/fixtures/common && printf '[]' > ingest/tests/fixtures/common/empty_list.json
touch ingest/digest/__init__.py ingest/digest/sources/__init__.py
```

(`matter_SO2026-0027202.json` is the substitute ordinance for agenda record O2026-0027202, saved 2026-10-13; the O-record itself now returns 404.)

`.gitignore`:

```
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
site/
```

`ingest/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "housing-digest-ingest"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "beautifulsoup4>=4.12",
  "pypdf>=4.0",
  "pydantic>=2.7",
  "anthropic",
  "pyyaml>=6.0",
  "shapely>=2.0",
  "jinja2>=3.1",
  "jsonschema>=4.0",
  "tzdata",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.setuptools.packages.find]
include = ["digest*"]

[tool.setuptools.package-data]
digest = ["templates/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Install into a venv at the repo root:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "ingest[dev]"     # Windows; on Linux/macOS: .venv/bin/python
```

Activate the venv for every later step (`source .venv/Scripts/activate` in Git Bash).

- [ ] **Step 2: Write the failing model tests**

`ingest/tests/test_models.py`:

```python
import datetime as dt
import json

import jsonschema

from digest.models import CHI, Attachment, Digest, Item, Meeting, dump_digest, load_digest
from digest.schema import SCHEMA_PATH, schema_text

NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)


def sample_digest() -> Digest:
    m = Meeting(
        id="cpc:2026-10-15", source_id="cpc", body="Chicago Plan Commission", body_id="cpc",
        start=dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI), agenda_text="not serialized",
    )
    it = Item(id="cpc:2026-10-15#1133-w-kinzie-st", meeting_id=m.id, source_ref="1133 W. Kinzie St.",
              title="A proposed Planned Development", attachments=[Attachment(label="PD Application", url="https://x/a.pdf")])
    return Digest(generated_at=NOW, meetings=[m], items=[it])


def test_digest_roundtrip_excludes_agenda_text(tmp_path):
    path = tmp_path / "items.json"
    dump_digest(sample_digest(), path)
    text = path.read_text(encoding="utf-8")
    assert "agenda_text" not in text
    back = load_digest(path)
    assert back.items[0].title == "A proposed Planned Development"
    assert back.meetings[0].agenda_text is None


def test_load_missing_returns_none(tmp_path):
    assert load_digest(tmp_path / "nope.json") is None


def test_start_serializes_with_chicago_offset():
    assert '"2026-10-15T10:00:00-05:00"' in sample_digest().model_dump_json()


def test_schema_committed_matches_models():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema_text()


def test_sample_validates_against_schema():
    instance = json.loads(sample_digest().model_dump_json())
    jsonschema.validate(instance, json.loads(schema_text()))
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.models'`

- [ ] **Step 4: Implement models, schema, cache**

`ingest/digest/models.py`:

```python
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


def load_digest(path: Path) -> Digest | None:
    if not path.exists():
        return None
    return Digest.model_validate_json(path.read_text(encoding="utf-8"))


def dump_digest(digest: Digest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(digest.model_dump_json(indent=1) + "\n", encoding="utf-8")
```

`ingest/digest/schema.py`:

```python
"""Generate schema/items.schema.json from the Digest model: python -m digest.schema"""
import json
from pathlib import Path

from .models import Digest

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "items.schema.json"


def schema_text() -> str:
    return json.dumps(Digest.model_json_schema(mode="serialization"), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(schema_text(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
```

`ingest/digest/cache.py`:

```python
import json
from pathlib import Path
from typing import Any


class JsonCache:
    """A small JSON-file key/value cache, committed to the repo under data/cache/."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
```

Generate the schema:

```bash
cd ingest && python -m digest.schema
```

- [ ] **Step 5: Run model tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_models.py -v`
Expected: 5 passed

- [ ] **Step 6: Write the failing fetcher/fakes tests**

`ingest/tests/test_http.py`:

```python
import pytest

from digest.http import BOT_UA, BROWSER_UA, HttpFetcher, NotFound, url_key
from fakes import FakeFetcher


def test_url_key_encodes_params():
    key = url_key("https://api.example.org/meeting", {"filter": "body eq 'X'", "top": 5})
    assert key == "https://api.example.org/meeting?filter=body+eq+%27X%27&top=5"


def test_user_agent_by_host():
    f = HttpFetcher()
    assert f._headers("www.chicago.gov")["User-Agent"] == BROWSER_UA
    assert f._headers("webapi.legistar.com")["User-Agent"] == BOT_UA
    assert "@" not in BOT_UA


def test_fake_fetcher_routes_first_match_and_raises():
    f = FakeFetcher([("recordNumber/O2026-1", NotFound("x", 404)), ("recordNumber/", "common/empty_list.json")])
    with pytest.raises(NotFound):
        f.get_json("https://h/matter/recordNumber/O2026-1")
    assert f.get_json("https://h/matter/recordNumber/SO2026-1") == []
    with pytest.raises(AssertionError, match="no route"):
        f.get_text("https://elsewhere/")
```

(If `test_url_key_encodes_params` fails only because your httpx version encodes spaces as `%20` instead of `+`, change the expected string to match; the FakeFetcher matches on the *decoded* key either way.)

- [ ] **Step 7: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_http.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.http'`

- [ ] **Step 8: Implement the fetcher and the fakes**

`ingest/digest/http.py`:

```python
"""HTTP access for adapters. Adapters depend on the Fetcher protocol so tests can use FakeFetcher."""
from __future__ import annotations

import json
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
BOT_UA = "chicago-housing-digest/0.1 (+https://github.com/danielkayhertz/chicago-housing-digest)"
BROWSER_HOSTS = ("chicago.gov",)


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, message: str = ""):
        super().__init__(f"{status} {url} {message}".strip())
        self.url = url
        self.status = status


class NotFound(FetchError):
    pass


def url_key(url: str, params: dict | None = None) -> str:
    return str(httpx.URL(url, params=params))


class Fetcher(Protocol):
    def get_bytes(self, url: str, params: dict | None = None) -> bytes: ...
    def get_text(self, url: str, params: dict | None = None) -> str: ...
    def get_json(self, url: str, params: dict | None = None) -> Any: ...


class HttpFetcher:
    """Polite fetcher: per-host delay, retries on transport errors and 5xx, browser UA for chicago.gov."""

    def __init__(self, delay: float = 1.1, timeout: float = 90.0, retries: int = 2):
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)
        self.delay = delay
        self.retries = retries
        self._last: dict[str, float] = {}

    def _headers(self, host: str) -> dict[str, str]:
        ua = BROWSER_UA if host.endswith(BROWSER_HOSTS) else BOT_UA
        return {"User-Agent": ua, "Accept": "*/*"}

    def get_bytes(self, url: str, params: dict | None = None) -> bytes:
        host = urlsplit(url).hostname or ""
        key = url_key(url, params)
        for attempt in range(self.retries + 1):
            wait = self._last.get(host, 0.0) + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()
            try:
                r = self.client.get(url, params=params, headers=self._headers(host))
            except httpx.TransportError as e:
                if attempt == self.retries:
                    raise FetchError(key, None, str(e)) from e
                continue
            if r.status_code == 404:
                raise NotFound(key, 404)
            if r.status_code >= 500 and attempt < self.retries:
                continue
            if r.status_code >= 400:
                raise FetchError(key, r.status_code)
            return r.content
        raise FetchError(key, None, "retries exhausted")

    def get_text(self, url: str, params: dict | None = None) -> str:
        return self.get_bytes(url, params).decode("utf-8", errors="replace")

    def get_json(self, url: str, params: dict | None = None) -> Any:
        return json.loads(self.get_bytes(url, params))
```

`ingest/tests/fakes.py`:

```python
import json
from pathlib import Path
from urllib.parse import unquote_plus

from digest.http import url_key

FIX = Path(__file__).parent / "fixtures"


class FakeFetcher:
    """Routes requests by substring of the decoded URL; first matching route wins.

    A route target is a fixture path relative to tests/fixtures, raw bytes, or an Exception to raise.
    """

    def __init__(self, routes: list[tuple[str, object]]):
        self.routes = routes
        self.calls: list[str] = []

    def get_bytes(self, url, params=None):
        key = unquote_plus(url_key(url, params))
        self.calls.append(key)
        for needle, target in self.routes:
            if needle in key:
                if isinstance(target, Exception):
                    raise target
                if isinstance(target, bytes):
                    return target
                return (FIX / target).read_bytes()
        raise AssertionError(f"FakeFetcher: no route for {key}")

    def get_text(self, url, params=None):
        return self.get_bytes(url, params).decode("utf-8")

    def get_json(self, url, params=None):
        return json.loads(self.get_bytes(url, params))


class FakeLLM:
    """Stands in for digest.llm.LLM. responses maps purpose -> list (popped in order) or callable(content)."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[dict] = []

    def parse(self, *, purpose, model_key, system, content, schema, max_tokens=16000):
        self.calls.append({"purpose": purpose, "model_key": model_key, "system": system, "content": content})
        r = self.responses[purpose]
        value = r(content) if callable(r) else r.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
```

- [ ] **Step 9: Run all tests**

Run: `cd ingest && python -m pytest -v`
Expected: 8 passed

- [ ] **Step 10: Commit**

```bash
git add .gitignore ingest schema
git commit -m "Scaffold ingest package: contract models, schema, fetcher, fakes"
```

---

### Task 2: City Council committees adapter (eLMS)

**Files:**
- Create: `ingest/digest/pdftext.py`, `ingest/digest/sources/base.py`, `ingest/digest/sources/elms.py`, `ingest/tests/test_elms.py`

**Interfaces:**
- Consumes: `Fetcher`, `NotFound`, models from Task 1.
- Produces:
  - `digest.pdftext`: `pdf_text(data: bytes, max_pages=60) -> str`, `has_text_layer(text) -> bool`.
  - `digest.sources.base`: `BaseAdapter(cfg)` (attributes `source_id`, `cfg`, `days`, `min_meetings`; abstract `fetch(fetcher, now) -> SourceResult`), `Window(start, end).contains(d)`, `window_for(now, days) -> Window`, `slugify(text, max_len=60) -> str`, `html_to_text(html) -> str`, `parse_ampm(text) -> dt.time | None`, `ImplausibleResult(Exception)`.
  - `digest.sources.elms`: `ElmsAdapter(cfg)`; `parse_agenda(text) -> list[tuple[str, str]]`; `agenda_file(files) -> dict | None`; `committee_outcome(matter, body, day) -> Outcome | None`.
  - Config keys: `id`, `body` (exact eLMS body name), `display_body`, optional `window_days`, `min_meetings`, `page_size`.

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_elms.py`:

```python
import datetime as dt
import json

from digest.http import NotFound
from digest.models import CHI, Outcome
from digest.pdftext import pdf_text
from digest.sources import elms
from digest.sources.elms import ElmsAdapter, agenda_file, parse_agenda
from fakes import FIX, FakeFetcher

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=CHI)
CFG = {"id": "elms-zoning", "body": "Committee on Zoning, Landmarks and Building Standards",
       "display_body": "City Council Committee on Zoning"}
SEP17 = "elms:A44FB4F3-45B0-F111-AAAC-001DD80B58E5"


def routes(**over):
    base = [
        ("recordNumber/O2026-0025391", "elms/matter_by_record.json"),
        ("recordNumber/O2026-0027202", NotFound("O2026-0027202", 404)),
        ("recordNumber/SO2026-0027202", "elms/matter_SO2026-0027202.json"),
        ("meetingattachmentspublic/", "elms/agenda_zoning.pdf"),
        ("/meeting?", "elms/meetings_zoning.json"),
    ]
    return [(k, over.get(k, v)) for k, v in base]


def test_parse_agenda_records_and_titles():
    text = pdf_text((FIX / "elms/agenda_zoning.pdf").read_bytes())
    assert parse_agenda(text) == [
        ("O2026-0025391", "Amendment of Municipal Code Chapter 17-3 by adding Section 17-3-0600 regarding "
                          "transient-oriented development (TOD) streets"),
        ("O2026-0027202", "Amendment of Municipal Code Chapter 5-12 empowering Department of Buildings to "
                          "strengthen standards and enforcement mechanisms for residential buildings"),
    ]


def test_agenda_file_picks_highest_sort():
    rows = json.loads((FIX / "elms/meetings_zoning.json").read_text(encoding="utf-8"))["data"]
    sep17 = next(r for r in rows if r["meetingId"] == SEP17.split(":")[1])
    assert agenda_file(sep17["files"])["path"].endswith("a3e478c9-5f66-457d-8499-0cb46d2cfdec.pdf")


def test_fetch_meetings_items_outcomes():
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes()), NOW)
    by_id = {m.id: m for m in res.meetings}
    assert len(res.meetings) == 6
    m = by_id[SEP17]
    assert m.body == "City Council Committee on Zoning"
    assert m.start.isoformat() == "2026-09-17T10:00:00-05:00"
    assert m.status == "held"
    assert m.agenda_url.endswith("a3e478c9-5f66-457d-8499-0cb46d2cfdec.pdf")
    assert m.video_url == "https://vimeo.com/manage/videos/1227517217"

    items = [i for i in res.items if i.meeting_id == SEP17]
    assert [i.id for i in items] == [f"{SEP17}#O2026-0025391", f"{SEP17}#O2026-0027202"]
    tod, bldg = items
    assert tod.title.startswith("Amendment of Municipal Code Chapter 17-3")
    assert tod.outcome is None
    assert [a.label for a in tod.attachments] == ["O2026-0025391 Ordinance", "O2026-0025391 Mayoral Letter"]
    assert bldg.source_ref == "SO2026-0027202"
    assert bldg.outcome == Outcome(action="Recommended to Pass", date=dt.date(2026, 9, 17), tally=None)

    cancelled = {x.id for x in res.meetings if x.status == "cancelled"}
    assert len(cancelled) == 3
    assert not any(i.meeting_id in cancelled for i in res.items)


def test_missing_matter_falls_back_to_agenda_title():
    gone = NotFound("x", 404)
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes(**{"recordNumber/SO2026-0027202": gone})), NOW)
    bldg = next(i for i in res.items if i.id == f"{SEP17}#O2026-0027202")
    assert bldg.source_ref == "O2026-0027202"
    assert bldg.title.startswith("Amendment of Municipal Code Chapter 5-12")
    assert bldg.attachments == []


def test_agenda_without_record_numbers_warns(monkeypatch):
    monkeypatch.setattr(elms, "pdf_text", lambda data: "COMMITTEE ON ZONING AGENDA\n" * 20)
    res = ElmsAdapter(CFG).fetch(FakeFetcher(routes()), NOW)
    assert res.items == []
    assert any("no record numbers" in w for w in res.warnings)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_elms.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.pdftext'`

- [ ] **Step 3: Implement pdftext, base, elms**

`ingest/digest/pdftext.py`:

```python
from io import BytesIO

from pypdf import PdfReader


def pdf_text(data: bytes, max_pages: int = 60) -> str:
    """Text layer of a PDF, or "" if it has none or cannot be parsed."""
    try:
        reader = PdfReader(BytesIO(data))
        pages = reader.pages[:max_pages]
    except Exception:
        return ""
    out = []
    for page in pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return "\n".join(out)


def has_text_layer(text: str) -> bool:
    return len(text.strip()) >= 200
```

`ingest/digest/sources/base.py`:

```python
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
```

`ingest/digest/sources/elms.py`:

```python
"""City Council committees via the Clerk's eLMS API. Agenda items come from the agenda PDF's record numbers."""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

from ..http import Fetcher, NotFound
from ..models import CHI, Attachment, Item, Meeting, MeetingStatus, Outcome, SourceResult
from ..pdftext import has_text_layer, pdf_text
from .base import BaseAdapter, window_for

ELMS = "https://api.chicityclerkelms.chicago.gov"
RECORD_RE = re.compile(r"DOCUMENT\s*#\s*([A-Z][A-Za-z]{0,2}\d{4}-\d{4,8})")
STATUS_WORDS: dict[str, MeetingStatus] = {
    "cancel": "cancelled", "reschedul": "rescheduled",
    "recess": "held", "adjourn": "held", "reconven": "held",
}


def _local(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso).astimezone(CHI)


def map_status(raw: str | None, start: dt.datetime, now: dt.datetime) -> MeetingStatus:
    s = (raw or "").lower()
    for word, status in STATUS_WORDS.items():
        if word in s:
            return status
    return "held" if start < now else "scheduled"


def agenda_file(files: list[dict] | None) -> dict | None:
    """The newest agenda: highest sort among Agenda attachments (Amended Agenda outranks Agenda)."""
    agendas = [f for f in files or [] if f.get("attachmentType") == "Agenda" and f.get("path")]
    return max(agendas, key=lambda f: f.get("sort") or 0) if agendas else None


def _block_title(block: str) -> str:
    groups: list[list[str]] = []
    cur: list[str] = []
    for line in block.splitlines()[1:]:  # line 0 is the rest of the "DOCUMENT #" header
        if line.strip():
            cur.append(line.strip())
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    keep = [
        g for g in groups
        if not g[0].startswith(("Sponsor", "Co-Sponsor"))
        and not " ".join(g).isupper()
        and not " ".join(g).isdigit()
    ]
    return " ".join(keep[0]) if keep else ""


def parse_agenda(text: str) -> list[tuple[str, str]]:
    """(record number, title as printed) in agenda order, first occurrence only."""
    matches = list(RECORD_RE.finditer(text))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, m in enumerate(matches):
        rec = m.group(1)
        if rec in seen:
            continue
        seen.add(rec)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((rec, _block_title(text[m.end():end])))
    return out


def _tally(votes: list[dict]) -> str | None:
    if not votes:
        return None
    counts = Counter(v.get("vote") for v in votes)
    return ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))


def committee_outcome(matter: dict, body: str, day: dt.date) -> Outcome | None:
    """Committee actions often have an empty meetingId, so join on (acting body, Chicago date)."""
    for a in matter.get("actions") or []:
        if a.get("actionByName") == body and a.get("actionDate") and _local(a["actionDate"]).date() == day:
            return Outcome(action=a["actionName"], date=day, tally=_tally(a.get("votes") or []))
    return None


class ElmsAdapter(BaseAdapter):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.body: str = cfg["body"]
        self.display: str = cfg.get("display_body", self.body)
        self.page_size: int = cfg.get("page_size", 20)

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        win = window_for(now, self.days)
        res = SourceResult()
        for raw in self._meetings(fetcher, win):
            m = self._meeting(raw, now)
            res.meetings.append(m)
            if m.status == "cancelled" or not m.agenda_url:
                continue
            items, warnings = self._items(fetcher, m)
            res.items += items
            res.warnings += warnings
        return res

    def _meetings(self, fetcher: Fetcher, win) -> list[dict]:
        out = []
        for page in range(10):  # newest (furthest future) first
            data = fetcher.get_json(f"{ELMS}/meeting", {
                "filter": f"body eq '{self.body}'", "top": self.page_size, "skip": page * self.page_size,
            })
            rows = data.get("data") or []
            out += [r for r in rows if win.contains(_local(r["date"]).date())]
            if len(rows) < self.page_size or _local(rows[-1]["date"]).date() < win.start:
                break
        return out

    def _meeting(self, r: dict, now: dt.datetime) -> Meeting:
        start = _local(r["date"])
        af = agenda_file(r.get("files"))
        return Meeting(
            id=f"elms:{r['meetingId']}", source_id=self.source_id, body=self.display,
            body_id=r.get("bodyId") or self.body, start=start,
            status=map_status(r.get("status"), start, now),
            location=r.get("location") or None, agenda_url=af["path"] if af else None,
            video_url=r.get("videoLink") or None,
        )

    def _items(self, fetcher: Fetcher, m: Meeting) -> tuple[list[Item], list[str]]:
        text = pdf_text(fetcher.get_bytes(m.agenda_url))
        records = parse_agenda(text)
        if not records:
            why = "agenda has text but no record numbers" if has_text_layer(text) else "agenda has no text layer"
            return [], [f"{m.id}: {why}"]
        return [self._item(m, rec, title, self._matter(fetcher, rec)) for rec, title in records], []

    def _matter(self, fetcher: Fetcher, rec: str) -> dict | None:
        # A substituted record (O… → SO…) can 404 under its original number.
        candidates = (rec, "S" + rec) if rec[0] in "OR" else (rec,)
        for candidate in candidates:
            try:
                return fetcher.get_json(f"{ELMS}/matter/recordNumber/{candidate}")
            except NotFound:
                continue
        return None

    def _item(self, m: Meeting, rec: str, agenda_title: str, matter: dict | None) -> Item:
        item_id = f"{m.id}#{rec}"
        if matter is None:
            return Item(id=item_id, meeting_id=m.id, source_ref=rec, title=agenda_title or rec)
        attachments = [
            Attachment(label=a.get("fileName", "").removesuffix(".pdf") or a.get("attachmentType") or "Attachment",
                       url=a["path"])
            for a in matter.get("attachments") or [] if a.get("path")
        ]
        return Item(
            id=item_id, meeting_id=m.id, source_ref=matter.get("recordNumber") or rec,
            title=matter.get("title") or agenda_title or rec, attachments=attachments,
            outcome=committee_outcome(matter, self.body, m.start.date()),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_elms.py -v`
Expected: 5 passed. If `test_parse_agenda_records_and_titles` fails on whitespace only, print `repr(parse_agenda(text))` and fix `_block_title` (not the expected string — the expected titles are what the agenda prints).

- [ ] **Step 5: Commit**

```bash
git add ingest/digest ingest/tests/test_elms.py
git commit -m "Add eLMS City Council committee adapter"
```

---

### Task 3: CHA adapter (generic Legistar)

**Files:**
- Create: `ingest/digest/sources/legistar.py`, `ingest/tests/test_legistar.py`

**Interfaces:**
- Consumes: `BaseAdapter`, `window_for` (Task 2); models.
- Produces: `LegistarAdapter(cfg)`; config keys `id`, `client` (e.g. `thecha`), `bodies` (map of Legistar body id → display name), optional `jurisdiction`, `window_days`, `min_meetings`.

- [ ] **Step 1: Write the failing test**

`ingest/tests/test_legistar.py`:

```python
import datetime as dt

from digest.models import CHI
from digest.sources.legistar import LegistarAdapter
from fakes import FakeFetcher

NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=CHI)
CFG = {"id": "cha", "client": "thecha", "bodies": {
    138: "CHA Board of Commissioners", 188: "CHA Finance & Audit Committee",
    189: "CHA Real Estate Operations Development Committee", 190: "CHA Tenant Services Committee"}}


def fetcher():
    return FakeFetcher([
        ("/events/1146/eventitems", "legistar/cha_eventitems.json"),
        ("/eventitems", "common/empty_list.json"),
        ("/thecha/events?", "legistar/cha_events.json"),
    ])


def test_events_skip_hidden_and_map_bodies():
    f = fetcher()
    res = LegistarAdapter(CFG).fetch(f, NOW)
    assert sorted(m.id for m in res.meetings) == ["legistar:thecha:1116", "legistar:thecha:1146", "legistar:thecha:1147"]
    m = next(m for m in res.meetings if m.id == "legistar:thecha:1146")
    assert m.body == "CHA Real Estate Operations Development Committee"
    assert m.start.isoformat() == "2026-09-15T08:40:00-05:00"
    assert m.status == "held"
    assert m.detail_url.startswith("https://thecha.legistar.com/MeetingDetail.aspx")
    assert any("EventDate ge datetime'2026-09-06'" in c for c in f.calls)


def test_items_only_matters_with_attachments():
    res = LegistarAdapter(CFG).fetch(fetcher(), NOW)
    assert [i.source_ref for i in res.items] == ["26-57", "26-96", "26-112"]
    iga = res.items[0]
    assert iga.id == "legistar:thecha:1146#26-57"
    assert iga.title.startswith("Authorization to execute an Intergovernmental Agreement")
    assert iga.attachments[0].label.startswith("Approved IGA with City of Chicago")
    assert iga.attachments[0].url.endswith(".pdf")
    assert iga.outcome is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_legistar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.sources.legistar'`

- [ ] **Step 3: Implement**

`ingest/digest/sources/legistar.py`:

```python
"""Generic Legistar Web API adapter (CHA in v1; suburban clients in v1.5)."""
from __future__ import annotations

import datetime as dt

from ..http import Fetcher
from ..models import CHI, Attachment, Item, Meeting, Outcome, SourceResult
from .base import BaseAdapter, window_for

API = "https://webapi.legistar.com/v1"


def _parse_time(s: str | None) -> dt.time | None:
    try:
        return dt.datetime.strptime((s or "").strip(), "%I:%M %p").time()
    except ValueError:
        return None


class LegistarAdapter(BaseAdapter):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.client: str = cfg["client"]
        self.bodies: dict[int, str] = {int(k): v for k, v in cfg["bodies"].items()}
        self.jurisdiction: str = cfg.get("jurisdiction", "chicago")

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        win = window_for(now, self.days)
        res = SourceResult()
        events = fetcher.get_json(f"{API}/{self.client}/events", {
            "$filter": f"EventDate ge datetime'{win.start.isoformat()}' and EventDate le datetime'{win.end.isoformat()}'",
        })
        for ev in events:
            if ev.get("EventBodyId") not in self.bodies:
                continue
            if (ev.get("EventAgendaStatusName") or "").lower() == "hidden":
                continue  # placeholder until the agenda is published
            m = self._meeting(ev, now)
            res.meetings.append(m)
            rows = fetcher.get_json(f"{API}/{self.client}/events/{ev['EventId']}/eventitems", {"Attachments": 1})
            res.items += [self._item(m, r) for r in rows if r.get("EventItemMatterFile")]
        return res

    def _meeting(self, ev: dict, now: dt.datetime) -> Meeting:
        day = dt.date.fromisoformat(ev["EventDate"][:10])
        t = _parse_time(ev.get("EventTime"))
        start = dt.datetime.combine(day, t or dt.time(0, 0), tzinfo=CHI)
        return Meeting(
            id=f"legistar:{self.client}:{ev['EventId']}", source_id=self.source_id,
            body=self.bodies[ev["EventBodyId"]], body_id=str(ev["EventBodyId"]),
            jurisdiction=self.jurisdiction, start=start, time_known=t is not None,
            status="held" if start < now else "scheduled", location=ev.get("EventLocation") or None,
            agenda_url=ev.get("EventAgendaFile") or None, minutes_url=ev.get("EventMinutesFile") or None,
            video_url=ev.get("EventVideoPath") or None, detail_url=ev.get("EventInSiteURL") or None,
        )

    def _item(self, m: Meeting, r: dict) -> Item:
        action = r.get("EventItemActionName") or r.get("EventItemPassedFlagName")
        title = (r.get("EventItemTitle") or r.get("EventItemMatterName") or r["EventItemMatterFile"]).strip()
        return Item(
            id=f"{m.id}#{r['EventItemMatterFile']}", meeting_id=m.id, source_ref=r["EventItemMatterFile"],
            title=title,
            attachments=[
                Attachment(label=a["MatterAttachmentName"], url=a["MatterAttachmentHyperlink"])
                for a in r.get("EventItemMatterAttachments") or [] if a.get("MatterAttachmentHyperlink")
            ],
            outcome=Outcome(action=action, date=m.start.date(), tally=r.get("EventItemTally") or None) if action else None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_legistar.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/sources/legistar.py ingest/tests/test_legistar.py
git commit -m "Add generic Legistar adapter for CHA"
```

---

### Task 4: Plan Commission and Community Development Commission adapter

**Files:**
- Create: `ingest/digest/sources/dpd.py`, `ingest/tests/test_dpd.py`

**Interfaces:**
- Consumes: `BaseAdapter`, `window_for`, `slugify`, `parse_ampm`, `ImplausibleResult` (Task 2).
- Produces: `DpdAdapter(cfg)`; `parse_schedule(html, base) -> list[ScheduleEntry]`; `parse_hearing(html, base) -> tuple[dt.time | None, list[HearingRow]]`; dataclasses `ScheduleEntry(date, agenda_url, cancelled)`, `HearingRow(address, ward, community_area, description, attachments)`. Config keys `id` (`cpc`/`cdc`), `listing_url`, `body`, `default_time` (`"HH:MM"`).

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_dpd.py`:

```python
import datetime as dt

from digest.models import CHI
from digest.sources.dpd import DpdAdapter, parse_hearing, parse_schedule
from fakes import FIX, FakeFetcher

BASE = "https://www.chicago.gov/content/city/en/depts/dcd/x.html"


def read(name):
    return (FIX / "dpd" / name).read_text(encoding="utf-8")


def entries_2026(name):
    return {e.date: e for e in parse_schedule(read(name), BASE) if e.date.year == 2026}


def test_parse_schedule_cpc():
    es = entries_2026("cpc_listing.html")
    assert len(es) == 12
    assert es[dt.date(2026, 10, 15)].agenda_url.endswith("October_2026_Plan_Commission_Hearing.html")
    assert es[dt.date(2026, 11, 19)].agenda_url is None


def test_parse_schedule_cdc_cancellations():
    es = entries_2026("cdc_listing.html")
    assert len(es) == 12
    assert {d for d, e in es.items() if e.cancelled} == {dt.date(2026, 3, 10), dt.date(2026, 9, 8)}
    assert es[dt.date(2026, 10, 13)].agenda_url.endswith("/agendas/2026/october.html")


def test_parse_schedule_handles_nbsp_and_abbreviations():
    html = """<h2>2027 Meeting Schedule</h2><table><tr>
      <td><a href="/a/jan.html">Jan.\xa012</a><br>Sept.&nbsp;14<br>Canceled</td>
      <td>October 12<p><a href="/a/oct.html">Agenda</a></p></td></tr></table>"""
    es = {e.date: e for e in parse_schedule(html, BASE)}
    assert es[dt.date(2027, 1, 12)].agenda_url == "https://www.chicago.gov/a/jan.html"
    assert es[dt.date(2027, 9, 14)].cancelled is True
    assert es[dt.date(2027, 10, 12)].agenda_url is None  # the Agenda link is in a separate <p> segment


def test_parse_hearing_cpc():
    t, rows = parse_hearing(read("cpc_2026_10.html"), BASE)
    assert t is None
    assert [r.address for r in rows] == ["1133 W. Kinzie St.", "3700-3846 N. Seminary Ave.", ""]
    assert [r.ward for r in rows] == [27, 44, 10]
    assert [r.community_area for r in rows] == ["Near West Side", "Lake View", None]
    assert [a.label for a in rows[0].attachments] == ["PD Application", "Presentation"]
    assert rows[2].attachments[0].label == "South Chicago Land Use Framework Plan (Informational webpage)"
    assert rows[0].description.startswith("A proposed Planned Development, submitted by MKR Owner LLC")


def test_parse_hearing_cdc_time_and_rows():
    t, rows = parse_hearing(read("cdc_2026_10.html"), BASE)
    assert t == dt.time(13, 30)
    assert [(r.address, r.ward) for r in rows] == [("1207 W. Leland Ave.", 46), ("758 E. 111th St.", 9)]
    assert rows[0].attachments[0].label == "Staff Report"


def test_adapter_cdc_window_and_items():
    cfg = {"id": "cdc", "body": "Community Development Commission", "default_time": "13:30",
           "listing_url": "https://www.chicago.gov/content/city/en/depts/dcd/community-development-commission/meetings--agendas-and-video-archives.html"}
    f = FakeFetcher([
        ("agendas/2026/october.html", "dpd/cdc_2026_10.html"),
        ("meetings--agendas-and-video-archives.html", "dpd/cdc_listing.html"),
    ])
    res = DpdAdapter(cfg).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    assert [m.id for m in res.meetings] == ["cdc:2026-10-13", "cdc:2026-11-10"]
    oct13, nov10 = res.meetings
    assert oct13.start.isoformat() == "2026-10-13T13:30:00-05:00" and oct13.time_known
    assert nov10.time_known is False and nov10.agenda_url is None
    assert [i.id for i in res.items] == ["cdc:2026-10-13#1207-w-leland-ave", "cdc:2026-10-13#758-e-111th-st"]
    assert res.items[0].geography_hint.ward == 46


def test_cpc_item_without_address_uses_position():
    cfg = {"id": "cpc", "body": "Chicago Plan Commission", "default_time": "10:00",
           "listing_url": "https://www.chicago.gov/content/city/en/depts/dcd/chicago-plan-commission/meetings--agendas---video-archives.html"}
    f = FakeFetcher([
        ("October_2026_Plan_Commission_Hearing", "dpd/cpc_2026_10.html"),
        ("meetings--agendas---video-archives", "dpd/cpc_listing.html"),
    ])
    res = DpdAdapter(cfg).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    ids = [i.id for i in res.items]
    assert ids == ["cpc:2026-10-15#1133-w-kinzie-st", "cpc:2026-10-15#3700-3846-n-seminary-ave", "cpc:2026-10-15#item-3"]
    assert res.meetings[0].time_known is False  # the CPC page states no time
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_dpd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.sources.dpd'`

- [ ] **Step 3: Implement**

`ingest/digest/sources/dpd.py`:

```python
"""Chicago Plan Commission and Community Development Commission (chicago.gov HTML pages)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from ..http import Fetcher
from ..models import CHI, Attachment, Geography, Item, Meeting, SourceResult
from .base import BaseAdapter, ImplausibleResult, parse_ampm, slugify, window_for

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


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


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
    tm = TIME_RE.search(_clean(soup.get_text(" ", strip=True)))
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
                description=_clean(tds[1].get_text(" ", strip=True)),
                attachments=[
                    Attachment(label=_clean(a.get_text(" ", strip=True)), url=urljoin(base, a["href"]))
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
            res.items += [self._item(m, n, row) for n, row in enumerate(rows, 1)]
        return res

    def _item(self, m: Meeting, n: int, row: HearingRow) -> Item:
        ref = slugify(row.address) if row.address else f"item-{n}"
        return Item(
            id=f"{m.id}#{ref}", meeting_id=m.id, source_ref=row.address or f"Item {n}",
            title=row.description, attachments=row.attachments,
            geography_hint=Geography(address=row.address or None, ward=row.ward, community_area=row.community_area),
        )
```

(The `re.match(r"\d", address)` guard turns the South Chicago framework-plan row, whose first cell has no street number, into an empty address.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_dpd.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/sources/dpd.py ingest/tests/test_dpd.py
git commit -m "Add Plan Commission and CDC adapter"
```

---
### Task 5: IHDA adapter (meetings + TEFRA, QAP and subsidy notices)

**Files:**
- Create: `ingest/digest/sources/ihda.py`, `ingest/tests/test_ihda.py`

**Interfaces:**
- Consumes: `BaseAdapter`, `window_for`, `slugify`, `parse_ampm`, `ImplausibleResult` (Task 2); `Notice` model.
- Produces: `IhdaAdapter(cfg)` (config keys `id`, `url`, `window_days` = `[-60, 90]`); `parse_page(html, base, source_id) -> tuple[list[IhdaMeeting], list[Notice]]`; dataclass `IhdaMeeting(body, date, time, agenda_url, minutes_url, notice_url)`. Meetings with an agenda set `needs_ai_extraction=True` (Task 9 extracts their items; the agenda PDFs' text layer is scrambled).

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_ihda.py`:

```python
import datetime as dt
from collections import Counter

from digest.models import CHI
from digest.sources.ihda import IhdaAdapter, parse_page
from fakes import FIX, FakeFetcher

URL = "https://www.ihda.org/about-ihda/public-meetings-and-notices/"


def page():
    return parse_page((FIX / "ihda/meetings_page.html").read_text(encoding="utf-8"), URL, "ihda")


def test_meetings_parsed_and_skips_schedule_and_livestream():
    meetings, _ = page()
    assert {(m.body, m.date) for m in meetings} == {
        ("IHDA Board", dt.date(2026, 9, 18)), ("IHDA Finance Committee", dt.date(2026, 9, 18)),
        ("IHDA Audit Committee", dt.date(2026, 9, 18)), ("IHDA Board", dt.date(2026, 7, 24)),
        ("IHDA Finance Committee", dt.date(2026, 7, 24)), ("IHDA Audit Committee", dt.date(2025, 6, 26)),
        ("IHDA Asset Management Committee", dt.date(2026, 5, 15)),
    }
    board = next(m for m in meetings if m.body == "IHDA Board" and m.date == dt.date(2026, 9, 18))
    assert board.time == dt.time(11, 0)
    assert board.agenda_url.endswith("0918-Agenda-Final.pdf")
    assert board.notice_url is not None
    finance = next(m for m in meetings if m.body == "IHDA Finance Committee" and m.date == dt.date(2026, 9, 18))
    assert finance.time == dt.time(10, 0) and finance.agenda_url.endswith("Finance-Committee-Agenda.pdf")
    july = next(m for m in meetings if m.body == "IHDA Board" and m.date == dt.date(2026, 7, 24))
    assert july.minutes_url is not None and july.agenda_url is None


def test_notices_by_section():
    _, notices = page()
    assert Counter(n.kind for n in notices) == {"hearing": 2, "comment_period": 4, "public_notice": 1}
    tefra = notices[0]
    assert tefra.title.startswith("Pheasant Ridge Hunter Apartments")
    assert tefra.deadline.isoformat() == "2026-10-15T10:00:00-05:00"
    assert tefra.id.startswith("ihda:notice:") and len(tefra.id) == len("ihda:notice:") + 12
    assert any("Qualified Allocation Plan" in n.title for n in notices if n.kind == "comment_period")
    assert next(n for n in notices if n.kind == "public_notice").title.startswith("Notice to Terminate Subsidy")


def test_adapter_window_and_extraction_flag():
    f = FakeFetcher([("public-meetings-and-notices", "ihda/meetings_page.html")])
    cfg = {"id": "ihda", "url": URL, "window_days": [-60, 90]}
    res = IhdaAdapter(cfg).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    assert sorted(m.id for m in res.meetings) == [
        "ihda:ihda-audit-committee:2026-09-18", "ihda:ihda-board:2026-09-18", "ihda:ihda-finance-committee:2026-09-18"]
    board = next(m for m in res.meetings if m.id == "ihda:ihda-board:2026-09-18")
    assert board.start.isoformat() == "2026-09-18T11:00:00-05:00"
    assert board.jurisdiction == "state" and board.needs_ai_extraction and board.status == "held"
    assert len(res.notices) == 7 and res.items == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_ihda.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.sources.ihda'`

- [ ] **Step 3: Implement**

`ingest/digest/sources/ihda.py`:

```python
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
from .base import BaseAdapter, ImplausibleResult, parse_ampm, slugify, window_for

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


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


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
        out.append((_clean(h3.get_text(" ", strip=True)).upper(), [li for li in lis if not li.find("li")]))
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
            text = _clean(li.get_text(" ", strip=True))
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_ihda.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/sources/ihda.py ingest/tests/test_ihda.py
git commit -m "Add IHDA meetings and notices adapter"
```

---

### Task 6: CLIHTF adapter

**Files:**
- Create: `ingest/digest/sources/clihtf.py`, `ingest/tests/test_clihtf.py`

**Interfaces:**
- Consumes: `BaseAdapter`, `window_for`, `html_to_text` (Task 2).
- Produces: `ClihtfAdapter(cfg)` (config key `id`). Board meetings carry the agenda as `Meeting.agenda_text` (not serialized) and `needs_ai_extraction=True`.

- [ ] **Step 1: Write the failing test**

`ingest/tests/test_clihtf.py`:

```python
import datetime as dt

from digest.models import CHI
from digest.sources.clihtf import ClihtfAdapter
from fakes import FakeFetcher


def test_board_meetings_only_with_agenda_text():
    f = FakeFetcher([("tribe/events/v1/events", "clihtf/events.json")])
    res = ClihtfAdapter({"id": "clihtf"}).fetch(f, dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI))
    assert [m.id for m in res.meetings] == ["clihtf:319068"]
    m = res.meetings[0]
    assert m.body == "CLIHTF Board of Directors"
    assert m.start.isoformat() == "2026-10-13T08:30:00-05:00"
    assert m.status == "scheduled" and m.needs_ai_extraction
    assert m.agenda_url == "https://clihtf.org/event/board-meeting-11-7/"
    assert "I. Roll Call" in m.agenda_text and "Allocations Committee" in m.agenda_text
    assert "start_date=2026-09-21" in f.calls[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_clihtf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.sources.clihtf'`

- [ ] **Step 3: Implement**

`ingest/digest/sources/clihtf.py`:

```python
"""Chicago Low-Income Housing Trust Fund via The Events Calendar REST API."""
from __future__ import annotations

import datetime as dt

from ..http import Fetcher
from ..models import CHI, Meeting, SourceResult
from .base import BaseAdapter, html_to_text, window_for

API = "https://clihtf.org/wp-json/tribe/events/v1/events"


class ClihtfAdapter(BaseAdapter):
    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        win = window_for(now, self.days)
        data = fetcher.get_json(API, {
            "start_date": f"{win.start.isoformat()} 00:00:00",
            "end_date": f"{win.end.isoformat()} 23:59:59",
            "per_page": 50,
        })
        res = SourceResult()
        for ev in data.get("events") or []:
            title = ev.get("title") or ""
            if "board meeting" not in title.lower():
                continue
            start = dt.datetime.fromisoformat(ev["start_date"]).replace(tzinfo=CHI)
            text = html_to_text(ev.get("description") or "")
            cancelled = "cancel" in title.lower()
            res.meetings.append(Meeting(
                id=f"clihtf:{ev['id']}", source_id=self.source_id, body="CLIHTF Board of Directors",
                body_id="clihtf-board", start=start,
                status="cancelled" if cancelled else ("held" if start < now else "scheduled"),
                agenda_url=ev.get("url"), detail_url=ev.get("url"),
                needs_ai_extraction=bool(text.strip()) and not cancelled, agenda_text=text or None,
            ))
        return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ingest && python -m pytest tests/test_clihtf.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/sources/clihtf.py ingest/tests/test_clihtf.py
git commit -m "Add CLIHTF board meeting adapter"
```

---

### Task 7: Page-change watcher (DOH, CHA, CLIHTF pages)

**Files:**
- Create: `ingest/digest/sources/watcher.py`, `ingest/tests/test_watcher.py`, `config/watch.yaml`

**Interfaces:**
- Consumes: `BaseAdapter`, `ImplausibleResult` (Task 2); `FetchError` (Task 1); `Notice`.
- Produces: `WatcherAdapter(cfg, pages: list[dict], snap_dir: Path)`; `snapshot(html, base) -> dict` (`{"text": str, "links": {url: label}}`); `changes(page, prev, cur, now, source_id) -> list[Notice]`. Page dicts have `id`, `title`, `body`, `url`. Snapshots live at `data/snapshots/<page id>.json`. The first fetch of a page records a baseline and emits nothing.

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_watcher.py`:

```python
import datetime as dt

from digest.http import NotFound
from digest.models import CHI
from digest.sources.watcher import WatcherAdapter, snapshot
from fakes import FIX, FakeFetcher

URL = "https://www.chicago.gov/content/city/en/depts/doh.html"
PAGE = {"id": "doh-home", "title": "DOH home", "body": "Chicago Department of Housing", "url": URL}
NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)
HTML = (FIX / "watch/doh_home.html").read_text(encoding="utf-8")


def run(tmp_path, html):
    adapter = WatcherAdapter({"id": "watch"}, [PAGE], tmp_path / "snapshots")
    return adapter.fetch(FakeFetcher([("doh.html", html.encode("utf-8"))]), NOW)


def test_snapshot_has_main_text_and_absolute_links():
    snap = snapshot(HTML, URL)
    assert "Housing" in snap["text"]
    assert "https://www.chicago.gov/content/city/en/depts/doh/supp_info/2023-qualified-allocation-plan.html" in snap["links"]
    assert not any(u.startswith(("mailto:", "#")) for u in snap["links"])


def test_first_run_is_baseline(tmp_path):
    res = run(tmp_path, HTML)
    assert res.notices == []
    assert (tmp_path / "snapshots" / "doh-home.json").exists()


def test_new_link_becomes_notice(tmp_path):
    run(tmp_path, HTML)
    changed = HTML.replace("</main>", '<a href="/content/dam/city/depts/doh/new-rfp.pdf">2027 Multifamily RFP</a></main>', 1)
    res = run(tmp_path, changed)
    assert len(res.notices) == 1
    n = res.notices[0]
    assert n.kind == "page_change" and n.title == "New on DOH home: 2027 Multifamily RFP"
    assert n.url == "https://www.chicago.gov/content/dam/city/depts/doh/new-rfp.pdf"
    assert n.id.startswith("watch:doh-home:")
    assert run(tmp_path, changed).notices == []  # no repeat once the snapshot is updated


def test_text_only_change_describes_added_lines(tmp_path):
    run(tmp_path, HTML)
    res = run(tmp_path, HTML.replace("</main>", "<p>Comment period open until November 30</p></main>", 1))
    assert len(res.notices) == 1
    assert res.notices[0].title == "DOH home page updated"
    assert "Comment period open until November 30" in res.notices[0].detail
    assert res.notices[0].url == URL


def test_one_page_failing_is_a_warning(tmp_path):
    pages = [PAGE, {**PAGE, "id": "gone", "url": "https://www.chicago.gov/gone.html"}]
    f = FakeFetcher([("gone.html", NotFound("gone", 404)), ("doh.html", HTML.encode("utf-8"))])
    res = WatcherAdapter({"id": "watch"}, pages, tmp_path / "snapshots").fetch(f, NOW)
    assert any("gone" in w for w in res.warnings)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.sources.watcher'`

- [ ] **Step 3: Implement**

`ingest/digest/sources/watcher.py`:

```python
"""Page-change watcher: new links and added text on watched pages become page_change notices."""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import FetchError, Fetcher
from ..models import Notice, SourceResult
from .base import BaseAdapter, ImplausibleResult


def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def snapshot(html: str, base: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main") or soup.find("article") or soup.body or soup
    for t in root.find_all(["script", "style", "nav", "noscript"]):
        t.decompose()
    lines = [re.sub(r"\s+", " ", line).strip() for line in root.get_text("\n").replace("\xa0", " ").splitlines()]
    links: dict[str, str] = {}
    for a in root.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base, href).split("#")[0]
        links.setdefault(url, re.sub(r"\s+", " ", a.get_text(" ", strip=True)) or url)
    return {"text": "\n".join(line for line in lines if line), "links": links}


def changes(page: dict, prev: dict | None, cur: dict, now: dt.datetime, source_id: str) -> list[Notice]:
    if prev is None:
        return []
    out = [
        Notice(id=f"{source_id}:{page['id']}:{_h(url)}", source_id=source_id, body=page["body"],
               kind="page_change", title=f"New on {page['title']}: {label}", url=url, detail=label, posted_at=now)
        for url, label in cur["links"].items() if url not in prev["links"]
    ]
    if not out and cur["text"] != prev["text"]:
        added = [
            line[1:] for line in difflib.unified_diff(prev["text"].splitlines(), cur["text"].splitlines(), lineterm="", n=0)
            if line.startswith("+") and not line.startswith("+++")
        ]
        if added:
            detail = "\n".join(added)[:2000]
            out.append(Notice(id=f"{source_id}:{page['id']}:{_h(detail)}", source_id=source_id, body=page["body"],
                              kind="page_change", title=f"{page['title']} page updated", url=page["url"],
                              detail=detail, posted_at=now))
    return out


class WatcherAdapter(BaseAdapter):
    def __init__(self, cfg: dict, pages: list[dict], snap_dir: Path):
        super().__init__(cfg)
        self.pages = pages
        self.snap_dir = snap_dir

    def fetch(self, fetcher: Fetcher, now: dt.datetime) -> SourceResult:
        res = SourceResult()
        ok = 0
        for page in self.pages:
            try:
                html = fetcher.get_text(page["url"])
            except FetchError as e:
                res.warnings.append(f"watch page {page['id']} failed: {e}")
                continue
            ok += 1
            path = self.snap_dir / f"{page['id']}.json"
            prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            cur = snapshot(html, page["url"])
            res.notices += changes(page, prev, cur, now, self.source_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cur, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        if self.pages and ok == 0:
            raise ImplausibleResult("every watched page failed")
        return res
```

`config/watch.yaml`:

```yaml
# Pages whose changes become page_change notices. Add pages freely; the first run of a page records a baseline.
pages:
  - {id: doh-home, title: Department of Housing home, body: Chicago Department of Housing,
     url: "https://www.chicago.gov/content/city/en/depts/doh.html"}
  - {id: doh-reports, title: "DOH Data, Plans and Reports", body: Chicago Department of Housing,
     url: "https://www.chicago.gov/content/city/en/depts/doh/provdrs/goals_reports.html"}
  - {id: doh-qap, title: DOH Qualified Allocation Plan, body: Chicago Department of Housing,
     url: "https://www.chicago.gov/content/city/en/depts/doh/supp_info/2023-qualified-allocation-plan.html"}
  - {id: cha-public-notices, title: CHA public notices, body: Chicago Housing Authority,
     url: "https://www.thecha.org/news/category/public-notice"}
  - {id: cha-public-comment, title: CHA public comment, body: Chicago Housing Authority,
     url: "https://www.thecha.org/board-commissioners/public-comment"}
  - {id: clihtf-quarterly, title: CLIHTF quarterly reports, body: CLIHTF,
     url: "https://clihtf.org/about-history/quarterly-reports/"}
  - {id: clihtf-annual, title: CLIHTF annual reports, body: CLIHTF,
     url: "https://clihtf.org/about-history/annual-reports/"}
  - {id: clihtf-landlord, title: CLIHTF landlord notices, body: CLIHTF,
     url: "https://clihtf.org/for-owners/landlord-notices/"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_watcher.py -v`
Expected: 5 passed

- [ ] **Step 5: Check every watched URL loads (live, read-only)**

```bash
cd ingest && python - <<'EOF'
import yaml
from digest.http import HttpFetcher
from digest.sources.watcher import snapshot
f = HttpFetcher()
for p in yaml.safe_load(open("../config/watch.yaml", encoding="utf-8"))["pages"]:
    try:
        s = snapshot(f.get_text(p["url"]), p["url"])
        print("OK ", p["id"], len(s["text"]), "chars", len(s["links"]), "links")
    except Exception as e:
        print("BAD", p["id"], e)
EOF
```

Expected: every line `OK` with more than 200 chars of text. Remove any `BAD` page from `watch.yaml` and list it in the task report (do not guess a replacement URL).

- [ ] **Step 6: Commit**

```bash
git add ingest/digest/sources/watcher.py ingest/tests/test_watcher.py config/watch.yaml
git commit -m "Add page-change watcher for DOH, CHA and CLIHTF pages"
```

---

### Task 8: Categories and geography

**Files:**
- Create: `config/categories.yaml`, `ingest/digest/categories.py`, `ingest/digest/geo.py`, `ingest/scripts/fetch_boundaries.py`, `config/geo/wards.geojson`, `config/geo/community_areas.geojson`, `ingest/tests/test_categories.py`, `ingest/tests/test_geo.py`

**Interfaces:**
- Consumes: `CategoryHit`, `Geography` (Task 1); `Fetcher`, `FetchError`; `JsonCache`.
- Produces:
  - `digest.categories`: `Category(id, label, description, keywords, bodies)`, `Categories(path)` with `.items`, `.by_id`, `.fingerprint() -> str`, `.rule_hits(body, texts) -> list[CategoryHit]` (body rule first, else first matching keyword), `.prompt_block() -> str`; `keyword_match(kw, text) -> bool`; `merge_hits(*groups) -> list[CategoryHit]` (first hit per id wins).
  - `digest.geo`: `Geo(wards_path, areas_path, fetcher, cache: JsonCache | None)` with `.locate(address) -> Geography` and `.geocode(address) -> tuple[float, float] | None` (lon, lat).
  - Body names in `categories.yaml` must equal the meeting `body` values adapters emit (Tasks 2–6 and `config/sources.yaml` in Task 12) and notice `body` values (`"IHDA"`, watcher page bodies).

- [ ] **Step 1: Write the categories config**

`config/categories.yaml` (scope text, keywords and bodies copied from spec §6):

```yaml
# IFE housing projects. The model assigns zero or more of these; body and keyword rules add them deterministically.
# Editing this file changes its fingerprint, which re-enriches every record on the next run (a cost, not a migration).
categories:
  - id: cha
    label: CHA & public housing
    description: CHA actions, redevelopment and production, RAD and Section 18 conversions, disposition, public-housing property tax/PILOT, Gautreaux.
    keywords: [CHA, Chicago Housing Authority, RAD, Section 18, disposition, PILOT, Gautreaux, ACOP]
    bodies: [CHA Board of Commissioners, CHA Finance & Audit Committee, CHA Real Estate Operations Development Committee, CHA Tenant Services Committee, Chicago Housing Authority]
  - id: social-housing
    label: Social housing
    description: Public or mixed-income developer models, acquisition and revolving funds, land trusts, disposition of public land for housing, CLIHTF.
    keywords: [social housing, mixed-income, land trust, revolving fund, acquisition fund, CLIHTF]
    bodies: [CLIHTF Board of Directors, CLIHTF]
  - id: ahpaa
    label: Zoning & housing supply
    description: Rezonings and planned developments with residential units, city-initiated rezonings, ADUs and missing middle, zoning text amendments; suburban housing plans and AHPAA actions.
    keywords: [rezoning, planned development, upzoning, AHPAA, missing middle]
    bodies: [Chicago Plan Commission]
  - id: property-taxes
    label: Property taxes
    description: Property tax incentive classes for housing, tax sale and scavenger sale, Land Bank, PILOT agreements, assessment appeals.
    keywords: [Class 7, Class 9, Class L, tax sale, scavenger, Land Bank, PILOT]
    bodies: []
  - id: state-agenda
    label: State housing finance
    description: IHDA actions, tax-exempt bonds and TEFRA hearings, QAPs, private activity bond volume cap, LIHTC awards.
    keywords: [IHDA, TEFRA, QAP, volume cap, LIHTC, private activity bond]
    bodies: [IHDA Board, IHDA Finance Committee, IHDA Audit Committee, IHDA Asset Management Committee, IHDA]
  - id: healthy-homes
    label: Healthy homes & tenant protections
    description: Building code, lead, inspections, receivership, demolition, heat and habitability; eviction, right to counsel, RLTO, relocation, foreclosure tenant protections.
    keywords: [lead, receivership, heat, habitability, eviction, right to counsel, Keep Chicago Renting]
    bodies: []
  - id: housing-agenda
    label: Affordable housing deals & policy
    description: City financing for affordable deals, the ARO, TIF for housing, Department of Housing plans and RFPs, citywide housing ordinances and budget.
    keywords: [Department of Housing, ARO, Affordable Requirements Ordinance, TIF]
    bodies: [Chicago Department of Housing]
  - id: cls-housing
    label: Criminal legal system & housing
    description: Criminal-background screening in housing, Just Housing, crime-free and nuisance-property ordinances, reentry and supportive housing, sealing/expungement as it bears on housing.
    keywords: [Just Housing, criminal background, crime-free, nuisance, reentry, sealing, expungement]
    bodies: []
```

(`Chicago Housing Authority`, `CLIHTF`, `IHDA` and `Chicago Department of Housing` are notice bodies — watcher pages and IHDA notices — so their notices get the matching category.)

- [ ] **Step 2: Write the failing categories tests**

`ingest/tests/test_categories.py`:

```python
from pathlib import Path

from digest.categories import Categories, keyword_match, merge_hits
from digest.models import CategoryHit

CATS = Categories(Path(__file__).resolve().parents[2] / "config" / "categories.yaml")


def test_eight_ife_categories():
    assert [c.id for c in CATS.items] == [
        "cha", "social-housing", "ahpaa", "property-taxes", "state-agenda",
        "healthy-homes", "housing-agenda", "cls-housing"]


def test_body_rule_wins_over_keyword():
    hits = CATS.rule_hits("CHA Board of Commissioners", ["Section 18 disposition"])
    assert CategoryHit(id="cha", via="body", detail="CHA Board of Commissioners") in hits


def test_keyword_rules():
    hits = {h.id: h for h in CATS.rule_hits("City Council Committee on Finance", ["Class 7 incentive for 1200 S. State"])}
    assert hits["property-taxes"].via == "keyword" and hits["property-taxes"].detail == "Class 7"


def test_acronyms_are_case_sensitive_and_word_bounded():
    assert not keyword_match("PILOT", "a pilot program")
    assert keyword_match("PILOT", "payment in lieu of taxes (PILOT) agreement")
    assert not keyword_match("CHA", "CHAIR of the committee")
    assert keyword_match("eviction", "Eviction diversion")
    assert not keyword_match("lead", "leadership")


def test_merge_hits_first_per_id_wins():
    a = [CategoryHit(id="cha", via="body")]
    b = [CategoryHit(id="cha", via="model"), CategoryHit(id="ahpaa", via="model")]
    assert [(h.id, h.via) for h in merge_hits(a, b)] == [("cha", "body"), ("ahpaa", "model")]


def test_prompt_block_and_fingerprint():
    assert "- cha: CHA & public housing." in CATS.prompt_block()
    assert len(CATS.fingerprint()) == 12
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_categories.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.categories'`

- [ ] **Step 4: Implement categories**

`ingest/digest/categories.py`:

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import CategoryHit


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    description: str
    keywords: tuple[str, ...]
    bodies: tuple[str, ...]


def keyword_match(kw: str, text: str | None) -> bool:
    """Word-bounded. Short all-caps acronyms (CHA, PILOT, ARO) match case-sensitively; everything else ignores case."""
    flags = 0 if (kw.isupper() and len(kw) <= 6) else re.IGNORECASE
    return re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", text or "", flags) is not None


def merge_hits(*groups: list[CategoryHit]) -> list[CategoryHit]:
    out: dict[str, CategoryHit] = {}
    for group in groups:
        for h in group:
            out.setdefault(h.id, h)
    return list(out.values())


class Categories:
    def __init__(self, path: Path):
        self.path = path
        self._raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(self._raw)
        self.items = [
            Category(c["id"], c["label"], c["description"], tuple(c.get("keywords") or ()), tuple(c.get("bodies") or ()))
            for c in data["categories"]
        ]
        self.by_id = {c.id: c for c in self.items}

    def fingerprint(self) -> str:
        return hashlib.sha256(self._raw.encode("utf-8")).hexdigest()[:12]

    def rule_hits(self, body: str | None, texts: list[str]) -> list[CategoryHit]:
        hits = []
        for c in self.items:
            if body and body in c.bodies:
                hits.append(CategoryHit(id=c.id, via="body", detail=body))
                continue
            kw = next((k for k in c.keywords if any(keyword_match(k, t) for t in texts)), None)
            if kw:
                hits.append(CategoryHit(id=c.id, via="keyword", detail=kw))
        return hits

    def prompt_block(self) -> str:
        return "\n".join(f"- {c.id}: {c.label}. {c.description}" for c in self.items)
```

- [ ] **Step 5: Run categories tests**

Run: `cd ingest && python -m pytest tests/test_categories.py -v`
Expected: 6 passed

- [ ] **Step 6: Download the boundary files**

`ingest/scripts/fetch_boundaries.py`:

```python
"""Download Chicago ward and community-area boundaries into config/geo/ (run once; files are committed)."""
import json
from pathlib import Path

from digest.http import HttpFetcher

OUT = Path(__file__).resolve().parents[2] / "config" / "geo"
SETS = {
    "wards.geojson": ("https://data.cityofchicago.org/resource/p293-wvbd.geojson", "ward", 50),
    "community_areas.geojson": ("https://data.cityofchicago.org/resource/igwz-8jzy.geojson", "community", 77),
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    f = HttpFetcher()
    for name, (url, prop, expected) in SETS.items():
        data = f.get_json(url, {"$limit": 500})
        feats = data["features"]
        assert len(feats) == expected, f"{name}: {len(feats)} features, expected {expected}"
        assert all(prop in ft["properties"] for ft in feats), f"{name}: missing property {prop}"
        (OUT / name).write_text(json.dumps(data), encoding="utf-8")
        print(f"{name}: {len(feats)} features")
```

Run: `cd ingest && python scripts/fetch_boundaries.py`
Expected: `wards.geojson: 50 features` and `community_areas.geojson: 77 features`

- [ ] **Step 7: Write the failing geo tests**

`ingest/tests/test_geo.py`:

```python
import json

from digest.cache import JsonCache
from digest.geo import Geo
from fakes import FakeFetcher


def square(x0, y0, props):
    ring = [[x0, y0], [x0 + 1, y0], [x0 + 1, y0 + 1], [x0, y0 + 1], [x0, y0]]
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def make_geo(tmp_path, routes):
    w = tmp_path / "w.geojson"
    a = tmp_path / "a.geojson"
    w.write_text(json.dumps({"type": "FeatureCollection", "features": [square(0, 0, {"ward": "27"}), square(1, 0, {"ward": "44"})]}))
    a.write_text(json.dumps({"type": "FeatureCollection", "features": [square(0, 0, {"community": "NEAR WEST SIDE"})]}))
    f = FakeFetcher(routes)
    return Geo(w, a, f, JsonCache(tmp_path / "geocode.json")), f


CENSUS_HIT = json.dumps({"result": {"addressMatches": [{"coordinates": {"x": 0.5, "y": 0.5}}]}}).encode()
CENSUS_MISS = json.dumps({"result": {"addressMatches": []}}).encode()
NOMINATIM_HIT = json.dumps([{"lat": "0.5", "lon": "1.5"}]).encode()


def test_census_hit_assigns_ward_and_area(tmp_path):
    geo, _ = make_geo(tmp_path, [("geocoding.geo.census.gov", CENSUS_HIT)])
    g = geo.locate("1133 W. Kinzie St.")
    assert (g.address, g.ward, g.community_area) == ("1133 W. Kinzie St.", 27, "Near West Side")


def test_census_miss_falls_back_to_nominatim_and_caches(tmp_path):
    geo, f = make_geo(tmp_path, [("geocoding.geo.census.gov", CENSUS_MISS), ("nominatim.openstreetmap.org", NOMINATIM_HIT)])
    assert geo.locate("3700-3846 N. Seminary Ave.").ward == 44
    assert any("3700 N. Seminary Ave., Chicago, IL" in c for c in f.calls)  # address range reduced to its first number
    n = len(f.calls)
    geo.locate("3700-3846 N. Seminary Ave.")
    assert len(f.calls) == n


def test_no_geocode_returns_address_only(tmp_path):
    geo, _ = make_geo(tmp_path, [("geocoding.geo.census.gov", CENSUS_MISS), ("nominatim.openstreetmap.org", b"[]")])
    g = geo.locate("1 Nowhere Pl.")
    assert (g.address, g.ward, g.community_area) == ("1 Nowhere Pl.", None, None)
```

- [ ] **Step 8: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_geo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.geo'`

- [ ] **Step 9: Implement geo**

`ingest/digest/geo.py`:

```python
"""Address → (ward, community area). Census geocoder first, Nominatim fallback, point-in-polygon on city boundaries."""
from __future__ import annotations

import json
import re
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.prepared import prep

from .cache import JsonCache
from .http import FetchError, Fetcher
from .models import Geography

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _load(path: Path, prop: str) -> list[tuple[object, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(prep(shape(f["geometry"])), str(f["properties"][prop])) for f in data["features"] if f.get("geometry")]


def _which(polys: list[tuple[object, str]], pt: Point) -> str | None:
    return next((value for poly, value in polys if poly.contains(pt)), None)


def normalize_address(address: str) -> str:
    a = address.strip().rstrip(",").strip()
    return re.sub(r"^(\d+)-\d+", r"\1", a)  # "3700-3846 N. Seminary" → "3700 N. Seminary"


class Geo:
    def __init__(self, wards_path: Path, areas_path: Path, fetcher: Fetcher, cache: JsonCache | None = None):
        self.wards = _load(wards_path, "ward")
        self.areas = _load(areas_path, "community")
        self.fetcher = fetcher
        self.cache = cache

    def locate(self, address: str) -> Geography:
        pt = self.geocode(address)
        if pt is None:
            return Geography(address=address)
        p = Point(pt)
        ward = _which(self.wards, p)
        area = _which(self.areas, p)
        return Geography(address=address, ward=int(ward) if ward else None,
                         community_area=area.title() if area else None)

    def geocode(self, address: str) -> tuple[float, float] | None:
        q = f"{normalize_address(address)}, Chicago, IL"
        if self.cache is not None and (hit := self.cache.get(q)) is not None:
            return tuple(hit) if hit else None
        pt = self._census(q) or self._nominatim(q)
        if self.cache is not None:
            self.cache.set(q, list(pt) if pt else [])
        return pt

    def _census(self, q: str) -> tuple[float, float] | None:
        try:
            data = self.fetcher.get_json(CENSUS, {"address": q, "benchmark": "Public_AR_Current", "format": "json"})
        except FetchError:
            return None
        matches = data.get("result", {}).get("addressMatches") or []
        return (matches[0]["coordinates"]["x"], matches[0]["coordinates"]["y"]) if matches else None

    def _nominatim(self, q: str) -> tuple[float, float] | None:
        try:
            data = self.fetcher.get_json(NOMINATIM, {"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "us"})
        except FetchError:
            return None
        return (float(data[0]["lon"]), float(data[0]["lat"])) if data else None
```

(A miss is cached as `[]` so a known-bad address is not re-geocoded every run; delete its key from `data/cache/geocode.json` to retry. The ward property in the city file may be a string such as `"27"`; `int()` handles it.)

- [ ] **Step 10: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_categories.py tests/test_geo.py -v`
Expected: 9 passed

- [ ] **Step 11: Commit**

```bash
git add config/categories.yaml config/geo ingest/digest/categories.py ingest/digest/geo.py ingest/scripts ingest/tests/test_categories.py ingest/tests/test_geo.py
git commit -m "Add IFE project categories and ward/community-area geography"
```

---

### Task 9: Claude wrapper and AI agenda extraction

**Files:**
- Create: `ingest/digest/llm.py`, `ingest/digest/extract.py`, `ingest/tests/test_llm.py`, `ingest/tests/test_extract.py`

**Interfaces:**
- Consumes: `Meeting`, `Item`, `Attachment` (Task 1); `Fetcher`, `JsonCache` (Task 1); `slugify` (Task 2). `FakeLLM` (Task 1) has the same `parse` signature as `LLM`.
- Produces:
  - `digest.llm`: `LLM(client, models: dict[str, str], usage_path: Path)` with `.parse(*, purpose: str, model_key: str, system: str, content: str | list[dict], schema: type[BaseModel], max_tokens: int = 16000) -> BaseModel`; `LLMRefused(Exception)`, raised when the model refuses or returns no valid output. Model keys are `screen`, `summarize`, `extract` (from `config/ai.yaml`). Each call appends one JSON line to `data/usage.jsonl`: `{"ts", "purpose", "model", "input_tokens", "output_tokens"}`.
  - `digest.extract`: `ExtractedItem(number, title)`, `AgendaExtraction(items)`, `extract_items(meeting, fetcher, llm, cache: JsonCache) -> list[Item]`. Cache key is the meeting ID; the cached value is `{"hash": <sha256[:16] of the agenda input>, "items": [...]}`.

- [ ] **Step 1: Confirm the installed SDK has `messages.parse`**

```bash
python -c "import anthropic; c = anthropic.Anthropic(api_key='x'); print(anthropic.__version__, hasattr(c.messages, 'parse'))"
```

Expected: a version number and `True`. If it prints `False`, run `pip install -U anthropic` and pin the installed version as the floor in `ingest/pyproject.toml` (`"anthropic>=<version>"`).

- [ ] **Step 2: Write the failing LLM wrapper tests**

`ingest/tests/test_llm.py`:

```python
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from digest.llm import LLM, LLMRefused


class Verdict(BaseModel):
    ok: bool


class FakeMessages:
    def __init__(self, resp):
        self.resp = resp
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def make(tmp_path, stop_reason="end_turn", parsed=Verdict(ok=True)):
    resp = SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed,
                           usage=SimpleNamespace(input_tokens=120, output_tokens=15))
    msgs = FakeMessages(resp)
    llm = LLM(SimpleNamespace(messages=msgs), {"screen": "claude-haiku-5-5"}, tmp_path / "usage.jsonl")
    return llm, msgs


def test_parse_passes_schema_and_logs_usage(tmp_path):
    llm, msgs = make(tmp_path)
    out = llm.parse(purpose="screen", model_key="screen", system="sys", content="hello", schema=Verdict, max_tokens=8000)
    assert out == Verdict(ok=True)
    assert msgs.kwargs["model"] == "claude-haiku-5-5"
    assert msgs.kwargs["output_format"] is Verdict
    assert msgs.kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert msgs.kwargs["max_tokens"] == 8000 and msgs.kwargs["system"] == "sys"
    row = json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert (row["purpose"], row["model"], row["input_tokens"], row["output_tokens"]) == ("screen", "claude-haiku-5-5", 120, 15)


def test_refusal_raises_and_still_logs(tmp_path):
    llm, _ = make(tmp_path, stop_reason="refusal", parsed=None)
    with pytest.raises(LLMRefused):
        llm.parse(purpose="screen", model_key="screen", system="s", content="c", schema=Verdict)
    assert (tmp_path / "usage.jsonl").exists()


def test_missing_parsed_output_raises(tmp_path):
    llm, _ = make(tmp_path, stop_reason="max_tokens", parsed=None)
    with pytest.raises(LLMRefused, match="max_tokens"):
        llm.parse(purpose="screen", model_key="screen", system="s", content="c", schema=Verdict)
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.llm'`

- [ ] **Step 4: Implement the wrapper**

`ingest/digest/llm.py`:

```python
"""Thin wrapper over client.messages.parse: one place for model names, refusals and the usage log."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError


class LLMRefused(Exception):
    """The model refused, or returned no output that validates against the schema."""


class LLM:
    def __init__(self, client, models: dict[str, str], usage_path: Path):
        self.client = client
        self.models = models
        self.usage_path = usage_path

    def parse(self, *, purpose: str, model_key: str, system: str, content, schema: type[BaseModel],
              max_tokens: int = 16000) -> BaseModel:
        model = self.models[model_key]
        try:
            resp = self.client.messages.parse(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": content}], output_format=schema,
            )
        except ValidationError as e:  # truncated or malformed JSON
            raise LLMRefused(f"{purpose}: output did not validate: {e}") from e
        self._log(purpose, model, resp.usage)
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise LLMRefused(f"{purpose}: no parsed output (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    def _log(self, purpose: str, model: str, usage) -> None:
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "purpose": purpose, "model": model,
               "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
        with self.usage_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
```

- [ ] **Step 5: Run the wrapper tests**

Run: `cd ingest && python -m pytest tests/test_llm.py -v`
Expected: 3 passed

- [ ] **Step 6: Write the failing extraction tests**

`ingest/tests/test_extract.py`:

```python
import datetime as dt

from digest.cache import JsonCache
from digest.extract import AgendaExtraction, ExtractedItem, extract_items
from digest.models import CHI, Meeting
from fakes import FakeFetcher, FakeLLM

START = dt.datetime(2026, 10, 13, 8, 30, tzinfo=CHI)
AGENDA = "I. Roll Call\nII. Approval of Minutes\nIII. Allocations Committee report\nIV. 2027 budget"


def clihtf_meeting(text=AGENDA):
    return Meeting(id="clihtf:319068", source_id="clihtf", body="CLIHTF Board of Directors", body_id="clihtf-board",
                   start=START, agenda_url="https://clihtf.org/event/board-meeting-11-7/", agenda_text=text,
                   needs_ai_extraction=True)


def rows(*pairs):
    return AgendaExtraction(items=[ExtractedItem(number=n, title=t) for n, t in pairs])


def test_extract_from_text_and_cache(tmp_path):
    llm = FakeLLM({"extract": [rows(("III.", "Allocations Committee report"), ("IV.", "2027 budget"))]})
    cache = JsonCache(tmp_path / "extract.json")
    items = extract_items(clihtf_meeting(), FakeFetcher([]), llm, cache)
    assert [i.id for i in items] == ["clihtf:319068#iii", "clihtf:319068#iv"]
    assert items[0].source_ref == "III" and items[0].title == "Allocations Committee report"
    assert items[0].extraction == "ai_extracted"
    assert items[0].attachments[0].label == "Agenda"
    assert "Allocations Committee report" in llm.calls[0]["content"]
    assert llm.calls[0]["model_key"] == "extract"
    again = extract_items(clihtf_meeting(), FakeFetcher([]), llm, cache)  # FakeLLM list is empty: a call would fail
    assert [i.id for i in again] == [i.id for i in items] and len(llm.calls) == 1


def test_changed_agenda_invalidates_cache(tmp_path):
    llm = FakeLLM({"extract": [rows(("1", "A")), rows(("1", "A"), ("2", "B"))]})
    cache = JsonCache(tmp_path / "extract.json")
    extract_items(clihtf_meeting(), FakeFetcher([]), llm, cache)
    items = extract_items(clihtf_meeting(AGENDA + "\nV. New business"), FakeFetcher([]), llm, cache)
    assert len(items) == 2 and len(llm.calls) == 2


def test_extract_from_pdf_sends_document_block(tmp_path):
    m = Meeting(id="ihda:ihda-board:2026-09-18", source_id="ihda", body="IHDA Board", body_id="ihda-board",
                start=dt.datetime(2026, 9, 18, 11, 0, tzinfo=CHI),
                agenda_url="https://www.ihda.org/wp-content/uploads/2026/09/0918-Agenda-Final.pdf", needs_ai_extraction=True)
    f = FakeFetcher([("0918-Agenda-Final.pdf", "ihda/board_agenda_2026_09_18.pdf")])
    llm = FakeLLM({"extract": [rows(("1.", "Pheasant Ridge Hunter Apartments: tax-exempt bond financing"))]})
    items = extract_items(m, f, llm, JsonCache(tmp_path / "extract.json"))
    block = llm.calls[0]["content"][0]
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf"
    assert "\n" not in block["source"]["data"]
    assert items[0].id == "ihda:ihda-board:2026-09-18#1"


def test_unnumbered_and_duplicate_numbers_get_unique_ids(tmp_path):
    llm = FakeLLM({"extract": [rows(("", "First"), ("", "Second"), ("A", "Third"), ("A.", "Fourth"))]})
    items = extract_items(clihtf_meeting(), FakeFetcher([]), llm, JsonCache(tmp_path / "e.json"))
    assert [i.id.split("#")[1] for i in items] == ["1", "2", "a", "a-2"]
```

- [ ] **Step 7: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.extract'`

- [ ] **Step 8: Implement extraction**

`ingest/digest/extract.py`:

```python
"""Claude reads agendas that have no structured items (IHDA PDFs, CLIHTF event text) and lists the business items."""
from __future__ import annotations

import base64
import hashlib
from collections import Counter

from pydantic import BaseModel

from .cache import JsonCache
from .http import Fetcher
from .models import Attachment, Item, Meeting
from .sources.base import slugify


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
    if cached and cached.get("hash") == h:
        rows = [ExtractedItem(**r) for r in cached["items"]]
    else:
        rows = llm.parse(purpose="extract", model_key="extract", system=SYSTEM, content=content,
                         schema=AgendaExtraction, max_tokens=16000).items
        cache.set(meeting.id, {"hash": h, "items": [r.model_dump() for r in rows]})

    attachments = [Attachment(label="Agenda", url=meeting.agenda_url)] if meeting.agenda_url else []
    seen: Counter[str] = Counter()
    items = []
    for i, r in enumerate(rows, 1):
        ref = r.number.strip().rstrip(".").strip() or str(i)
        slug = slugify(ref)
        seen[slug] += 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        items.append(Item(id=f"{meeting.id}#{slug}", meeting_id=meeting.id, source_ref=ref, title=r.title.strip(),
                          extraction="ai_extracted", attachments=list(attachments)))
    return items
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_llm.py tests/test_extract.py -v`
Expected: 7 passed

- [ ] **Step 10: Commit**

```bash
git add ingest/digest/llm.py ingest/digest/extract.py ingest/tests/test_llm.py ingest/tests/test_extract.py
git commit -m "Add Claude wrapper with usage log and AI agenda extraction"
```

---

### Task 10: Enrichment (screen, summary, grounding, categories, geography, overrides)

**Files:**
- Create: `ingest/digest/enrich.py`, `ingest/tests/test_enrich.py`

**Interfaces:**
- Consumes: `LLMRefused` (Task 9); `Categories`, `merge_hits` (Task 8); `Geo` (Task 8, any object with `.locate(address) -> Geography` works); `pdf_text`, `has_text_layer` (Task 2); `html_to_text` (Task 2); `JsonCache`, `Fetcher`, `FetchError` (Task 1); `Digest`, `Item`, `Notice`, `Meeting`, `Enrichment`, `CategoryHit`, `Geography`, `Figures` (Task 1).
- Produces:
  - `Screen(housing_relevant, reason)`, `Summary(housing_relevant, relevance_reason, summary, categories, address, entities, units, affordable_units)`.
  - `Record(id, body, title, context, urls, extra_text="", hint=None)`; `record_for_item(item, meeting) -> Record`; `record_for_notice(notice) -> Record`.
  - `EnrichContext(llm, fetcher, categories, geo, cache, overrides, screen_bodies, models, char_cap=12000, max_attachments=2)`.
  - `content_hash(title, sources, fingerprint) -> str`; `gather_sources(fetcher, urls, char_cap, max_attachments) -> tuple[str, list[dict], str]` (text, document blocks, source_quality); `resolve_geography(hint, address, geo) -> Geography`; `apply_overrides(enrichment, override) -> Enrichment`; `enrich_record(record, ctx) -> Enrichment`; `enrich_digest(digest, ctx) -> Digest` (sets `.enrichment` on every item and notice, in place).
  - Cache: `data/cache/enrich.json`, keyed by record ID, value = the pre-override `Enrichment` as JSON. Failed records are never cached. Overrides (`config/overrides.yaml`, Task 12) are applied after the cache on every run.

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_enrich.py`:

```python
import datetime as dt
import io
from pathlib import Path

from pypdf import PdfWriter

from digest.cache import JsonCache
from digest.categories import Categories
from digest.enrich import (EnrichContext, Screen, Summary, enrich_digest, enrich_record, record_for_item,
                           record_for_notice)
from digest.llm import LLMRefused
from digest.models import CHI, Attachment, Digest, Geography, Item, Meeting, Notice
from fakes import FakeFetcher, FakeLLM

CATS = Categories(Path(__file__).resolve().parents[2] / "config" / "categories.yaml")
NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)
MODELS = {"screen": "claude-haiku-5-5", "summarize": "claude-sonnet-5-5", "extract": "claude-sonnet-5-5"}
PD_HTML = b"<html><body><p>MKR Owner LLC proposes 1,200 units at 1133 W. Kinzie St., 240 of them affordable.</p></body></html>"

CPC = Meeting(id="cpc:2026-10-15", source_id="cpc", body="Chicago Plan Commission", body_id="cpc",
              start=dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI))
ZONING = Meeting(id="elms:Z1", source_id="elms-zoning", body="City Council Committee on Zoning", body_id="z",
                 start=dt.datetime(2026, 10, 14, 10, 0, tzinfo=CHI))


class StubGeo:
    def __init__(self):
        self.calls = []

    def locate(self, address):
        self.calls.append(address)
        return Geography(address=address, ward=27, community_area="Near West Side")


def pd_item(url="https://x/pd.html", item_id="cpc:2026-10-15#1133-w-kinzie-st"):
    return Item(id=item_id, meeting_id=CPC.id, source_ref="1133 W. Kinzie St.",
                title="A proposed Planned Development, submitted by MKR Owner LLC, for 1133 W. Kinzie St.",
                attachments=[Attachment(label="PD Application", url=url)])


def summary(**kw):
    base = dict(housing_relevant=True, relevance_reason="Adds homes.", summary="MKR Owner LLC proposes 1,200 homes.",
                categories=["ahpaa", "not-a-category", "housing-agenda"], address="1133 W. Kinzie St.",
                entities=["MKR Owner LLC", "Invented Partners LP"], units=1200, affordable_units=999)
    return Summary(**{**base, **kw})


def ctx(tmp_path, llm, routes=(("pd.html", PD_HTML),), overrides=None, geo=None):
    return EnrichContext(llm=llm, fetcher=FakeFetcher(list(routes)), categories=CATS, geo=geo,
                         cache=JsonCache(tmp_path / "enrich.json"), overrides=overrides or {},
                         screen_bodies={"City Council Committee on Zoning"}, models=MODELS)


def test_screened_out_council_item_skips_summary(tmp_path):
    llm = FakeLLM({"screen": [Screen(housing_relevant=False, reason="A sign permit.")], "summarize": []})
    it = Item(id="elms:Z1#Or2026-1", meeting_id=ZONING.id, source_ref="Or2026-1", title="Sign permit at 100 W. Madison St.")
    e = enrich_record(record_for_item(it, ZONING), ctx(tmp_path, llm))
    assert e.status == "screened_out" and not e.housing_relevant
    assert e.relevance_reason == "A sign permit." and e.model == "claude-haiku-5-5"
    assert [c["purpose"] for c in llm.calls] == ["screen"]


def test_ok_path_grounds_entities_numbers_categories_and_address(tmp_path):
    geo = StubGeo()
    llm = FakeLLM({"summarize": [summary()]})
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, geo=geo))
    assert e.status == "ok" and e.housing_relevant and e.model == "claude-sonnet-5-5"
    assert e.entities == ["MKR Owner LLC"]                       # invented entity dropped
    assert (e.figures.units, e.figures.affordable_units) == (1200, None)  # "1,200" is grounded; 999 is not
    assert [(c.id, c.via) for c in e.categories] == [("ahpaa", "body"), ("housing-agenda", "model")]
    assert e.source_quality == "full"
    assert geo.calls == ["1133 W. Kinzie St."] and e.geography.ward == 27
    assert "1,200 units" in llm.calls[0]["content"][-1]["text"]
    assert "- cha: CHA & public housing." in llm.calls[0]["system"]


def test_ungrounded_address_is_not_geocoded(tmp_path):
    geo = StubGeo()
    llm = FakeLLM({"summarize": [summary(address="99 Imaginary Blvd.")]})
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, geo=geo))
    assert geo.calls == [] and e.geography.ward is None


def test_hint_with_ward_and_area_is_used_without_geocoding(tmp_path):
    geo = StubGeo()
    it = pd_item()
    it.geography_hint = Geography(address="1207 W. Leland Ave.", ward=46, community_area="Uptown")
    e = enrich_record(record_for_item(it, CPC), ctx(tmp_path, FakeLLM({"summarize": [summary()]}), geo=geo))
    assert geo.calls == [] and (e.geography.ward, e.geography.community_area) == (46, "Uptown")


def test_cache_reuse_and_invalidation(tmp_path):
    llm = FakeLLM({"summarize": lambda content: summary()})
    c = ctx(tmp_path, llm, routes=[("pd", PD_HTML)])
    enrich_record(record_for_item(pd_item(), CPC), c)
    enrich_record(record_for_item(pd_item(), CPC), c)
    assert len(llm.calls) == 1
    enrich_record(record_for_item(pd_item(url="https://x/pd-v2.html"), CPC), c)  # new attachment → new hash
    assert len(llm.calls) == 2


def test_refusal_marks_failed_and_continues(tmp_path):
    llm = FakeLLM({"summarize": [LLMRefused("refusal"), summary()]})
    c = ctx(tmp_path, llm, routes=[("pd", PD_HTML)])
    a = pd_item(item_id="cpc:2026-10-15#a")
    b = pd_item(item_id="cpc:2026-10-15#b")
    d = enrich_digest(Digest(generated_at=NOW, meetings=[CPC], items=[a, b]), c)
    assert [i.enrichment.status for i in d.items] == ["failed", "ok"]
    assert [h.id for h in d.items[0].enrichment.categories] == ["ahpaa"]  # rule hits survive a failure
    assert c.cache.get("cpc:2026-10-15#a") is None and c.cache.get("cpc:2026-10-15#b") is not None
    llm.responses["summarize"] = [summary()]
    assert enrich_record(record_for_item(a, CPC), c).status == "ok"  # retried next run


def test_scanned_pdf_goes_as_document_block(tmp_path):
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(612, 792)
    w.write(buf)
    llm = FakeLLM({"summarize": [summary(entities=[], units=None, address=None)]})
    e = enrich_record(record_for_item(pd_item(url="https://x/scan.pdf"), CPC),
                      ctx(tmp_path, llm, routes=[("scan.pdf", buf.getvalue())]))
    assert e.source_quality == "scanned"
    assert llm.calls[0]["content"][0]["type"] == "document"


def test_failed_fetch_is_partial(tmp_path):
    from digest.http import FetchError
    llm = FakeLLM({"summarize": [summary()]})
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, routes=[("pd.html", FetchError("u", 500))]))
    assert e.source_quality == "partial" and e.status == "ok"


def test_overrides_apply_after_cache_and_rescue_failures(tmp_path):
    llm = FakeLLM({"summarize": [LLMRefused("refusal")]})
    ov = {"cpc:2026-10-15#1133-w-kinzie-st": {"summary": "Hand-written summary.", "categories": ["social-housing"]}}
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, overrides=ov))
    assert e.status == "ok" and e.overridden and e.summary == "Hand-written summary."
    assert [(c.id, c.via) for c in e.categories] == [("social-housing", "override")]


def test_keywords_also_match_summary(tmp_path):
    llm = FakeLLM({"summarize": [summary(summary="Approves a Section 18 disposition.", categories=[])]})
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm))
    assert ("cha", "keyword") in {(c.id, c.via) for c in e.categories}


def test_notice_keyword_and_body_rules(tmp_path):
    n = Notice(id="watch:doh-home:abc", source_id="watch", body="Chicago Department of Housing", kind="page_change",
               title="Department of Housing home page updated", url="https://www.chicago.gov/content/city/en/depts/doh.html",
               detail="New Class 7 incentive guidance posted")
    llm = FakeLLM({"summarize": [summary(categories=[], entities=[], units=None, affordable_units=None, address=None)]})
    e = enrich_record(record_for_notice(n), ctx(tmp_path, llm, routes=[]))
    assert {(c.id, c.via) for c in e.categories} == {("housing-agenda", "body"), ("property-taxes", "keyword")}
    assert e.source_quality == "none"
    assert "Class 7 incentive guidance" in llm.calls[0]["content"][-1]["text"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.enrich'`

- [ ] **Step 3: Implement**

`ingest/digest/enrich.py`:

```python
"""One shared enrichment per item/notice: relevance screen, summary, grounding checks, categories, geography."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel

from .cache import JsonCache
from .categories import Categories, merge_hits
from .http import FetchError, Fetcher
from .llm import LLMRefused
from .models import CategoryHit, Digest, Enrichment, Figures, Geography, Item, Meeting, Notice
from .pdftext import has_text_layer, pdf_text
from .sources.base import html_to_text

PROMPT_VERSION = "1"  # bump to re-enrich everything after a prompt change
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
    late_rules = ctx.categories.rule_hits(None, [s.summary, *entities])  # keywords also match summary and entities
    model_hits = [CategoryHit(id=c, via="model") for c in s.categories if c in ctx.categories.by_id]
    return Enrichment(
        status="ok", housing_relevant=s.housing_relevant, relevance_reason=s.relevance_reason, summary=s.summary,
        categories=merge_hits(rules, late_rules, model_hits),
        geography=resolve_geography(rec.hint, _grounded_address(s.address, corpus), ctx.geo),
        entities=entities,
        figures=Figures(units=_grounded_number(s.units, numbers),
                        affordable_units=_grounded_number(s.affordable_units, numbers)),
        source_quality=quality, model=ctx.models["summarize"], content_hash=h,
    )


def enrich_record(rec: Record, ctx: EnrichContext) -> Enrichment:
    h = content_hash(rec.title, rec.urls + [rec.extra_text], ctx.categories.fingerprint())
    cached = ctx.cache.get(rec.id)
    if cached and cached.get("content_hash") == h:
        e = Enrichment.model_validate(cached)
    else:
        try:
            e = _fresh(rec, ctx, h)
        except (LLMRefused, anthropic.APIError) as ex:
            e = Enrichment(status="failed", relevance_reason=f"enrichment failed: {ex}"[:300],
                           categories=ctx.categories.rule_hits(rec.body, [rec.title, rec.extra_text]),
                           geography=rec.hint or Geography(), model=ctx.models["summarize"], content_hash=h)
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
```

Notes for the implementer:
- Grounding is deliberately strict. An entity or number that does not appear in the title, notice text, or retrieved document text is dropped. For a scanned PDF this drops everything the model read from the image, which is the intended trade (no unverifiable figures in the email).
- `Summary` fields have no defaults on purpose. Structured outputs then requires the model to emit every field.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_enrich.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/enrich.py ingest/tests/test_enrich.py
git commit -m "Add enrichment: screen, summary, grounding, categories, geography, overrides"
```

---

### Task 11: State merge and change events

**Files:**
- Create: `ingest/digest/state.py`, `ingest/tests/test_state.py`

**Interfaces:**
- Consumes: `Digest`, `Meeting`, `Item`, `Notice`, `SourceStatus`, `ChangeEvent`, `SourceResult` (Task 1).
- Produces: `merge(prev: Digest | None, results: dict[str, SourceResult | Exception], now: dt.datetime) -> Digest`; `count_items(digest) -> None` (sets `item_count_total` / `item_count_relevant` on each meeting; an item counts as relevant when it has no enrichment yet, failed enrichment, or `housing_relevant`). Constants `EVENT_DAYS = 14`, `MEETING_PRUNE_DAYS = 45`, `NOTICE_PRUNE_DAYS = 90`.
- Rules:
  - A source absent from `prev.sources` is new: its records are added with no events (baseline).
  - A source whose result is an Exception keeps all its previous records, gets `status="error"`, keeps its old `last_success_at`, and emits nothing.
  - A refreshed meeting that comes back with zero items keeps its previous items. When it comes back with items, they replace the previous set (agenda order).
  - Meetings and notices that a source stops returning are kept until pruned.

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_state.py`:

```python
import datetime as dt

from digest.http import FetchError
from digest.models import CHI, ChangeEvent, Digest, Enrichment, Item, Meeting, Notice, Outcome, SourceResult
from digest.state import count_items, merge

T0 = dt.datetime(2026, 10, 5, 6, 20, tzinfo=CHI)
T1 = T0 + dt.timedelta(hours=6)
OCT15 = dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI)


def mtg(mid="cpc:2026-10-15", start=OCT15, **kw):
    return Meeting(id=mid, source_id=kw.pop("source_id", "cpc"), body="Chicago Plan Commission", body_id="cpc",
                   start=start, **kw)


def itm(iid, mid="cpc:2026-10-15", **kw):
    return Item(id=iid, meeting_id=mid, source_ref=iid.split("#")[-1], title=kw.pop("title", f"Item {iid}"), **kw)


def res(meetings=(), items=(), notices=(), warnings=()):
    return SourceResult(meetings=list(meetings), items=list(items), notices=list(notices), warnings=list(warnings))


def baseline():
    return merge(None, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")])}, T0)


def types(d):
    return [(e.type, e.ref_id) for e in d.events]


def test_first_run_of_a_source_is_quiet():
    d = baseline()
    assert d.events == []
    assert d.meetings[0].first_seen_at == T0 and d.items[0].first_seen_at == T0
    assert [(s.source_id, s.status, s.last_success_at) for s in d.sources] == [("cpc", "ok", T0)]


def test_new_meeting_emits_meeting_new_only():
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a"), mtg("cpc:2026-11-19", dt.datetime(2026, 11, 19, 10, 0, tzinfo=CHI))],
                                      [itm("cpc:2026-10-15#a"), itm("cpc:2026-11-19#x", "cpc:2026-11-19")])}, T1)
    assert types(d) == [("meeting_new", "cpc:2026-11-19")]


def test_reschedule_same_id_emits_rescheduled():
    moved = mtg(start=dt.datetime(2026, 10, 22, 10, 0, tzinfo=CHI), agenda_url="https://x/a")
    d = merge(baseline(), {"cpc": res([moved], [itm("cpc:2026-10-15#a")])}, T1)
    assert types(d) == [("meeting_rescheduled", "cpc:2026-10-15")]
    assert len(d.meetings) == 1 and d.meetings[0].start.day == 22
    assert d.meetings[0].first_seen_at == T0 and d.meetings[0].last_changed_at == T1


def test_cancel_emits_cancelled():
    d = merge(baseline(), {"cpc": res([mtg(status="cancelled", agenda_url="https://x/a")])}, T1)
    assert types(d) == [("meeting_cancelled", "cpc:2026-10-15")]


def test_agenda_posted_and_item_added():
    prev = merge(None, {"cpc": res([mtg()])}, T0)
    d = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")])}, T1)
    assert types(d) == [("agenda_posted", "cpc:2026-10-15"), ("item_added", "cpc:2026-10-15#a")]


def test_outcome_recorded():
    done = itm("cpc:2026-10-15#a", outcome=Outcome(action="Approved", date=dt.date(2026, 10, 15)))
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a")], [done])}, T1)
    assert types(d) == [("outcome_recorded", "cpc:2026-10-15#a")]


def test_failed_source_carries_forward():
    prev = merge(None, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")]),
                        "clihtf": res([mtg("clihtf:1", source_id="clihtf")])}, T0)
    d = merge(prev, {"cpc": FetchError("https://www.chicago.gov/x", 503), "clihtf": res([mtg("clihtf:1", source_id="clihtf")])}, T1)
    assert [m.id for m in d.meetings if m.source_id == "cpc"] == ["cpc:2026-10-15"]
    assert [i.id for i in d.items] == ["cpc:2026-10-15#a"]
    status = {s.source_id: s for s in d.sources}
    assert status["cpc"].status == "error" and status["cpc"].last_success_at == T0 and "503" in status["cpc"].message
    assert status["clihtf"].status == "ok" and status["clihtf"].last_success_at == T1
    assert d.events == []


def test_warnings_make_a_source_stale():
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a")], warnings=["agenda has no text layer"])}, T1)
    s = d.sources[0]
    assert s.status == "stale" and s.message == "agenda has no text layer"


def test_refreshed_meeting_without_items_keeps_previous_items():
    d = merge(baseline(), {"cpc": res([mtg(agenda_url="https://x/a")], [])}, T1)
    assert [i.id for i in d.items] == ["cpc:2026-10-15#a"]


def test_items_missing_from_refreshed_meeting_are_dropped():
    prev = merge(None, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a"), itm("cpc:2026-10-15#b")])}, T0)
    d = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#b")])}, T1)
    assert [i.id for i in d.items] == ["cpc:2026-10-15#b"]


def test_last_changed_only_moves_on_content_change():
    prev = baseline()
    prev.items[0].enrichment = Enrichment(status="ok", summary="s")
    same = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a")])}, T1)
    assert same.items[0].last_changed_at == T0
    assert same.items[0].enrichment.summary == "s"  # carried over when the new item has none
    edited = merge(prev, {"cpc": res([mtg(agenda_url="https://x/a")], [itm("cpc:2026-10-15#a", title="Amended")])}, T1)
    assert edited.items[0].last_changed_at == T1 and edited.items[0].first_seen_at == T0


def test_notice_new_and_pruning():
    old_notice = Notice(id="ihda:notice:old", source_id="ihda", body="IHDA", kind="comment_period", title="Old", url="https://x/o")
    open_notice = Notice(id="ihda:notice:open", source_id="ihda", body="IHDA", kind="hearing", title="Open", url="https://x/p",
                         deadline=T0 + dt.timedelta(days=200))
    long_ago = T0 - dt.timedelta(days=100)
    prev = merge(None, {"ihda": res([mtg("ihda:b:2026-07-01", dt.datetime(2026, 7, 1, 10, 0, tzinfo=CHI), source_id="ihda")],
                                    [itm("ihda:b:2026-07-01#1", "ihda:b:2026-07-01")], [old_notice, open_notice])}, long_ago)
    prev.events = [ChangeEvent(type="meeting_new", ref_id="x", at=T0 - dt.timedelta(days=15)),
                   ChangeEvent(type="meeting_new", ref_id="y", at=T0 - dt.timedelta(days=13))]
    new_notice = Notice(id="ihda:notice:new", source_id="ihda", body="IHDA", kind="hearing", title="New", url="https://x/n")
    d = merge(prev, {"ihda": res(notices=[new_notice])}, T0)
    assert d.meetings == [] and d.items == []                       # meeting held more than 45 days ago
    assert sorted(n.id for n in d.notices) == ["ihda:notice:new", "ihda:notice:open"]
    assert types(d) == [("meeting_new", "y"), ("notice_new", "ihda:notice:new")]


def test_count_items():
    d = baseline()
    d.items.append(itm("cpc:2026-10-15#b", enrichment=Enrichment(status="screened_out")))
    d.items.append(itm("cpc:2026-10-15#c", enrichment=Enrichment(status="ok", housing_relevant=True)))
    count_items(d)
    assert (d.meetings[0].item_count_total, d.meetings[0].item_count_relevant) == (3, 2)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.state'`

- [ ] **Step 3: Implement**

`ingest/digest/state.py`:

```python
"""Merge this run's adapter results into the previous items.json and record what changed."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .models import ChangeEvent, Digest, Item, Meeting, Notice, SourceResult, SourceStatus

EVENT_DAYS = 14
MEETING_PRUNE_DAYS = 45
NOTICE_PRUNE_DAYS = 90
VOLATILE = {"first_seen_at", "last_changed_at", "enrichment", "item_count_total", "item_count_relevant"}


def _content(obj) -> dict:
    return obj.model_dump(mode="json", exclude=VOLATILE)


def _stamp(new, old, now: dt.datetime) -> None:
    if old is None:
        new.first_seen_at = new.last_changed_at = now
    else:
        new.first_seen_at = old.first_seen_at or now
        new.last_changed_at = now if _content(new) != _content(old) else (old.last_changed_at or now)


def merge(prev: Digest | None, results: dict[str, SourceResult | Exception], now: dt.datetime) -> Digest:
    prev = prev or Digest(generated_at=now)
    old_status = {s.source_id: s for s in prev.sources}
    pm = {m.id: m for m in prev.meetings}
    pi = {i.id: i for i in prev.items}
    pn = {n.id: n for n in prev.notices}
    meetings: dict[str, Meeting] = dict(pm)
    items: dict[str, Item] = dict(pi)
    notices: dict[str, Notice] = dict(pn)
    statuses = dict(old_status)
    events = [e for e in prev.events if e.at >= now - dt.timedelta(days=EVENT_DAYS)]

    for sid, res in results.items():
        before = old_status.get(sid)
        if isinstance(res, Exception):
            statuses[sid] = SourceStatus(source_id=sid, status="error",
                                         last_success_at=before.last_success_at if before else None,
                                         message=f"{type(res).__name__}: {res}"[:500])
            continue
        statuses[sid] = SourceStatus(source_id=sid, status="stale" if res.warnings else "ok", last_success_at=now,
                                     message="; ".join(res.warnings)[:1000])
        quiet = before is None

        def emit(kind: str, ref: str) -> None:
            if not quiet:
                events.append(ChangeEvent(type=kind, ref_id=ref, at=now))

        fresh_by_meeting: dict[str, list[Item]] = defaultdict(list)
        for it in res.items:
            fresh_by_meeting[it.meeting_id].append(it)

        for m in res.meetings:
            old = pm.get(m.id)
            if old is None:
                emit("meeting_new", m.id)
            else:
                if m.status == "cancelled" and old.status != "cancelled":
                    emit("meeting_cancelled", m.id)
                elif m.start != old.start and m.status != "cancelled":
                    emit("meeting_rescheduled", m.id)
                if m.agenda_url and not old.agenda_url:
                    emit("agenda_posted", m.id)
            _stamp(m, old, now)
            meetings[m.id] = m

            fresh = fresh_by_meeting.get(m.id)
            if not fresh:
                continue  # agenda unreadable this run, or extraction skipped: keep what we had
            for iid in [k for k, v in items.items() if v.meeting_id == m.id]:
                del items[iid]
            for it in fresh:
                o = pi.get(it.id)
                if o is None:
                    if old is not None:
                        emit("item_added", it.id)  # a brand-new meeting's items are covered by meeting_new
                else:
                    if it.outcome is not None and it.outcome != o.outcome:
                        emit("outcome_recorded", it.id)
                    if it.enrichment is None:
                        it.enrichment = o.enrichment
                _stamp(it, o, now)
                items[it.id] = it

        for n in res.notices:
            o = pn.get(n.id)
            if o is None:
                emit("notice_new", n.id)
            elif n.enrichment is None:
                n.enrichment = o.enrichment
            _stamp(n, o, now)
            notices[n.id] = n

    meeting_cutoff = now - dt.timedelta(days=MEETING_PRUNE_DAYS)
    notice_cutoff = now - dt.timedelta(days=NOTICE_PRUNE_DAYS)
    kept_meetings = sorted((m for m in meetings.values() if m.start >= meeting_cutoff), key=lambda m: (m.start, m.id))
    order = {m.id: i for i, m in enumerate(kept_meetings)}
    kept_items = sorted((i for i in items.values() if i.meeting_id in order), key=lambda i: order[i.meeting_id])
    kept_notices = sorted(
        (n for n in notices.values()
         if not (n.first_seen_at and n.first_seen_at < notice_cutoff and not (n.deadline and n.deadline > now))),
        key=lambda n: (n.first_seen_at or now, n.id),
    )
    return Digest(generated_at=now, sources=sorted(statuses.values(), key=lambda s: s.source_id),
                  meetings=kept_meetings, items=kept_items, notices=kept_notices, events=events)


def count_items(digest: Digest) -> None:
    for m in digest.meetings:
        its = [i for i in digest.items if i.meeting_id == m.id]
        m.item_count_total = len(its)
        m.item_count_relevant = sum(
            1 for i in its if i.enrichment is None or i.enrichment.status == "failed" or i.enrichment.housing_relevant)
```

(`sorted` is stable, so items keep agenda order within a meeting: a refreshed meeting's items are deleted and re-inserted in the order the adapter returned them.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_state.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/state.py ingest/tests/test_state.py
git commit -m "Add state merge with change events, carry-forward and pruning"
```

---

### Task 12: Source registry, configs, and the run orchestrator

**Files:**
- Create: `ingest/digest/registry.py`, `ingest/digest/run.py`, `config/sources.yaml`, `config/ai.yaml`, `config/overrides.yaml`, `ingest/tests/test_run.py`

**Interfaces:**
- Consumes: every adapter (Tasks 2–7); `extract_items` (Task 9); `LLM`, `LLMRefused` (Task 9); `EnrichContext`, `enrich_digest` (Task 10); `merge`, `count_items` (Task 11); `Categories`, `Geo` (Task 8); `load_digest`, `dump_digest` (Task 1).
- Produces:
  - `digest.registry`: `build_adapters(config_dir: Path, data_dir: Path, only: set[str] | None = None) -> list[BaseAdapter]`.
  - `digest.run`: `load_ai(config_dir) -> dict`; `run(root: Path, *, fetcher, llm=None, now=None, only=None) -> Digest`; `main(argv=None)`. CLI: `python -m digest.run [--no-ai] [--sources id,id] [--root PATH]`. Exit code 0 even when sources fail (failures are recorded in `sources[]`); nonzero only on a crash.
  - Files written by a run: `data/items.json`, `data/cache/extract.json`, `data/cache/enrich.json`, `data/cache/geocode.json`, `data/snapshots/*.json`, `data/usage.jsonl`.

- [ ] **Step 1: Write the configs**

`config/sources.yaml`:

```yaml
# One entry per source. `adapter` picks the class; the other keys are that adapter's config.
# min_meetings: fewer meetings than this in the window is treated as a broken source.
sources:
  - id: elms-zoning
    adapter: elms
    body: "Committee on Zoning, Landmarks and Building Standards"
    display_body: City Council Committee on Zoning
    min_meetings: 1
  - id: elms-housing
    adapter: elms
    body: "Committee on Housing and Real Estate"
    display_body: City Council Committee on Housing and Real Estate
    min_meetings: 1
  - id: elms-finance
    adapter: elms
    body: "Committee on Finance"
    display_body: City Council Committee on Finance
    min_meetings: 1
  - id: cha
    adapter: legistar
    client: thecha
    bodies:
      138: CHA Board of Commissioners
      188: CHA Finance & Audit Committee
      189: CHA Real Estate Operations Development Committee
      190: CHA Tenant Services Committee
    min_meetings: 1
  - id: cpc
    adapter: dpd
    body: Chicago Plan Commission
    default_time: "10:00"
    listing_url: "https://www.chicago.gov/content/city/en/depts/dcd/chicago-plan-commission/meetings--agendas---video-archives.html"
    min_meetings: 1
  - id: cdc
    adapter: dpd
    body: Community Development Commission
    default_time: "13:30"
    listing_url: "https://www.chicago.gov/content/city/en/depts/dcd/community-development-commission/meetings--agendas-and-video-archives.html"
    min_meetings: 1
  - id: ihda
    adapter: ihda
    url: "https://www.ihda.org/about-ihda/public-meetings-and-notices/"
    window_days: [-60, 90]
  - id: clihtf
    adapter: clihtf
  - id: watch
    adapter: watcher
    watch_file: watch.yaml
```

`config/ai.yaml`:

```yaml
# Model per purpose. The screen runs only on items from screen_bodies (City Council committees,
# whose agendas are mostly not about housing); everything else goes straight to the summary.
models:
  screen: claude-haiku-5-5
  summarize: claude-sonnet-5-5
  extract: claude-sonnet-5-5
# USD per million tokens, used only by `python -m digest.costs`.
prices:
  claude-haiku-5-5: {input: 0.10, output: 0.50}
  claude-sonnet-5-5: {input: 2.00, output: 10.00}
screen_bodies:
  - City Council Committee on Zoning
  - City Council Committee on Housing and Real Estate
  - City Council Committee on Finance
char_cap: 12000
max_attachments: 2
```

`config/overrides.yaml`:

```yaml
# Hand corrections, keyed by item or notice id (copy the id from items.json).
# Allowed fields: housing_relevant, summary, relevance_reason, categories (list of ids), geography {address, ward, community_area}.
# Example:
#   "elms:A44FB4F3-45B0-F111-AAAC-001DD80B58E5#O2026-0025391":
#     housing_relevant: true
#     categories: [ahpaa]
overrides: {}
```

- [ ] **Step 2: Write the failing end-to-end tests**

`ingest/tests/test_run.py`:

```python
import datetime as dt
import json
import shutil
from pathlib import Path

import jsonschema

from digest.enrich import Summary
from digest.extract import AgendaExtraction, ExtractedItem
from digest.http import FetchError
from digest.models import CHI, load_digest
from digest.registry import build_adapters
from digest.run import run
from digest.schema import schema_text
from fakes import FakeFetcher, FakeLLM

REPO = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 10, 5, 6, 20, tzinfo=CHI)
SOURCES = """
sources:
  - {id: cpc, adapter: dpd, body: Chicago Plan Commission, default_time: "10:00",
     listing_url: "https://www.chicago.gov/content/city/en/depts/dcd/chicago-plan-commission/meetings--agendas---video-archives.html"}
  - {id: clihtf, adapter: clihtf}
"""
SQUARE = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"ward": "1", "community": "LOOP"},
          "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}]}
MISS = json.dumps({"result": {"addressMatches": []}}).encode()


def make_root(tmp_path):
    cfg = tmp_path / "config"
    (cfg / "geo").mkdir(parents=True)
    (cfg / "sources.yaml").write_text(SOURCES, encoding="utf-8")
    for name in ("categories.yaml", "ai.yaml", "overrides.yaml"):
        shutil.copy(REPO / "config" / name, cfg / name)
    for name in ("wards.geojson", "community_areas.geojson"):
        (cfg / "geo" / name).write_text(json.dumps(SQUARE), encoding="utf-8")
    return tmp_path


def routes(cpc_down=False):
    listing = FetchError("https://www.chicago.gov/listing", 503) if cpc_down else "dpd/cpc_listing.html"
    return [
        ("geocoding.geo.census.gov", MISS),
        ("nominatim.openstreetmap.org", b"[]"),
        ("October_2026_Plan_Commission_Hearing", "dpd/cpc_2026_10.html"),  # before the listing, as in Task 4
        ("meetings--agendas---video-archives", listing),
        ("tribe/events/v1/events", "clihtf/events.json"),
        ("", b"<html><body><p>Attachment text.</p></body></html>"),  # any attachment
    ]


def fake_llm():
    return FakeLLM({
        "extract": lambda content: AgendaExtraction(items=[ExtractedItem(number="1", title="Approve allocations")]),
        "summarize": lambda content: Summary(housing_relevant=True, relevance_reason="r", summary="s", categories=[],
                                             address=None, entities=[], units=None, affordable_units=None),
        "screen": [],
    })


def test_build_adapters_reads_real_config():
    ids = [a.source_id for a in build_adapters(REPO / "config", REPO / "data")]
    assert ids == ["elms-zoning", "elms-housing", "elms-finance", "cha", "cpc", "cdc", "ihda", "clihtf", "watch"]
    assert [a.source_id for a in build_adapters(REPO / "config", REPO / "data", {"cpc"})] == ["cpc"]


def test_run_baseline_writes_valid_items_json(tmp_path):
    root = make_root(tmp_path)
    d = run(root, fetcher=FakeFetcher(routes()), llm=fake_llm(), now=NOW)
    assert {s.source_id: s.status for s in d.sources} == {"cpc": "ok", "clihtf": "ok"}
    assert d.events == []
    assert "clihtf:319068#1" in {i.id for i in d.items}
    assert all(i.enrichment is not None for i in d.items)
    cpc = next(m for m in d.meetings if m.id == "cpc:2026-10-15")
    assert cpc.item_count_total == 3 and cpc.item_count_relevant == 3
    data = json.loads((root / "data" / "items.json").read_text(encoding="utf-8"))
    jsonschema.validate(data, json.loads(schema_text()))
    assert (root / "data" / "cache" / "enrich.json").exists() and (root / "data" / "cache" / "extract.json").exists()


def test_run_isolates_failed_source(tmp_path):
    root = make_root(tmp_path)
    run(root, fetcher=FakeFetcher(routes()), llm=fake_llm(), now=NOW)
    d = run(root, fetcher=FakeFetcher(routes(cpc_down=True)), llm=fake_llm(), now=NOW + dt.timedelta(hours=6))
    status = {s.source_id: s for s in d.sources}
    assert status["cpc"].status == "error" and status["cpc"].last_success_at == NOW
    assert status["clihtf"].status == "ok"
    assert any(m.id == "cpc:2026-10-15" for m in d.meetings)
    assert d.events == []


def test_run_without_ai_keeps_previous_enrichment(tmp_path):
    root = make_root(tmp_path)
    run(root, fetcher=FakeFetcher(routes()), llm=fake_llm(), now=NOW)
    d = run(root, fetcher=FakeFetcher(routes()), llm=None, now=NOW + dt.timedelta(hours=6))
    assert "clihtf:319068#1" in {i.id for i in d.items}           # extracted items survive a --no-ai run
    assert all(i.enrichment is not None for i in d.items)
    assert load_digest(root / "data" / "items.json").generated_at == NOW + dt.timedelta(hours=6)
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.registry'`

- [ ] **Step 4: Implement the registry and the orchestrator**

`ingest/digest/registry.py`:

```python
from pathlib import Path

import yaml

from .sources.base import BaseAdapter
from .sources.clihtf import ClihtfAdapter
from .sources.dpd import DpdAdapter
from .sources.elms import ElmsAdapter
from .sources.ihda import IhdaAdapter
from .sources.legistar import LegistarAdapter
from .sources.watcher import WatcherAdapter

ADAPTERS = {"elms": ElmsAdapter, "legistar": LegistarAdapter, "dpd": DpdAdapter, "ihda": IhdaAdapter,
            "clihtf": ClihtfAdapter}


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_adapters(config_dir: Path, data_dir: Path, only: set[str] | None = None) -> list[BaseAdapter]:
    out: list[BaseAdapter] = []
    for cfg in _yaml(config_dir / "sources.yaml")["sources"]:
        if only and cfg["id"] not in only:
            continue
        if cfg["adapter"] == "watcher":
            pages = _yaml(config_dir / cfg["watch_file"])["pages"]
            out.append(WatcherAdapter(cfg, pages, data_dir / "snapshots"))
        else:
            out.append(ADAPTERS[cfg["adapter"]](cfg))
    return out
```

`ingest/digest/run.py`:

```python
"""Orchestrator: fetch every source, extract agendas, merge, enrich, write data/items.json."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import anthropic
import yaml

from .cache import JsonCache
from .categories import Categories
from .enrich import EnrichContext, enrich_digest
from .extract import extract_items
from .geo import Geo
from .http import FetchError, Fetcher, HttpFetcher
from .llm import LLM, LLMRefused
from .models import CHI, Digest, SourceResult, dump_digest, load_digest
from .registry import build_adapters
from .sources.base import ImplausibleResult
from .state import count_items, merge

log = logging.getLogger("digest")
ROOT = Path(__file__).resolve().parents[2]


def load_ai(config_dir: Path) -> dict:
    return yaml.safe_load((config_dir / "ai.yaml").read_text(encoding="utf-8"))


def _extract(res: SourceResult, fetcher: Fetcher, llm, cache: JsonCache) -> None:
    have = {i.meeting_id for i in res.items}
    for m in res.meetings:
        if not m.needs_ai_extraction or m.status == "cancelled" or m.id in have:
            continue
        try:
            res.items += extract_items(m, fetcher, llm, cache)
        except (LLMRefused, anthropic.APIError, FetchError) as e:
            res.warnings.append(f"{m.id}: agenda extraction failed: {e}")


def run(root: Path, *, fetcher: Fetcher, llm=None, now: dt.datetime | None = None,
        only: set[str] | None = None) -> Digest:
    now = now or dt.datetime.now(CHI)
    config, data = root / "config", root / "data"
    prev = load_digest(data / "items.json")
    extract_cache = JsonCache(data / "cache" / "extract.json")

    results: dict[str, SourceResult | Exception] = {}
    for adapter in build_adapters(config, data, only):
        try:
            res = adapter.fetch(fetcher, now)
            if len(res.meetings) < adapter.min_meetings:
                raise ImplausibleResult(f"{len(res.meetings)} meetings in window, expected at least {adapter.min_meetings}")
        except Exception as e:  # one broken source must never stop the others
            log.exception("source %s failed", adapter.source_id)
            results[adapter.source_id] = e
            continue
        if llm is not None:
            _extract(res, fetcher, llm, extract_cache)
        results[adapter.source_id] = res
        log.info("%s: %d meetings, %d items, %d notices", adapter.source_id, len(res.meetings), len(res.items), len(res.notices))

    digest = merge(prev, results, now)

    if llm is not None:
        ai = load_ai(config)
        overrides = yaml.safe_load((config / "overrides.yaml").read_text(encoding="utf-8")).get("overrides") or {}
        geocode_cache = JsonCache(data / "cache" / "geocode.json")
        ctx = EnrichContext(
            llm=llm, fetcher=fetcher, categories=Categories(config / "categories.yaml"),
            geo=Geo(config / "geo" / "wards.geojson", config / "geo" / "community_areas.geojson", fetcher, geocode_cache),
            cache=JsonCache(data / "cache" / "enrich.json"), overrides=overrides,
            screen_bodies=set(ai.get("screen_bodies") or []), models=ai["models"],
            char_cap=ai.get("char_cap", 12000), max_attachments=ai.get("max_attachments", 2),
        )
        enrich_digest(digest, ctx)
        ctx.cache.save()
        geocode_cache.save()

    count_items(digest)
    extract_cache.save()
    dump_digest(digest, data / "items.json")
    return digest


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m digest.run")
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--no-ai", action="store_true", help="skip agenda extraction and enrichment")
    p.add_argument("--sources", help="comma-separated source ids (default: all)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    llm = None
    if not args.no_ai:
        llm = LLM(anthropic.Anthropic(), load_ai(args.root / "config")["models"], args.root / "data" / "usage.jsonl")
    only = set(args.sources.split(",")) if args.sources else None
    digest = run(args.root, fetcher=HttpFetcher(), llm=llm, only=only)
    for s in digest.sources:
        print(f"{s.status:6} {s.source_id:14} {s.message}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_run.py -v`
Expected: 4 passed. If `item_count_total == 3` fails, print the CPC items: the count must equal the number of rows Task 4's `test_cpc_item_without_address_uses_position` expects (3).

- [ ] **Step 6: Run the full suite**

Run: `cd ingest && python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Check the eLMS body names against the live API (read-only)**

```bash
for b in "Committee on Zoning, Landmarks and Building Standards" "Committee on Housing and Real Estate" "Committee on Finance"; do
  python - "$b" <<'EOF'
import sys
from digest.http import HttpFetcher
body = sys.argv[1]
data = HttpFetcher().get_json("https://api.chicityclerkelms.chicago.gov/meeting", {"filter": f"body eq '{body}'", "top": 1})
rows = data.get("data") or []
print("OK " if rows else "BAD", body, rows[0]["date"] if rows else "")
EOF
done
```

Expected: three `OK` lines. For a `BAD` line, find the exact body name in an eLMS meeting record (`curl -s "https://api.chicityclerkelms.chicago.gov/meeting?top=50" | python -m json.tool | grep '"body"' | sort -u`), correct `config/sources.yaml`, and report the change.

- [ ] **Step 8: Live smoke run without AI (read-only on external sites, writes `data/`)**

Run: `cd ingest && python -m digest.run --no-ai`
Expected: one status line per source. Every source `ok`, or `stale` with a message you can explain. For any `error`, read the logged traceback and fix the adapter (the fixture tests show what the page looked like on 2026-10-13). Then inspect `data/items.json`: Council meetings have items with record numbers, CHA meetings have items, IHDA has notices. Commit `data/` only after this looks right.

- [ ] **Step 9: Commit**

```bash
git add config/sources.yaml config/ai.yaml config/overrides.yaml ingest/digest/registry.py ingest/digest/run.py ingest/tests/test_run.py data
git commit -m "Add source registry, configs and run orchestrator"
```

---

### Task 13: Static site and calendar feeds

**Files:**
- Create: `ingest/digest/site.py`, `ingest/digest/templates/index.html.j2`, `ingest/tests/test_site.py`

**Interfaces:**
- Consumes: `Digest`, `Meeting`, `Item`, `Notice` (Task 1); `Categories` (Task 8); `slugify` (Task 2).
- Produces: `build_site(digest, out_dir: Path, items_json: Path, categories: Categories, now) -> None`; `ics_calendar(meetings, name, now) -> str`; `ics_escape(text) -> str`; `fold(line) -> str`. CLI: `python -m digest.site [--root PATH]` writes `site/index.html`, `site/items.json`, `site/ics/<body-slug>.ics`.
- Display rules:
  - An item is shown when it has no enrichment yet, enrichment failed, or `housing_relevant` is true. Hidden items are counted as "+N items not about housing".
  - Summaries are labeled "AI summary".
  - Upcoming means `start` on or after today (Chicago). Open notices have no deadline or a future one. Recent outcomes are shown items whose `outcome.date` falls in the last 14 days.

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_site.py`:

```python
import datetime as dt
import json
from pathlib import Path

from digest.categories import Categories
from digest.models import (CHI, CategoryHit, Digest, Enrichment, Geography, Item, Meeting, Notice, Outcome,
                           dump_digest)
from digest.site import build_site, fold, ics_calendar, ics_escape

CATS = Categories(Path(__file__).resolve().parents[2] / "config" / "categories.yaml")
NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)


def sample():
    up = Meeting(id="cpc:2026-10-15", source_id="cpc", body="Chicago Plan Commission", body_id="cpc",
                 start=dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI), location="City Hall, Room 201A",
                 agenda_url="https://x/agenda.pdf")
    gone = Meeting(id="cdc:2026-11-10", source_id="cdc", body="Community Development Commission", body_id="cdc",
                   start=dt.datetime(2026, 11, 10, 0, 0, tzinfo=CHI), time_known=False, status="cancelled")
    past = Meeting(id="elms:Z1", source_id="elms-zoning", body="City Council Committee on Zoning", body_id="z",
                   start=dt.datetime(2026, 9, 30, 10, 0, tzinfo=CHI))
    ok = Enrichment(status="ok", housing_relevant=True, summary="Adds 120 homes near the Kinzie Green Line stop.",
                    categories=[CategoryHit(id="ahpaa", via="body")], geography=Geography(ward=27))
    items = [
        Item(id="cpc:2026-10-15#kinzie", meeting_id=up.id, source_ref="1133 W. Kinzie St.", title="Kinzie Planned Development",
             enrichment=ok),
        Item(id="cpc:2026-10-15#sign", meeting_id=up.id, source_ref="x", title="Billboard on Ogden Ave",
             enrichment=Enrichment(status="screened_out")),
        Item(id="elms:Z1#O2026-1", meeting_id=past.id, source_ref="O2026-1", title="Rezoning at 4400 N. Broadway",
             outcome=Outcome(action="Recommended to Pass", date=dt.date(2026, 9, 30)), enrichment=ok),
    ]
    notice = Notice(id="ihda:notice:abc", source_id="ihda", body="IHDA", kind="hearing", title="TEFRA hearing, Pheasant Ridge",
                    url="https://x/tefra.pdf", deadline=dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI))
    for m, n in ((up, 2), (gone, 0), (past, 1)):
        m.item_count_total = n
    return Digest(generated_at=NOW, meetings=[past, up, gone], items=items, notices=[notice])


def test_index_shows_relevant_hides_rest(tmp_path):
    src = tmp_path / "items.json"
    dump_digest(sample(), src)
    build_site(sample(), tmp_path / "site", src, CATS, NOW)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Kinzie Planned Development" in html and "Billboard on Ogden Ave" not in html
    assert "+1 item not about housing" in html
    assert "AI summary" in html and "Adds 120 homes" in html
    assert "Zoning &amp; housing supply" in html                  # category label, escaped
    assert "TEFRA hearing, Pheasant Ridge" in html
    assert "Recommended to Pass" in html                           # recent outcome
    assert "Cancelled" in html
    assert 'data-categories="ahpaa"' in html and 'data-ward="27"' in html
    assert json.loads((tmp_path / "site" / "items.json").read_text(encoding="utf-8"))["generated_at"]


def test_ics_feeds(tmp_path):
    src = tmp_path / "items.json"
    dump_digest(sample(), src)
    build_site(sample(), tmp_path / "site", src, CATS, NOW)
    cpc = (tmp_path / "site" / "ics" / "chicago-plan-commission.ics").read_bytes().decode("utf-8")
    assert cpc.startswith("BEGIN:VCALENDAR\r\n") and cpc.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;TZID=America/Chicago:20261015T100000\r\n" in cpc
    assert "UID:cpc-2026-10-15@housingdigest.danielkayhertz.com" in cpc
    assert "LOCATION:City Hall\\, Room 201A" in cpc
    cdc = (tmp_path / "site" / "ics" / "community-development-commission.ics").read_text(encoding="utf-8", newline="")
    assert "DTSTART;VALUE=DATE:20261110" in cdc and "STATUS:CANCELLED" in cdc
    assert all(len(line.encode("utf-8")) <= 75 for line in cpc.split("\r\n"))


def test_escape_and_fold():
    assert ics_escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    folded = fold("DESCRIPTION:" + "é" * 80)
    lines = folded.split("\r\n")
    assert all(len(line.encode("utf-8")) <= 75 for line in lines)
    assert all(line.startswith(" ") for line in lines[1:])
    assert "".join(line[1:] if i else line for i, line in enumerate(lines)) == "DESCRIPTION:" + "é" * 80


def test_calendar_has_timezone_block():
    cal = ics_calendar(sample().meetings[1:2], "Chicago Plan Commission", NOW)
    assert "BEGIN:VTIMEZONE\r\nTZID:America/Chicago" in cal
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_site.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.site'`

- [ ] **Step 3: Implement the site builder**

`ingest/digest/site.py`:

```python
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


def shown(item: Item) -> bool:
    e = item.enrichment
    return e is None or e.status == "failed" or e.housing_relevant


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
    url = m.detail_url or m.agenda_url
    if url:
        lines.append(f"URL:{url}")
    desc = [f"Agenda: {m.agenda_url}" if m.agenda_url else "Agenda not posted yet."]
    if m.item_count_total:
        desc.append(f"{m.item_count_relevant} of {m.item_count_total} items are about housing.")
    lines += [f"DESCRIPTION:{ics_escape(chr(10).join(desc))}",
              f"STATUS:{'CANCELLED' if m.status == 'cancelled' else 'CONFIRMED'}", "END:VEVENT"]
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

    upcoming = []
    for m in digest.meetings:
        if m.start.astimezone(CHI).date() < today:
            continue
        its = items_by_meeting.get(m.id, [])
        visible = [i for i in its if shown(i)]
        upcoming.append({"m": m, "items": visible, "hidden": len(its) - len(visible)})
    notices = [n for n in digest.notices
               if (n.deadline is None or n.deadline >= now)
               and (n.enrichment is None or n.enrichment.status == "failed" or n.enrichment.housing_relevant)]
    cutoff = today - dt.timedelta(days=OUTCOME_DAYS)
    outcomes = sorted(
        ({"i": i, "m": meetings_by_id.get(i.meeting_id)} for i in digest.items
         if i.outcome and i.outcome.date >= cutoff and shown(i)),
        key=lambda r: r["i"].outcome.date, reverse=True,
    )
    bodies = sorted({m.body for m in digest.meetings})

    env = Environment(loader=PackageLoader("digest", "templates"), autoescape=select_autoescape(["html", "j2"]))
    env.filters["anchor"] = lambda s: slugify(s, 120)
    html = env.get_template("index.html.j2").render(
        digest=digest, upcoming=upcoming, notices=notices, outcomes=outcomes, bodies=bodies,
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
```

`ingest/digest/templates/index.html.j2`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chicago Housing Meetings</title>
<style>
  :root { --ink: #1d1d1f; --muted: #5f6368; --line: #dadce0; --bg: #fff; --tag: #eef3f8; --warn: #a50e0e; }
  @media (prefers-color-scheme: dark) { :root { --ink: #e8eaed; --muted: #9aa0a6; --line: #3c4043; --bg: #202124; --tag: #2b3440; --warn: #f28b82; } }
  body { margin: 0 auto; max-width: 860px; padding: 16px; font: 16px/1.5 system-ui, sans-serif; color: var(--ink); background: var(--bg); }
  h1 { font-size: 1.6rem; margin-bottom: 0; } h2 { margin-top: 2rem; border-bottom: 1px solid var(--line); }
  .meta, .hidden-count, .src { color: var(--muted); font-size: .9rem; }
  .meeting { margin: 1.25rem 0; } .meeting h3 { margin: 0; font-size: 1.1rem; }
  .cancelled { color: var(--warn); font-weight: 600; }
  ul.items { padding-left: 1.1rem; } ul.items li { margin: .5rem 0; }
  .tag { display: inline-block; background: var(--tag); border-radius: 4px; padding: 0 .4rem; font-size: .8rem; margin-right: .25rem; }
  .ai { font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  .filters { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0; } select { font: inherit; }
  a { color: inherit; }
</style>
</head>
<body>
<h1>Chicago Housing Meetings</h1>
<p class="meta">Updated {{ generated.strftime('%B %d, %Y %I:%M %p') }} Chicago time. Summaries are written by AI from public documents; check the source before relying on them.</p>

<div class="filters">
  <select id="f-cat" aria-label="Category"><option value="">All categories</option>
    {% for c in categories %}<option value="{{ c.id }}">{{ c.label }}</option>{% endfor %}</select>
  <select id="f-body" aria-label="Body"><option value="">All bodies</option>
    {% for b in bodies %}<option value="{{ b }}">{{ b }}</option>{% endfor %}</select>
  <select id="f-ward" aria-label="Ward"><option value="">All wards</option>
    {% for w in range(1, 51) %}<option value="{{ w }}">Ward {{ w }}</option>{% endfor %}</select>
</div>

{% macro item_row(i) -%}
{% set e = i.enrichment %}
<li id="{{ i.id | anchor }}" class="row"
    data-categories="{{ (e.categories | map(attribute='id') | join(' ')) if e else '' }}"
    data-ward="{{ e.geography.ward if e and e.geography.ward else '' }}">
  <strong>{{ i.title }}</strong> <span class="src">{{ i.source_ref }}</span>
  {% if e and e.summary %}<div><span class="ai">AI summary</span> {{ e.summary }}</div>{% endif %}
  <div>{% if e %}{% for c in e.categories %}<span class="tag">{{ cat_labels.get(c.id, c.id) }}</span>{% endfor %}{% endif %}
    {% if e and e.geography.ward %}<span class="tag">Ward {{ e.geography.ward }}</span>{% endif %}
    {% for a in i.attachments[:3] %}<a href="{{ a.url }}">{{ a.label }}</a> {% endfor %}</div>
</li>
{%- endmacro %}

<h2>Upcoming meetings</h2>
{% for u in upcoming %}{% set m = u.m %}
<section class="meeting" id="{{ m.id | anchor }}" data-body="{{ m.body }}">
  <h3>{{ m.body }}</h3>
  <div class="meta">
    {{ m.start.strftime('%A, %B %d') }}{% if m.time_known %}, {{ m.start.strftime('%I:%M %p').lstrip('0') }}{% endif %}
    {% if m.location %} · {{ m.location }}{% endif %}
    {% if m.status == 'cancelled' %} · <span class="cancelled">Cancelled</span>{% endif %}
    {% if m.agenda_url %} · <a href="{{ m.agenda_url }}">Agenda</a>{% endif %}
    · <a href="{{ ics[m.body] }}">Calendar</a>
  </div>
  {% if u['items'] %}<ul class="items">{% for i in u['items'] %}{{ item_row(i) }}{% endfor %}</ul>{% endif %}
  {% if u.hidden %}<div class="hidden-count">+{{ u.hidden }} item{{ 's' if u.hidden != 1 else '' }} not about housing</div>{% endif %}
</section>
{% else %}<p>No upcoming meetings found.</p>{% endfor %}

<h2>Open notices</h2>
<ul class="items">
{% for n in notices %}
  <li class="row" data-body="{{ n.body }}"
      data-categories="{{ (n.enrichment.categories | map(attribute='id') | join(' ')) if n.enrichment else '' }}" data-ward="">
    <strong><a href="{{ n.url }}">{{ n.title }}</a></strong>
    <span class="src">{{ n.body }}{% if n.deadline %} · deadline {{ n.deadline.strftime('%B %d, %Y') }}{% endif %}</span>
    {% if n.enrichment and n.enrichment.summary %}<div><span class="ai">AI summary</span> {{ n.enrichment.summary }}</div>{% endif %}
  </li>
{% else %}<li>No open notices.</li>{% endfor %}
</ul>

<h2>Recent outcomes</h2>
<ul class="items">
{% for r in outcomes %}
  <li class="row" data-body="{{ r.m.body if r.m else '' }}"
      data-categories="{{ (r.i.enrichment.categories | map(attribute='id') | join(' ')) if r.i.enrichment else '' }}"
      data-ward="{{ r.i.enrichment.geography.ward if r.i.enrichment and r.i.enrichment.geography.ward else '' }}">
    <strong>{{ r.i.outcome.action }}</strong> ({{ r.i.outcome.date.strftime('%B %d') }}{% if r.m %}, {{ r.m.body }}{% endif %}): {{ r.i.title }}
  </li>
{% else %}<li>No outcomes in the last two weeks.</li>{% endfor %}
</ul>

<p class="meta">Data: <a href="items.json">items.json</a></p>
<script>
  const sel = id => document.getElementById(id);
  function apply() {
    const cat = sel('f-cat').value, body = sel('f-body').value, ward = sel('f-ward').value;
    document.querySelectorAll('.row').forEach(row => {
      const section = row.closest('.meeting');
      const rowBody = row.dataset.body || (section && section.dataset.body) || '';
      const ok = (!cat || (row.dataset.categories || '').split(' ').includes(cat))
        && (!body || rowBody === body) && (!ward || row.dataset.ward === ward);
      row.hidden = !ok;
    });
    document.querySelectorAll('.meeting').forEach(s => {
      const filtering = cat || body || ward;
      const visible = [...s.querySelectorAll('.row')].some(r => !r.hidden);
      s.hidden = filtering ? !(visible || (body && s.dataset.body === body && !cat && !ward)) : false;
    });
  }
  ['f-cat', 'f-body', 'f-ward'].forEach(id => sel(id).addEventListener('change', apply));
</script>
</body>
</html>
```

(Do not use `%-d`-style format codes: Windows `strftime` does not support them.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_site.py -v`
Expected: 4 passed

- [ ] **Step 5: Build the site from the smoke-run data and look at it**

Run: `cd ingest && python -m digest.site`, then open `site/index.html` in a browser. Check: upcoming meetings are in date order, cancelled meetings are marked, the three filters narrow the list, a calendar link downloads an `.ics` that imports into a calendar app at the right local time.

- [ ] **Step 6: Commit**

```bash
git add ingest/digest/site.py ingest/digest/templates/index.html.j2 ingest/tests/test_site.py
git commit -m "Add static page and per-body calendar feeds"
```

---

### Task 14: Relevance eval and cost report

**Files:**
- Create: `ingest/digest/eval.py`, `ingest/digest/costs.py`, `ingest/tests/test_eval.py`, `ingest/tests/test_costs.py`

**Interfaces:**
- Consumes: `Digest`, `load_digest` (Task 1); `config/ai.yaml` prices (Task 12).
- Produces:
  - `digest.eval`: `sample(digest, n=30, seed=13) -> list[dict]`; `write_sample(rows, path)`; `score(path) -> dict`. CLI: `python -m digest.eval sample` writes `eval/to_label.csv`; Daniel fills the `label_relevant` (`y`/`n`) and `label_categories` (space-separated ids) columns and saves it as `eval/labels.csv`; `python -m digest.eval score` prints precision, recall and per-category agreement.
  - `digest.costs`: `summarize(usage_path, prices, since=None) -> dict`. CLI: `python -m digest.costs [--since YYYY-MM-DD]`.

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_eval.py`:

```python
import datetime as dt

from digest.eval import sample, score, write_sample
from digest.models import CHI, CategoryHit, Digest, Enrichment, Item, Meeting

NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=CHI)


def digest(n_rel=20, n_not=20):
    m = Meeting(id="m", source_id="s", body="B", body_id="b", start=NOW)
    items = [Item(id=f"r{i}", meeting_id="m", source_ref="x", title=f"Rel {i}",
                  enrichment=Enrichment(status="ok", housing_relevant=True, summary="s", categories=[CategoryHit(id="cha", via="model")]))
             for i in range(n_rel)]
    items += [Item(id=f"n{i}", meeting_id="m", source_ref="x", title=f"Not {i}", enrichment=Enrichment(status="screened_out"))
              for i in range(n_not)]
    items.append(Item(id="pending", meeting_id="m", source_ref="x", title="No enrichment"))
    return Digest(generated_at=NOW, meetings=[m], items=items)


def test_sample_is_balanced_and_deterministic():
    rows = sample(digest(), n=10)
    assert len(rows) == 10
    assert sum(r["model_relevant"] == "y" for r in rows) == 5
    assert "pending" not in {r["id"] for r in rows}
    assert rows == sample(digest(), n=10)
    assert rows[0]["label_relevant"] == "" and rows[0]["model_categories"] in ("cha", "")


def test_score(tmp_path):
    path = tmp_path / "labels.csv"
    rows = [
        {"id": "a", "model_relevant": "y", "model_categories": "cha", "label_relevant": "y", "label_categories": "cha"},
        {"id": "b", "model_relevant": "y", "model_categories": "cha ahpaa", "label_relevant": "n", "label_categories": ""},
        {"id": "c", "model_relevant": "n", "model_categories": "", "label_relevant": "y", "label_categories": "ahpaa"},
        {"id": "d", "model_relevant": "n", "model_categories": "", "label_relevant": "n", "label_categories": ""},
        {"id": "e", "model_relevant": "y", "model_categories": "", "label_relevant": "", "label_categories": ""},  # unlabeled
    ]
    write_sample(rows, path)
    s = score(path)
    assert s["labeled"] == 4
    assert s["precision"] == 0.5 and s["recall"] == 0.5
    assert s["categories"]["cha"] == {"tp": 1, "fp": 1, "fn": 0}
    assert s["categories"]["ahpaa"] == {"tp": 0, "fp": 1, "fn": 1}
```

`ingest/tests/test_costs.py`:

```python
import json

from digest.costs import summarize

PRICES = {"claude-haiku-5-5": {"input": 0.10, "output": 0.50}, "claude-sonnet-5-5": {"input": 2.00, "output": 10.00}}


def test_summarize(tmp_path):
    p = tmp_path / "usage.jsonl"
    rows = [
        {"ts": "2026-10-01T11:20:00+00:00", "purpose": "screen", "model": "claude-haiku-5-5", "input_tokens": 1_000_000, "output_tokens": 0},
        {"ts": "2026-10-05T11:20:00+00:00", "purpose": "summarize", "model": "claude-sonnet-5-5", "input_tokens": 500_000, "output_tokens": 100_000},
        {"ts": "2026-10-05T17:20:00+00:00", "purpose": "summarize", "model": "claude-sonnet-5-5", "input_tokens": 500_000, "output_tokens": 100_000},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    s = summarize(p, PRICES)
    assert s["by_purpose"]["screen"] == {"calls": 1, "input_tokens": 1_000_000, "output_tokens": 0, "usd": 0.1}
    assert s["by_purpose"]["summarize"]["usd"] == 4.0
    assert s["total_usd"] == 4.1
    assert summarize(p, PRICES, since="2026-10-05")["total_usd"] == 4.0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ingest && python -m pytest tests/test_eval.py tests/test_costs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'digest.eval'`

- [ ] **Step 3: Implement**

`ingest/digest/eval.py`:

```python
"""Hand-label a sample of enriched items, then score the model against the labels."""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from .models import Digest, load_digest

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["id", "body", "date", "title", "summary", "model_relevant", "model_categories", "label_relevant", "label_categories"]


def sample(digest: Digest, n: int = 30, seed: int = 13) -> list[dict]:
    meetings = {m.id: m for m in digest.meetings}
    done = [i for i in digest.items if i.enrichment and i.enrichment.status in ("ok", "screened_out")]
    rel = [i for i in done if i.enrichment.housing_relevant]
    non = [i for i in done if not i.enrichment.housing_relevant]
    rng = random.Random(seed)
    k = min(len(rel), n // 2)
    picked = rng.sample(rel, k) + rng.sample(non, min(len(non), n - k))
    rows = []
    for i in picked:
        m = meetings.get(i.meeting_id)
        rows.append({
            "id": i.id, "body": m.body if m else "", "date": m.start.date().isoformat() if m else "",
            "title": i.title, "summary": i.enrichment.summary,
            "model_relevant": "y" if i.enrichment.housing_relevant else "n",
            "model_categories": " ".join(c.id for c in i.enrichment.categories),
            "label_relevant": "", "label_categories": "",
        })
    return rows


def write_sample(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)


def score(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["label_relevant"].strip().lower() in ("y", "n")]
    tp = sum(r["model_relevant"] == "y" and r["label_relevant"].lower() == "y" for r in rows)
    fp = sum(r["model_relevant"] == "y" and r["label_relevant"].lower() == "n" for r in rows)
    fn = sum(r["model_relevant"] == "n" and r["label_relevant"].lower() == "y" for r in rows)
    cats: dict[str, dict[str, int]] = {}
    for r in rows:
        model, label = set(r["model_categories"].split()), set(r["label_categories"].split())
        for c in model | label:
            d = cats.setdefault(c, {"tp": 0, "fp": 0, "fn": 0})
            d["tp" if c in model and c in label else "fp" if c in model else "fn"] += 1
    return {"labeled": len(rows), "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "categories": dict(sorted(cats.items()))}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m digest.eval")
    p.add_argument("command", choices=["sample", "score"])
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--root", type=Path, default=ROOT)
    args = p.parse_args(argv)
    if args.command == "sample":
        digest = load_digest(args.root / "data" / "items.json")
        out = args.root / "eval" / "to_label.csv"
        write_sample(sample(digest, args.n), out)
        print(f"wrote {out}: fill label_relevant (y/n) and label_categories, save as eval/labels.csv")
    else:
        s = score(args.root / "eval" / "labels.csv")
        print(f"labeled {s['labeled']}  precision {s['precision']}  recall {s['recall']}")
        for c, d in s["categories"].items():
            print(f"  {c:16} tp {d['tp']:3}  fp {d['fp']:3}  fn {d['fn']:3}")


if __name__ == "__main__":
    main()
```

`ingest/digest/costs.py`:

```python
"""Dollars spent on Claude, from data/usage.jsonl: python -m digest.costs [--since YYYY-MM-DD]"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def summarize(usage_path: Path, prices: dict, since: str | None = None) -> dict:
    by_purpose: dict[str, dict] = {}
    for line in usage_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if since and r["ts"][:10] < since:
            continue
        p = prices.get(r["model"], {"input": 0.0, "output": 0.0})
        usd = (r["input_tokens"] * p["input"] + r["output_tokens"] * p["output"]) / 1_000_000
        d = by_purpose.setdefault(r["purpose"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0})
        d["calls"] += 1
        d["input_tokens"] += r["input_tokens"]
        d["output_tokens"] += r["output_tokens"]
        d["usd"] = round(d["usd"] + usd, 4)
    return {"by_purpose": by_purpose, "total_usd": round(sum(d["usd"] for d in by_purpose.values()), 4)}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m digest.costs")
    p.add_argument("--since")
    p.add_argument("--root", type=Path, default=ROOT)
    args = p.parse_args(argv)
    prices = yaml.safe_load((args.root / "config" / "ai.yaml").read_text(encoding="utf-8"))["prices"]
    s = summarize(args.root / "data" / "usage.jsonl", prices, args.since)
    for purpose, d in s["by_purpose"].items():
        print(f"{purpose:10} {d['calls']:5} calls  {d['input_tokens']:>10} in  {d['output_tokens']:>9} out  ${d['usd']:.2f}")
    print(f"total ${s['total_usd']:.2f}")


if __name__ == "__main__":
    main()
```

(Unknown models are priced at zero so a typo cannot crash the report. Check that every model in `ai.yaml` `models` has a `prices` entry.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && python -m pytest tests/test_eval.py tests/test_costs.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/eval.py ingest/digest/costs.py ingest/tests/test_eval.py ingest/tests/test_costs.py
git commit -m "Add relevance eval sampling/scoring and cost report"
```

---

### Task 15: Scheduled workflow, README, and the gated live steps

**Files:**
- Create: `.github/workflows/ingest.yml`, `README.md`

**Interfaces:**
- Consumes: `python -m digest.run`, `python -m digest.site` (Tasks 12–13); the `ANTHROPIC_API_KEY` repository secret.
- Produces: a workflow that runs at 11:20, 17:20 and 23:20 UTC (6:20 a.m., 12:20 p.m., 6:20 p.m. Chicago daylight time), commits `data/`, and deploys `site/` to GitHub Pages. Plan 2 reads `data/items.json` from the repo.

- [ ] **Step 1: Write the workflow**

`.github/workflows/ingest.yml`:

```yaml
name: ingest
on:
  schedule:
    - cron: "20 11,17,23 * * *"
  workflow_dispatch:

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: ingest
  cancel-in-progress: false

jobs:
  ingest:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: ingest/pyproject.toml
      - run: pip install -e "ingest[dev]"
      - name: Tests
        run: cd ingest && python -m pytest -q
      - name: Ingest
        run: cd ingest && python -m digest.run
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: Build site
        run: cd ingest && python -m digest.site
      - name: Commit data
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data
          git diff --cached --quiet || git commit -m "data: ingest $(date -u +%Y-%m-%dT%H:%MZ)"
          git push
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: site
      - id: deploy
        uses: actions/deploy-pages@v4
```

(The bot commits `data/` under the GitHub Actions identity, not Daniel's. Human commits keep the identity in Global Constraints.)

- [ ] **Step 2: Write the README**

`README.md`:

````markdown
# Chicago Housing Digest

Collects Chicago housing-related public meetings, agenda items and notices into `data/items.json`, summarizes each item once with Claude, and publishes a page with calendar feeds. The email digest (Plan 2) reads `data/items.json`.

Sources: City Council committees on Zoning, Housing and Real Estate, and Finance (eLMS); CHA board and committees (Legistar); Plan Commission and Community Development Commission (chicago.gov); IHDA board, committees, TEFRA hearings and notices; CLIHTF board; and a page watcher for DOH, CHA and CLIHTF pages (`config/watch.yaml`).

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "ingest[dev]"   # .venv/bin/python on Linux/macOS
source .venv/Scripts/activate
```

## Commands (run from `ingest/`)

| Command | What it does |
|---|---|
| `python -m pytest` | Tests (no network, no Claude calls) |
| `python -m digest.run --no-ai` | Fetch all sources, merge, write `data/items.json` without Claude |
| `python -m digest.run` | Full run; needs `ANTHROPIC_API_KEY` |
| `python -m digest.run --sources cpc,cdc` | Refresh only some sources |
| `python -m digest.site` | Build `site/` from `data/items.json` |
| `python -m digest.costs [--since YYYY-MM-DD]` | Claude spend from `data/usage.jsonl` |
| `python -m digest.eval sample` / `score` | Hand-label a sample, then score relevance and categories |
| `python -m digest.schema` | Regenerate `schema/items.schema.json` after changing `digest/models.py` |

## Editing behavior

- **Categories:** `config/categories.yaml`. Editing it re-enriches every record on the next run (a cost, not a migration).
- **Fix one item:** add its id to `config/overrides.yaml`.
- **Models and prices:** `config/ai.yaml`.
- **Watch another page:** add it to `config/watch.yaml`. The first run records a baseline and emits nothing.
- **Source health:** `sources[]` in `data/items.json` (`ok`, `stale` with a message, or `error`). A failing source keeps its last good data.
````

- [ ] **Step 3: Validate the workflow file parses**

Run: `python -c "import yaml; d = yaml.safe_load(open('.github/workflows/ingest.yml')); print([s.get('uses') or s.get('name') for s in d['jobs']['ingest']['steps']])"`
Expected: `['actions/checkout@v4', 'actions/setup-python@v5', None, 'Tests', 'Ingest', 'Build site', 'Commit data', 'actions/configure-pages@v5', 'actions/upload-pages-artifact@v3', 'actions/deploy-pages@v4']`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ingest.yml README.md
git commit -m "Add scheduled ingest workflow and README"
```

- [ ] **Step 5: STOP. Ask Daniel before the first live AI run (spends money)**

Ask: "Ready for the first full run with Claude. It enriches every item currently in the window, so it is the most expensive run there will be (later runs only enrich new or changed items). OK to run it?" Only after a yes:

```bash
cd ingest && python -m digest.run && python -m digest.costs && python -m digest.site
```

Report: the cost of this first run, the number of items enriched, how many were screened out, and three sample summaries with their sources so Daniel can judge quality. Estimate the steady-state daily cost from a second run made a few hours later (`python -m digest.costs --since <today>` minus the first run).

- [ ] **Step 6: Relevance eval**

Run `python -m digest.eval sample` and hand `eval/to_label.csv` to Daniel with the instructions the command prints. After `eval/labels.csv` exists, run `python -m digest.eval score` and report precision and recall. If recall is below 0.9, loosen the screen prompt (`SCREEN_SYSTEM` in `enrich.py`), bump `PROMPT_VERSION`, and re-run only after Daniel approves the cost.

- [ ] **Step 7: STOP. Ask Daniel before creating the GitHub repo or pushing**

Ask whether to create `danielkayhertz/chicago-housing-digest` (public or private), push, add the `ANTHROPIC_API_KEY` secret, and enable Pages with source "GitHub Actions". Do none of these without a yes. After he approves and the secret is set, trigger the workflow once with `gh workflow run ingest` and confirm it goes green and the page loads.
