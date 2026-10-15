# Scannable Meeting Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Chicago Housing Digest page skimmable: color-banded meetings under day headings, a "This week" index plus a sticky side panel, City Council items grouped by type with big items as cards and routine ones in a compact table, and fact chips (ward, homes, dollars, zoning change, buyer) on every item.

**Architecture:** A new pure module `digest/layout.py` turns the shown meetings and items into an outline (index → days → meetings → groups → cards/rows, with headlines and facts resolved), driven by `config/layout.yaml`. `site.py` builds the outline and the Jinja template renders it; nothing new is stored. Two new facts (dollar amount, zoning change) come from new fields on the existing AI summary call, grounded against source text like the existing unit counts. The text helpers `safe_url`, `short_title`, `trim_summary` move to `digest/text.py` so `layout.py` and `site.py` can both use them without a circular import.

**Tech Stack:** Python 3 (pydantic v2, Jinja2, PyYAML, pytest), plain HTML/CSS and a small inline script. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-14-scannable-page-design.md`

## Global Constraints

- Python is `.venv/Scripts/python.exe` at the repo root; a bare `python` hangs. Run tests from `ingest/`: `cd ingest && ../.venv/Scripts/python.exe -m pytest -q`. Baseline: 195 pass.
- Do NOT change `PROMPT_VERSION` in `ingest/digest/enrich.py` (stays `"1"`). Bumping it makes the next scheduled run re-summarize every item (about $3–5); that run needs Daniel's explicit yes and happens after this plan, as a separate one-line commit.
- Do NOT run `python -m digest.run` (it makes paid API calls). Do NOT push, and do NOT run `gh workflow run`. The cron deploys from `master` three times a day.
- Commit identity: Daniel Kay Hertz <danielkayhertz@gmail.com>. Every commit message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never put anyone's email address in a User-Agent or other outgoing request.
- Families and color tokens are exactly these (tint / rule / ink):
  - council, light `#e7f5ff` / `#3a84ca` / `#0e4f86`, dark `#1e2f41` / `#67aaed` / `#a6d2ff`
  - ihda, light `#e5f9ef` / `#00986c` / `#005f3e`, dark `#193429` / `#4dbc92` / `#9cdfc1`
  - clihtf, light `#fff0e2` / `#b46d10` / `#753d00`, dark `#3c2a18` / `#d8944d` / `#f3c497`
  - planning, light `#f7f0ff` / `#926abe` / `#5b3b7c`, dark `#32293e` / `#b691e1` / `#dac1f8`
- Light neutrals: page `#ffffff`, soft `#f7fafe`, chip `#ebeff2`, line `#dbdee2`, muted `#535659`, ink `#1f2224`. Dark neutrals stay as today: bg `#202124`, ink `#e8eaed`, muted `#9aa0a6`, line `#3c4043`, chip `#2b3440`.
- Card rule: `units >= 10` or `amount >= 1_000_000` or the group is Planned developments, Zoning code changes, or Other. Flat (ungrouped) bodies: every item is a card.
- The family is always named in text next to its color.
- Side panel shows at viewport width `70rem` and above; below that it is `display: none` and the index serves alone.
- Do not add `.superpowers/` or `site/` contents to commits (both are gitignored).

## Review Focus

1. **An item with no enrichment, or a failed one, inside a grouped body** (new items appear before the AI runs). Expected: it renders as a table row or card with its title-derived headline and no chips, no crash. Test: Task 4 `test_unenriched_item_in_a_grouped_body_is_a_row_with_no_facts`.
2. **Markup in model-written text** (entity names, amount kinds, zoning codes, titles). Expected: shown as text, escaped. Test: Task 5 `test_grouped_body_renders_group_heading_cards_and_a_table` asserts `&lt;b&gt;Evil&lt;/b&gt; LLC`.
3. **A meeting stored in UTC near midnight**. Expected: it lands under its Chicago date. Test: Task 4 `test_outline_days_index_and_groups` (IHDA meeting at 11:30 PM Chicago stored as UTC).
4. **A meeting whose items are all hidden (not about housing) or that has no items** (cancelled CDC). Expected: it still appears in the index and its day with "0 items", no empty group headings or empty tables. Test: Task 5 `test_index_and_panel_list_every_shown_meeting_in_page_order` and Task 4 group assertions.
5. **A dollar figure written in millions** ("$51.5 million") while the model returns whole dollars. Expected: kept, not dropped as ungrounded. Test: Task 2 `test_amount_in_millions_is_grounded`.

---

### Task 1: Count homes across several buildings

**Files:**
- Modify: `ingest/digest/units.py`
- Test: `ingest/tests/test_units.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `parse_units(text: str) -> int | None` (same signature) now returns `N * M` for "N M-unit … buildings".

- [ ] **Step 1: Write the failing tests**

In `ingest/tests/test_units.py`, add these tuples to the end of the `AGENDA_CASES` list (before the closing `]`):

```python
    ("The buyer would build two 8-unit masonry residential buildings with ARO units.", 16),
    ("Construction of 2 12-unit residential buildings on the site.", 24),
    ("would allow an 8-unit building on a vacant lot", 8),
    ("a 3-story, 6-unit building with two 2-unit coach houses", 6),
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest tests/test_units.py -q`
Expected: 2 failures (the 16 and 24 cases return 8 and 12).

- [ ] **Step 3: Implement**

In `ingest/digest/units.py`, after the `_NON_DWELLING` definition add:

```python
# "TWO 8-UNIT MASONRY RESIDENTIAL BUILDINGS": several buildings of one size. The count is their total.
_MULTI = re.compile(r"(?<![\d,])\b(\d{1,2}|" + "|".join(_WORD_NUMBERS) + r")\s+(\d{1,3})[- ]UNIT\s+"
                    r"(?:[A-Z]+\s+){0,3}?BUILDINGS\b")
```

Replace the body of `parse_units` with:

```python
def parse_units(text: str) -> int | None:
    """The dwelling-unit count stated in the text (the largest, if several), or None."""
    desc = (text or "").upper()
    multi = [_value(n) * int(m) for n, m in _MULTI.findall(desc)]
    for tier in (_UNIT_PATTERNS_STRONG, _UNIT_PATTERNS_WEAK):
        found = list(multi) if tier is _UNIT_PATTERNS_STRONG else []
        for pat in tier:
            for m in re.finditer(pat, desc):
                if _AREA_SUFFIX.match(desc, m.end(1)) or _NON_DWELLING.match(desc, m.end(1)):
                    continue
                n = _value(m.group(1))
                if 1 <= n <= 2000:
                    found.append(n)
        found = [n for n in found if 1 <= n <= 2000]
        if found:
            return max(found)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest tests/test_units.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/units.py ingest/tests/test_units.py
git commit -m "Count homes across several same-size buildings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Dollar amount and zoning change from the summary call

**Files:**
- Modify: `ingest/digest/models.py` (class `Enrichment`)
- Modify: `ingest/digest/enrich.py` (class `Summary`, `SUMMARY_SYSTEM`, `_fresh`, `OVERRIDABLE`, new `_grounded_amount`)
- Modify: `schema/items.schema.json` (regenerated)
- Test: `ingest/tests/test_enrich.py`, `ingest/tests/test_run.py`

**Interfaces:**
- Consumes: `parse_units` from Task 1.
- Produces: `Enrichment.amount: int | None`, `Enrichment.amount_kind: str | None`, `Enrichment.zoning_from: list[str]`, `Enrichment.zoning_to: str | None` (all default empty). Task 3 reads these.

- [ ] **Step 1: Update the test helpers so they build the new Summary shape**

In `ingest/tests/test_enrich.py`, in `summary()`, extend `base` so it reads:

```python
    base = dict(housing_relevant=True, relevance_reason="Adds homes.", summary="MKR Owner LLC proposes 1,200 homes.",
                categories=["ahpaa", "not-a-category", "housing-agenda"], address="1133 W. Kinzie St.",
                entities=["MKR Owner LLC", "Invented Partners LP"], units=1200, affordable_units=999,
                amount=None, amount_kind=None, zoning_from=[], zoning_to=None)
```

In `ingest/tests/test_run.py`, in `fake_llm()`, change the `Summary(...)` call to:

```python
        "summarize": lambda content: Summary(housing_relevant=True, relevance_reason="r", summary="s", categories=[],
                                             address=None, entities=[], units=None, affordable_units=None,
                                             amount=None, amount_kind=None, zoning_from=[], zoning_to=None),
```

- [ ] **Step 2: Write the failing tests**

Append to `ingest/tests/test_enrich.py`:

```python
SALE_HTML = (b"<html><body><p>Sale of 4549 S. Indiana Ave. to Click Development, LLC for a sale price of $315,315. "
             b"The buyer would build two 8-unit masonry residential buildings. The site is rezoned from RS-3 and RT-4 "
             b"to Business Planned Development No. 1143. IHDA would make a $51.5 million conduit loan.</p></body></html>")


def sale_item():
    return pd_item(url="https://x/sale.html", item_id="elms:H1#sale")


def test_amount_and_kind_are_kept_when_grounded_and_dropped_when_not(tmp_path):
    llm = FakeLLM({"summarize": [summary(amount=315315, amount_kind=" sale price "),
                                 summary(amount=999999, amount_kind="sale price")]})
    c = ctx(tmp_path, llm, routes=(("sale.html", SALE_HTML),))
    e = enrich_record(record_for_item(sale_item(), CPC), c)
    assert (e.amount, e.amount_kind) == (315315, "sale price")
    c.cache = JsonCache(tmp_path / "other.json")
    e = enrich_record(record_for_item(sale_item(), CPC), c)
    assert (e.amount, e.amount_kind) == (None, None)


def test_amount_in_millions_is_grounded(tmp_path):
    llm = FakeLLM({"summarize": [summary(amount=51_500_000, amount_kind="conduit loan")]})
    e = enrich_record(record_for_item(sale_item(), CPC), ctx(tmp_path, llm, routes=(("sale.html", SALE_HTML),)))
    assert (e.amount, e.amount_kind) == (51_500_000, "conduit loan")


def test_zoning_codes_are_kept_only_when_each_appears_in_the_text(tmp_path):
    llm = FakeLLM({"summarize": [summary(zoning_from=["RS-3", "RT-4", "B3-2"],
                                         zoning_to="Business Planned Development No. 1143"),
                                 summary(zoning_from=["RS-3"], zoning_to="DX-12")]})
    c = ctx(tmp_path, llm, routes=(("sale.html", SALE_HTML),))
    e = enrich_record(record_for_item(sale_item(), CPC), c)
    assert e.zoning_from == ["RS-3", "RT-4"] and e.zoning_to == "Business Planned Development No. 1143"
    c.cache = JsonCache(tmp_path / "other.json")
    e = enrich_record(record_for_item(sale_item(), CPC), c)
    assert e.zoning_from == ["RS-3"] and e.zoning_to is None


def test_unit_total_across_buildings_is_grounded(tmp_path):
    llm = FakeLLM({"summarize": [summary(units=16, summary="Click Development, LLC would build 16 homes.")]})
    e = enrich_record(record_for_item(sale_item(), CPC), ctx(tmp_path, llm, routes=(("sale.html", SALE_HTML),)))
    assert e.figures.units == 16                                     # 2 x 8 from the text, not a bare "16"


def test_overrides_accept_amount_and_zoning(tmp_path):
    llm = FakeLLM({"summarize": [summary()]})
    ov = {pd_item().id: {"amount": 2_000_000, "amount_kind": "grant", "zoning_from": ["M2-3"], "zoning_to": "DX-5"}}
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, overrides=ov))
    assert (e.amount, e.amount_kind, e.zoning_from, e.zoning_to) == (2_000_000, "grant", ["M2-3"], "DX-5")
    assert e.overridden
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest tests/test_enrich.py tests/test_run.py -q`
Expected: failures (Summary rejects the unknown fields; Enrichment has no `amount`).

- [ ] **Step 4: Add the fields to the models**

In `ingest/digest/models.py`, in `class Enrichment`, add after `figures: Figures = Field(default_factory=Figures)`:

```python
    amount: int | None = None  # the dollar figure the item turns on, in whole dollars
    amount_kind: str | None = None  # what that figure is: "sale price", "conduit loan"
    zoning_from: list[str] = Field(default_factory=list)  # a rezoning's current districts, as written
    zoning_to: str | None = None  # and the requested one
```

In `ingest/digest/enrich.py`, in `class Summary`, add after `affordable_units: int | None`:

```python
    amount: int | None
    amount_kind: str | None
    zoning_from: list[str]
    zoning_to: str | None
```

- [ ] **Step 5: Update the prompt**

In `SUMMARY_SYSTEM`, replace the last line

```
units, affordable_units: counts of homes stated in the material, or null. Never estimate."""
```

with

```
units, affordable_units: counts of homes stated in the material, or null. When the material describes several buildings, give the total across all of them. Never estimate.
amount, amount_kind: the single dollar figure the item turns on (a sale price, loan, bond cap, grant, or fee waiver) in whole dollars, and what it is in two to four words ("sale price", "conduit loan"); null for both if there is none.
zoning_from, zoning_to: for a rezoning, the current district codes and the requested one, copied as written ("RS-3", "Business Planned Development No. 1143"); otherwise an empty list and null."""
```

Leave `PROMPT_VERSION = "1"` unchanged.

- [ ] **Step 6: Ground the new fields**

In `ingest/digest/enrich.py`, after `_grounded_number` add:

```python
_MILLIONS = re.compile(r"(\d+(?:\.\d+)?)\s*million\b")


def _grounded_amount(n: int | None, numbers: set[str], corpus: str) -> int | None:
    """A dollar figure stated in the text, as digits ("$315,315") or in millions ("$51.5 million")."""
    if n is None or n <= 0:
        return None
    if str(n) in numbers:
        return n
    return n if any(round(float(x) * 1_000_000) == n for x in _MILLIONS.findall(corpus)) else None
```

In `_fresh`, change `numbers = _numbers(corpus)` to:

```python
    numbers = _numbers(corpus)
    if total := parse_units(corpus):  # "two 8-unit buildings" grounds a total of 16
        numbers.add(str(total))
```

In `_fresh`, after the `address` lines (just before `late_rules = ...`), add:

```python
    amount = _grounded_amount(s.amount, numbers, corpus)
    if s.amount is not None and amount is None:
        log.info("%s: dropped ungrounded amount %s", rec.id, s.amount)
    amount_kind = ((s.amount_kind or "").strip()[:40] or None) if amount else None
    zoning_from = [z.strip() for z in s.zoning_from if z.strip() and _norm(z) in corpus]
    zoning_to = s.zoning_to.strip() if s.zoning_to and s.zoning_to.strip() and _norm(s.zoning_to) in corpus else None
```

and in the returned `Enrichment(...)`, after `figures=Figures(**figures),` add:

```python
        amount=amount, amount_kind=amount_kind, zoning_from=zoning_from, zoning_to=zoning_to,
```

Change `OVERRIDABLE` to:

```python
OVERRIDABLE = ("housing_relevant", "relevance_reason", "summary", "categories", "geography", "entities", "figures",
               "amount", "amount_kind", "zoning_from", "zoning_to")
```

- [ ] **Step 7: Regenerate the committed JSON schema**

Run: `cd ingest && ../.venv/Scripts/python.exe -m digest.schema`
Expected: `wrote ...schema/items.schema.json`.

- [ ] **Step 8: Run the full suite**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (195 + 4 from Task 1 + 5 new).

- [ ] **Step 9: Commit**

```bash
git add ingest/digest/models.py ingest/digest/enrich.py schema/items.schema.json ingest/tests/test_enrich.py ingest/tests/test_run.py
git commit -m "Extract dollar amounts and zoning changes in the summary call

Grounded against the source text like unit counts. PROMPT_VERSION is unchanged,
so existing items get the new fields only after a deliberate re-run.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Layout config, groups, card rule, headlines and facts

**Files:**
- Create: `config/layout.yaml`
- Create: `ingest/digest/text.py` (moved helpers)
- Create: `ingest/digest/layout.py`
- Modify: `ingest/digest/site.py` (import the moved helpers from `.text`)
- Test: `ingest/tests/test_layout.py`

**Interfaces:**
- Consumes: `Enrichment.amount`, `.amount_kind`, `.zoning_from`, `.zoning_to` (Task 2); `title_address(title) -> str | None` from `digest.enrich`.
- Produces (in `digest.layout`):
  - `DEFAULT_PATH: Path` (repo `config/layout.yaml`), `OTHER = "Other"`
  - `Family(id: str, label: str)` frozen dataclass
  - `Layout.load(path: Path) -> Layout`; attributes `families: dict[str, Family]` (body → family), `grouping: dict[str, list[Rule]]`, `always_card: set[str]`, `min_units: int`, `min_amount: int`, `address_groups: set[str]`, `name_groups: set[str]`
  - `Seg(text: str, bold: bool = False)` frozen dataclass; a fact is `tuple[Seg, ...]`
  - `fact_text(fact) -> str`
  - `group_for(layout, body: str, title: str, summary: str) -> str | None` (None = flat body)
  - `is_card(layout, group: str | None, units: int | None, amount: int | None) -> bool`
  - `headline(layout, item: Item, group: str | None) -> str`
  - `format_amount(n: int) -> str`
  - `facts(item: Item, head: str) -> list[tuple[Seg, ...]]`
- Produces (in `digest.text`): `safe_url`, `LONG_TITLE`, `short_title`, `trim_summary` (moved unchanged from `site.py`; `site.py` re-imports them so `from digest.site import short_title` keeps working).

- [ ] **Step 1: Move the text helpers**

Create `ingest/digest/text.py`:

```python
"""Small text helpers shared by the page layout and the site builder."""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def safe_url(url: str | None) -> str | None:
    """The URL when its scheme is http or https; None otherwise (javascript:, data:, relative, malformed)."""
    if not url:
        return None
    url = url.strip()
    try:
        scheme = urlsplit(url).scheme.strip().lower()
    except ValueError:
        return None
    return url if scheme in ("http", "https") else None


LONG_TITLE = 120


def short_title(title: str, limit: int = 100) -> str:
    """A title cut at a word boundary for the headline; the full text stays on the page under a disclosure."""
    if len(title) <= LONG_TITLE:
        return title
    cut = title[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return f"{cut}…"


_ABSENCE = re.compile(r"no further|gives no|provides no|no other details?|no details|only the meeting agenda"
                      r"|does not (?:name|give|say|state|specify)", re.I)


def trim_summary(summary: str) -> str:
    """Drop sentences that only say what the source leaves out. Older summaries carry them; the prompt now forbids them."""
    pieces = re.split(r"(?<=[.;])\s+", summary.strip())
    keep = [p for p in pieces if not _ABSENCE.search(p)]
    if not keep or len(keep) == len(pieces):
        return summary
    keep = [p[:-1] + "." if p.endswith(";") else p for p in keep]
    return " ".join(p[0].upper() + p[1:] for p in keep)  # a clause that followed a dropped "...;" starts a sentence now
```

In `ingest/digest/site.py`, delete the definitions of `safe_url`, `LONG_TITLE`, `short_title`, `_ABSENCE`, `trim_summary`, and add near the other imports:

```python
from .text import LONG_TITLE, safe_url, short_title, trim_summary  # noqa: F401  (tests import these from here)
```

Remove `import re` and `from urllib.parse import urlsplit` from `site.py` if nothing else there uses them.

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (pure move).

- [ ] **Step 2: Write the config**

Create `config/layout.yaml`:

```yaml
# How the meeting page groups and colors items. Read when the page is built; changing it never needs an AI re-run.

# Color family per body. A body not listed here gets neutral colors and no family label.
families:
  council:
    label: City Council
    bodies: [City Council Committee on Zoning, City Council Committee on Housing and Real Estate,
             City Council Committee on Finance]
  ihda:
    label: IHDA
    bodies: [IHDA Board, IHDA Finance Committee, IHDA Audit Committee]
  clihtf:
    label: CLIHTF
    bodies: [CLIHTF Board of Directors]
  planning:
    label: Planning
    bodies: [Chicago Plan Commission, Community Development Commission]

# Bodies listed here get type groups, shown in this order. The first matching rule wins: the title must start with
# one of title_prefix, and pattern (a regex, if given) must match the title or the summary. Unmatched items go to
# Other, shown last. Bodies not listed are a flat list in agenda order.
grouping:
  City Council Committee on Housing and Real Estate:
    - {label: Sales of city land, title_prefix: [Sale of City-owned]}
    - {label: CHA board appointments, title_prefix: [Appointment of]}
  City Council Committee on Zoning:
    - {label: Planned developments, title_prefix: [Zoning Reclassification], pattern: 'Planned Development|\bPD\b'}
    - {label: Single-site rezonings, title_prefix: [Zoning Reclassification]}
    - {label: Zoning code changes, title_prefix: [Amendment of Municipal Code, Designation of]}
  City Council Committee on Finance: []

# Inside a grouped body an item is a card if it is big; the rest go in a compact table.
always_card_groups: [Planned developments, Zoning code changes, Other]
card_min_units: 10
card_min_amount: 1000000

# The headline is the site address in these groups and the appointee's name in these; otherwise the title.
address_groups: [Sales of city land, Single-site rezonings, Planned developments]
name_groups: [CHA board appointments]
```

- [ ] **Step 3: Write the failing tests**

Create `ingest/tests/test_layout.py`:

```python
from digest.layout import (DEFAULT_PATH, OTHER, Layout, Seg, fact_text, facts, format_amount, group_for, headline,
                           is_card)
from digest.models import Enrichment, Figures, Geography, Item

L = Layout.load(DEFAULT_PATH)
HRE = "City Council Committee on Housing and Real Estate"
ZON = "City Council Committee on Zoning"


def item(title, id="x#1", **e):
    enr = Enrichment(status="ok", housing_relevant=True, **e) if e else None
    return Item(id=id, meeting_id="x", source_ref="O2026-1", title=title, enrichment=enr)


def test_families_come_from_config():
    assert L.families[ZON].id == "council" and L.families[ZON].label == "City Council"
    assert L.families["IHDA Audit Committee"].id == "ihda"
    assert L.families["Community Development Commission"].label == "Planning"
    assert "Some New Body" not in L.families


def test_group_rules():
    assert group_for(L, HRE, "Sale of City-owned property at 4549 S Indiana Ave to Click Development, LLC", "") \
        == "Sales of city land"
    assert group_for(L, HRE, "Appointment of Vernell Givens as Commissioner of Chicago Housing Authority", "") \
        == "CHA board appointments"
    assert group_for(L, HRE, "Amendment of loan agreement", "") == OTHER
    assert group_for(L, ZON, "Zoning Reclassification Map No. 8-F at 3800 S State St",
                     "Rezones to Business Planned Development No. 1143.") == "Planned developments"
    assert group_for(L, ZON, "Zoning Reclassification Map No. 1-H at 10 W Erie St", "From DX-5 to PD 512.") \
        == "Planned developments"
    assert group_for(L, ZON, "Zoning Reclassification Map No. 5-H at 5312 S Kenneth Ave", "Rezones RS-2 to RT-4.") \
        == "Single-site rezonings"
    assert group_for(L, ZON, "Zoning Reclassification Map No. 1-H at 10 W Erie St", "Rezones the UPDATED site.") \
        == "Single-site rezonings"                                   # "PD" inside a word is not a match
    assert group_for(L, ZON, "Amendment of Municipal Code Section 17-3", "") == "Zoning code changes"
    assert group_for(L, ZON, "Designation of segment of N Milwaukee Ave as Pedestrian Street", "") \
        == "Zoning code changes"
    assert group_for(L, ZON, "Historical landmark fee waiver for 1 N State St", "") == OTHER
    assert group_for(L, "City Council Committee on Finance", "Issuance of multi-family housing revenue bonds", "") == OTHER
    assert group_for(L, "IHDA Board", "Pheasant Ridge conduit loan", "") is None
    assert group_for(L, "Some New Body", "x", "") is None


def test_card_or_row():
    assert is_card(L, "Sales of city land", 10, None)
    assert not is_card(L, "Sales of city land", 9, None)
    assert is_card(L, "Sales of city land", None, 1_000_000)
    assert not is_card(L, "Sales of city land", None, 999_999)
    assert not is_card(L, "Single-site rezonings", 3, None)
    for g in ("Planned developments", "Zoning code changes", OTHER):
        assert is_card(L, g, None, None)
    assert is_card(L, None, None, None)                              # flat bodies: always cards


def test_headlines():
    sale = item("Sale of City-owned property at 4549 S Indiana Ave to Click Development, LLC",
                geography=Geography(address="4549 S. Indiana Ave."))
    assert headline(L, sale, "Sales of city land") == "4549 S. Indiana Ave."
    rez = item("Zoning Reclassification Map No. 5-H at 5312 S Kenneth Ave - App No. 22870")
    assert headline(L, rez, "Single-site rezonings") == "5312 S Kenneth Ave"   # no enrichment: from the title
    appt = item("Appointment of Vernell Givens as Commissioner of Chicago Housing Authority for a term expiring 2031.")
    assert headline(L, appt, "CHA board appointments") == "Vernell Givens"
    assert headline(L, item("Sale of City-owned property generally"), "Sales of city land") \
        == "Sale of City-owned property generally"                    # no address anywhere: the title
    assert headline(L, item("Appointment to the board"), "CHA board appointments") == "Appointment to the board"
    assert headline(L, item("Multifamily Update"), None) == "Multifamily Update"
    assert headline(L, item("word " * 40), None).endswith("…")


def test_facts_order_and_format():
    it = item("Sale of City-owned property at 2054 W Warren Blvd", geography=Geography(ward=27),
              figures=Figures(units=20, affordable_units=4), amount=315315, amount_kind="sale price",
              entities=["City of Chicago", "Department of Housing", "EGP Warren Apartments LLC"])
    got = facts(it, "2054 W Warren Blvd")
    assert [fact_text(f) for f in got] == ["Ward 27", "20 homes · 4 affordable", "$315,315 sale price",
                                           "EGP Warren Apartments LLC"]
    assert got[0] == (Seg("Ward "), Seg("27", True))
    assert got[1][0] == Seg("20", True)


def test_zoning_and_amount_formats():
    it = item("Zoning Reclassification Map No. 8-F at 3800 S State St", zoning_from=["RS-3", "RT-4", "PD 1143"],
              zoning_to="Business Planned Development No. 1143", amount=51_500_000, figures=Figures(units=1))
    assert [fact_text(f) for f in facts(it, "3800 S State St")] == [
        "1 home", "$51.5M", "RS-3, RT-4, PD 1143 → Business Planned Development No. 1143"]
    assert [fact_text(f) for f in facts(item("x", zoning_to="RT-4"), "x")] == ["to RT-4"]
    assert [fact_text(f) for f in facts(item("x", zoning_from=["RS-3"]), "x")] == []   # no target: no chip
    assert format_amount(999_999) == "$999,999" and format_amount(1_000_000) == "$1.0M"


def test_party_skips_public_bodies_and_the_headline():
    appt = item("Appointment of Vernell Givens as Commissioner", entities=["Vernell Givens", "Chicago Housing Authority"])
    assert facts(appt, "Vernell Givens") == []
    assert facts(item("No enrichment yet"), "x") == []
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest tests/test_layout.py -q`
Expected: collection error, `No module named 'digest.layout'`.

- [ ] **Step 5: Implement `layout.py` (config and item-level functions)**

Create `ingest/digest/layout.py`:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add config/layout.yaml ingest/digest/text.py ingest/digest/layout.py ingest/digest/site.py ingest/tests/test_layout.py
git commit -m "Add page layout rules: families, type groups, card rule, headlines, facts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The page outline (days, index, meetings, groups)

**Files:**
- Modify: `ingest/digest/layout.py` (append the outline types and `build_outline`)
- Test: `ingest/tests/test_layout.py` (append)

**Interfaces:**
- Consumes: everything Task 3 produces.
- Produces (in `digest.layout`):
  - `ItemView` with fields `item: Item, anchor: str, group: str | None, headline: str, full_title: str | None, facts: list[tuple[Seg, ...]], card: bool, summary: str, docs: list[Attachment], categories: str, ward: str` and property `facts_text: str` (facts joined by `" · "`)
  - `GroupView(label: str, cards: list[ItemView], rows: list[ItemView])` with property `count: int`
  - `MeetingView(meeting: Meeting, anchor: str, family: Family | None, when: str, groups: list[GroupView] | None, cards: list[ItemView], count: int, hidden: int, ics: str)`; `groups is None` means a flat body
  - `DayView(id: str, label: str, meetings: list[MeetingView])`
  - `IndexEntry(anchor: str, date_label: str, family: Family | None, body: str, count: int)`
  - `Outline(index: list[IndexEntry], days: list[DayView], has_upcoming: bool)`
  - `Shown = tuple[Meeting, list[Item], int]` (meeting, its shown items in agenda order, count of hidden items)
  - `build_outline(upcoming: list[Shown], recent: list[Shown], layout: Layout, ics: dict[str, str]) -> Outline`. Upcoming is sorted by start; recent newest first and gathered under one day with id `"recent"` and label `"Recent"`. Upcoming meetings' `when` is the time only (`"10:00 AM"`, or `""` if the time is unknown); recent meetings' `when` has the date (`"Tuesday, October 13, 10:00 AM"`).

- [ ] **Step 1: Write the failing tests**

Append to `ingest/tests/test_layout.py`:

```python
import datetime as dt

from digest.layout import build_outline
from digest.models import CHI, Meeting


def meeting(id, body, start):
    return Meeting(id=id, source_id="s", body=body, body_id="b", start=start)


def test_outline_days_index_and_groups():
    h = meeting("elms:H", HRE, dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI))
    cpc = meeting("cpc:1", "Chicago Plan Commission", dt.datetime(2026, 10, 15, 13, 0, tzinfo=CHI))
    late = meeting("ihda:1", "IHDA Board", dt.datetime(2026, 10, 16, 23, 30, tzinfo=CHI).astimezone(dt.timezone.utc))
    z = meeting("elms:Z", ZON, dt.datetime(2026, 10, 13, 10, 0, tzinfo=CHI))
    big = item("Sale of City-owned property at 1 W Alpha St", id="h#1", figures=Figures(units=20))
    small = item("Sale of City-owned property at 2 W Beta St", id="h#2", figures=Figures(units=2))
    appt = item("Appointment of Jo Doe as Commissioner", id="h#3")
    loan = item("Kinzie Planned Development", id="c#1")
    rez = item("Zoning Reclassification Map No. 1-H at 3 W Gamma St", id="z#1")
    o = build_outline([(cpc, [loan], 0), (late, [], 2), (h, [big, appt, small], 1)], [(z, [rez], 0)], L,
                      {b: f"ics/{b}.ics" for b in (HRE, ZON, "Chicago Plan Commission", "IHDA Board")})
    assert o.has_upcoming
    assert [(d.id, d.label) for d in o.days] == [("day-2026-10-15", "Thursday, October 15"),
                                                 ("day-2026-10-16", "Friday, October 16"), ("recent", "Recent")]
    assert [mv.meeting.id for mv in o.days[0].meetings] == ["elms:H", "cpc:1"]          # sorted by start
    assert [(x.anchor, x.date_label, x.family.id, x.count) for x in o.index] == [
        ("elms-h", "Thu Oct 15", "council", 3), ("cpc-1", "Thu Oct 15", "planning", 1),
        ("ihda-1", "Fri Oct 16", "ihda", 0), ("elms-z", "Tue Oct 13", "council", 1)]
    hv = o.days[0].meetings[0]
    assert hv.when == "10:00 AM" and hv.hidden == 1 and hv.ics == f"ics/{HRE}.ics" and hv.cards == []
    assert [g.label for g in hv.groups] == ["Sales of city land", "CHA board appointments"]
    sales = hv.groups[0]
    assert [v.item.id for v in sales.cards] == ["h#1"] and [v.item.id for v in sales.rows] == ["h#2"]
    assert sales.count == 2 and hv.groups[1].rows[0].headline == "Jo Doe"
    cv = o.days[0].meetings[1]
    assert cv.groups is None and [v.item.id for v in cv.cards] == ["c#1"] and cv.cards[0].card
    assert o.days[1].meetings[0].groups is None and o.days[1].meetings[0].count == 0     # IHDA: flat, all hidden
    zv = o.days[2].meetings[0]
    assert zv.when == "Tuesday, October 13, 10:00 AM" and zv.groups[0].label == "Single-site rezonings"


def test_item_view_carries_filter_data_and_disclosure():
    h = meeting("elms:H", HRE, dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI))
    from digest.models import Attachment, CategoryHit
    sale = item("Sale of City-owned property at 1 W Alpha St to Alpha LLC", id="h#1",
                geography=Geography(address="1 W Alpha St", ward=4), figures=Figures(units=12),
                categories=[CategoryHit(id="ahpaa", via="body"), CategoryHit(id="cha", via="model")],
                summary="Sale of land. The agenda gives no further detail.", entities=["Alpha LLC"])
    sale.attachments = [Attachment(label="Bad", url="javascript:x"), Attachment(label="Ordinance", url="https://x/o.pdf")]
    v = build_outline([(h, [sale], 0)], [], L, {HRE: "ics/h.ics"}).days[0].meetings[0].groups[0].cards[0]
    assert v.anchor == "h-1" and v.categories == "ahpaa cha" and v.ward == "4"
    assert v.headline == "1 W Alpha St" and v.full_title == sale.title             # title kept under the disclosure
    assert v.summary == "Sale of land." and [a.label for a in v.docs] == ["Ordinance"]
    assert v.facts_text == "Ward 4 · 12 homes · Alpha LLC"


def test_unenriched_item_in_a_grouped_body_is_a_row_with_no_facts():
    h = meeting("elms:H", HRE, dt.datetime(2026, 10, 15, 10, 0, tzinfo=CHI))
    raw = item("Sale of City-owned property at 5 W Delta St", id="h#9")
    v = build_outline([(h, [raw], 0)], [], L, {HRE: "x"}).days[0].meetings[0].groups[0].rows[0]
    assert v.headline == "5 W Delta St" and v.facts == [] and v.summary == "" and v.ward == "" and v.categories == ""


def test_no_upcoming_meetings():
    z = meeting("elms:Z", ZON, dt.datetime(2026, 10, 1, 9, 0, tzinfo=CHI))
    o = build_outline([], [(z, [], 0)], L, {ZON: "x"})
    assert not o.has_upcoming and [d.id for d in o.days] == ["recent"]
    unknown_time = meeting("cpc:2", "Chicago Plan Commission", dt.datetime(2026, 10, 20, 0, 0, tzinfo=CHI))
    unknown_time.time_known = False
    assert build_outline([(unknown_time, [], 0)], [], L, {"Chicago Plan Commission": "x"}).days[0].meetings[0].when == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest tests/test_layout.py -q`
Expected: ImportError for `build_outline`.

- [ ] **Step 3: Implement**

In `ingest/digest/layout.py`, change the imports to:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .enrich import title_address
from .models import CHI, Attachment, Item, Meeting
from .sources.base import slugify
from .text import safe_url, short_title, trim_summary
```

and append to the end of the file:

```python
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
    head = headline(layout, item, group)
    return ItemView(
        item=item, anchor=slugify(item.id, 120), group=group, headline=head,
        full_title=item.title if head != item.title else None, facts=facts(item, head),
        card=is_card(layout, group, e.figures.units if e else None, e.amount if e else None), summary=summary,
        docs=[a for a in item.attachments[:3] if safe_url(a.url)],
        categories=" ".join(c.id for c in e.categories) if e else "",
        ward=str(e.geography.ward) if e and e.geography.ward else "",
    )


def _when(m: Meeting, with_date: bool) -> str:
    start = m.start.astimezone(CHI)
    time = f"{start:%I:%M %p}".lstrip("0") if m.time_known else ""
    if not with_date:
        return time
    date = f"{start:%A, %B} {start.day}"
    return f"{date}, {time}" if time else date


def meeting_view(layout: Layout, shown: Shown, ics: dict[str, str], with_date: bool) -> MeetingView:
    m, items, hidden = shown
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
                       count=len(views), hidden=hidden, ics=ics.get(m.body, ""))


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
            index.append(IndexEntry(mv.anchor, f"{start:%a %b} {start.day}", mv.family, mv.meeting.body, mv.count))
    return Outline(index=index, days=days, has_upcoming=bool(by_day))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ingest/digest/layout.py ingest/tests/test_layout.py
git commit -m "Build the page outline: days, index, grouped and flat meetings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Render the outline: bands, index, side panel, cards and tables

**Files:**
- Modify: `ingest/digest/site.py` (`build_site`, `main`)
- Modify: `ingest/digest/templates/index.html.j2` (rewrite)
- Test: `ingest/tests/test_site.py`

**Interfaces:**
- Consumes: `Layout`, `DEFAULT_PATH`, `build_outline`, `Outline` and the view classes from Task 4.
- Produces: `build_site(digest, out_dir, items_json, categories, now, layout: Layout | None = None) -> None` (the new parameter defaults to `Layout.load(DEFAULT_PATH)`). Page markup that tests and the later email rely on:
  - day headings `<h2 id="day-YYYY-MM-DD">Thursday, October 15</h2>` and `<h2 id="recent">Recent</h2>`
  - index `<nav class="toc" aria-labelledby="toc-h">` and panel `<nav id="panel" aria-label="Meetings">`, each a list of `<li data-target="ANCHOR"><a href="#ANCHOR">…<span class="n">N items</span></a></li>`
  - meeting `<section class="meeting fam-ID" id="ANCHOR" data-body="BODY">` (`fam-none` when no family) with `<h3><span class="fam">LABEL</span> BODY</h3>`
  - group `<div class="group"><h4>LABEL <span class="n">· N</span></h4>`, then `<ul class="items">` of cards, then `<table class="rows">`
  - every card `<li class="row card" …>` and table row `<tr class="row" …>` carries `id`, `data-categories`, `data-ward`

- [ ] **Step 1: Update and add the rendering tests**

In `ingest/tests/test_site.py`:

Change the import line `from digest.models import (...)` to also import `Figures`:

```python
from digest.models import (CHI, CategoryHit, Digest, Enrichment, Figures, Geography, Item, Meeting, Notice, Outcome,
                           dump_digest)
```

and add `import re` at the top.

Add these helpers after `sample()`:

```python
HRE = "City Council Committee on Housing and Real Estate"


def render(d, tmp_path):
    src = tmp_path / "items.json"
    dump_digest(d, src)
    build_site(d, tmp_path / "site", src, CATS, NOW)
    return (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


def council_digest():
    d = sample()
    h = Meeting(id="elms:H1", source_id="elms-housing", body=HRE, body_id="h",
                start=dt.datetime(2026, 10, 8, 10, 30, tzinfo=CHI))

    def sale(n, addr, units, buyer):
        return Item(id=f"elms:H1#{n}", meeting_id=h.id, source_ref=f"O2026-{n}",
                    title=f"Sale of City-owned property at {addr} to {buyer}",
                    enrichment=Enrichment(status="ok", housing_relevant=True, summary=f"Sale of land for {units} homes.",
                                          categories=[CategoryHit(id="ahpaa", via="body")],
                                          geography=Geography(address=addr, ward=27), figures=Figures(units=units),
                                          amount=315315, amount_kind="sale price", entities=[buyer]))
    d.meetings.append(h)
    d.items += [sale(1, "2054 W Warren Blvd", 20, "EGP Warren Apartments LLC"), sale(2, "6 S Elm St", 2, "<b>Evil</b> LLC")]
    return d
```

In `test_meetings_from_the_last_two_weeks_show_under_recent_with_their_items`, replace the two `split` lines with:

```python
    upcoming, recent = html.split('<h2 id="recent">Recent</h2>')
    recent = recent.split("<h2>Open notices</h2>")[0]
```

Replace the whole of `test_meeting_heading_leads_with_the_date_and_documents_are_separated` with:

```python
def test_meeting_band_names_family_and_body_and_documents_are_separated(tmp_path):
    from digest.models import Attachment
    d = sample()
    d.items[0].attachments = [Attachment(label="Plan", url="https://x/p.pdf"), Attachment(label="Report", url="https://x/r.pdf")]
    html = render(d, tmp_path)
    sec = html.split('id="cpc-2026-10-15"')[1].split("</section>")[0]
    assert '<h3><span class="fam">Planning</span> Chicago Plan Commission</h3>' in sec
    assert "10:00 AM · " in sec and "1 housing item · " in sec
    assert 'Documents: <a href="https://x/p.pdf">Plan</a> · <a href="https://x/r.pdf">Report</a>' in html
    assert "<main>" in html and 'role="status"' in html and "Updated October 5, 2026, 12:00 PM" in html
    assert '<h2 id="day-2026-10-15">Thursday, October 15</h2>' in html
```

Append these tests:

```python
def test_grouped_body_renders_group_heading_cards_and_a_table(tmp_path):
    html = render(council_digest(), tmp_path)
    sec = html.split('id="elms-h1"')[1].split("</section>")[0]
    assert 'class="meeting fam-council"' in html and '<span class="fam">City Council</span>' in sec
    assert '<h4>Sales of city land <span class="n">· 2</span></h4>' in sec
    cards, table = sec.split('<table class="rows">')
    assert "<strong>2054 W Warren Blvd</strong>" in cards and "6 S Elm St" not in cards
    assert "<b>20</b> homes" in cards and "<b>$315,315</b> sale price" in cards and "EGP Warren Apartments LLC" in cards
    assert '<th scope="col">Item</th><th scope="col">Facts</th>' in table
    assert "<summary><strong>6 S Elm St</strong></summary>" in table
    assert '<td class="facts">Ward 27 · 2 homes · $315,315 sale price · &lt;b&gt;Evil&lt;/b&gt; LLC</td>' in table
    assert "<b>Evil</b>" not in html                                   # model text is escaped
    assert 'id="elms-h1-2"' in table and 'data-categories="ahpaa"' in table and 'data-ward="27"' in table


def test_flat_body_renders_only_cards(tmp_path):
    sec = render(council_digest(), tmp_path).split('id="cpc-2026-10-15"')[1].split("</section>")[0]
    assert '<li id="cpc-2026-10-15-kinzie" class="row card"' in sec and "<table" not in sec and "<h4>" not in sec


def test_index_and_panel_list_every_shown_meeting_in_page_order(tmp_path):
    html = render(council_digest(), tmp_path)
    toc = html.split('<nav class="toc"')[1].split("</nav>")[0]
    panel = html.split('<nav id="panel" aria-label="Meetings"')[1].split("</nav>")[0]
    order = ["elms-h1", "cpc-2026-10-15", "cdc-2026-11-10", "elms-z1"]
    for nav in (toc, panel):
        assert re.findall(r'href="#([^"]+)"', nav) == order
        assert re.findall(r'data-target="([^"]+)"', nav) == order
    assert '<span class="n">2 items</span>' in toc and '<span class="n">1 item</span>' in toc
    assert '<span class="n">0 items</span>' in toc                         # the cancelled CDC meeting
    assert html.index('<h2 id="day-2026-10-08">') < html.index('<h2 id="day-2026-10-15">') \
        < html.index('<h2 id="day-2026-11-10">') < html.index('<h2 id="recent">Recent</h2>')
    assert html.index('<nav class="toc"') < html.index('<h2 id="day-2026-10-08">')


def test_no_upcoming_meetings_says_so(tmp_path):
    d = sample()
    d.meetings = [m for m in d.meetings if m.id == "elms:Z1"]
    html = render(d, tmp_path)
    assert "No upcoming meetings found." in html and '<h2 id="recent">Recent</h2>' in html
```

- [ ] **Step 2: Run tests to verify the new and changed ones fail**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest tests/test_site.py -q`
Expected: the five new or rewritten tests fail; the others pass.

- [ ] **Step 3: Wire the outline into `build_site`**

In `ingest/digest/site.py`, add the import:

```python
from .layout import DEFAULT_PATH as LAYOUT_PATH, Layout, build_outline
```

Change the signature of `build_site` to:

```python
def build_site(digest: Digest, out_dir: Path, items_json: Path, categories: Categories, now: dt.datetime,
               layout: Layout | None = None) -> None:
```

Replace the meeting loop and the render call so the body of `build_site` up to `(out_dir / "ics").mkdir(...)` reads:

```python
    layout = layout or Layout.load(LAYOUT_PATH)
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
        (upcoming if day >= today else recent).append((m, visible, len(its) - len(visible)))
    notices = [n for n in digest.notices
               if (n.deadline is None or n.deadline >= now)
               and is_shown(n)]
    outcomes = sorted(
        ({"i": i, "m": meetings_by_id.get(i.meeting_id)} for i in digest.items
         if i.outcome and i.outcome.date >= cutoff and is_shown(i)),
        key=lambda r: r["i"].outcome.date, reverse=True,
    )
    bodies = sorted({m.body for m in digest.meetings})
    ics = {b: f"ics/{slugify(b)}.ics" for b in bodies}

    env = Environment(loader=PackageLoader("digest", "templates"), autoescape=select_autoescape(["html", "j2"]))
    env.filters["safe_url"] = safe_url
    env.filters["short_title"] = short_title
    env.tests["safe_url"] = lambda u: safe_url(u) is not None
    env.globals["LONG_TITLE"] = LONG_TITLE
    html = env.get_template("index.html.j2").render(
        digest=digest, outline=build_outline(upcoming, recent, layout, ics), notices=notices, outcomes=outcomes,
        bodies=bodies, categories=categories.items, generated=digest.generated_at.astimezone(CHI),
    )
```

(The `anchor` and `trim_summary` filters and `cat_labels` are no longer used by the template; the outline carries anchors and trimmed summaries.)

In `main`, change the `build_site(...)` call to:

```python
    build_site(digest, out, src, Categories(args.root / "config" / "categories.yaml"), dt.datetime.now(CHI),
               Layout.load(args.root / "config" / "layout.yaml"))
```

- [ ] **Step 4: Rewrite the template**

Replace the whole of `ingest/digest/templates/index.html.j2` with:

```jinja
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chicago Housing Meetings</title>
<style>
  :root { --ink: #1f2224; --muted: #535659; --line: #dbdee2; --bg: #fff; --soft: #f7fafe; --chip: #ebeff2; --warn: #a50e0e; }
  @media (prefers-color-scheme: dark) { :root { --ink: #e8eaed; --muted: #9aa0a6; --line: #3c4043; --bg: #202124; --soft: #262a30; --chip: #2b3440; --warn: #f28b82; } }
  .meeting { --tint: var(--soft); --rule: var(--line); --fink: var(--ink); }
  .fam-council { --tint: #e7f5ff; --rule: #3a84ca; --fink: #0e4f86; }
  .fam-ihda { --tint: #e5f9ef; --rule: #00986c; --fink: #005f3e; }
  .fam-clihtf { --tint: #fff0e2; --rule: #b46d10; --fink: #753d00; }
  .fam-planning { --tint: #f7f0ff; --rule: #926abe; --fink: #5b3b7c; }
  @media (prefers-color-scheme: dark) {
    .fam-council { --tint: #1e2f41; --rule: #67aaed; --fink: #a6d2ff; }
    .fam-ihda { --tint: #193429; --rule: #4dbc92; --fink: #9cdfc1; }
    .fam-clihtf { --tint: #3c2a18; --rule: #d8944d; --fink: #f3c497; }
    .fam-planning { --tint: #32293e; --rule: #b691e1; --fink: #dac1f8; }
  }
  body { margin: 0; padding: 16px; font: 16px/1.5 system-ui, sans-serif; color: var(--ink); background: var(--bg); }
  .page { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin-bottom: 0; }
  h2 { margin: 2.5rem 0 1rem; font-size: 1.3rem; padding-bottom: .2rem; border-bottom: 2px solid var(--ink); }
  .meta, .hidden-count, .src, .n { color: var(--muted); font-size: .9rem; }
  .n { font-variant-numeric: tabular-nums; }
  .cancelled { color: var(--warn); font-weight: 600; }
  a { color: inherit; }
  .toc { border: 1px solid var(--line); border-radius: 6px; padding: .5rem 1rem; margin: 1.5rem 0; }
  .toc h2 { margin: .25rem 0 .5rem; font-size: 1.05rem; border: 0; padding: 0; }
  .toc ul, #panel ul { list-style: none; margin: 0; padding: 0; }
  .toc li { padding: .2rem 0; border-top: 1px dotted var(--line); } .toc li:first-child { border-top: 0; }
  .toc a, #panel a { text-decoration: none; display: block; } .toc a:hover .b, #panel a:hover .b { text-decoration: underline; }
  .when { display: inline-block; min-width: 5.5rem; color: var(--muted); font-size: .9rem; }
  .tag { font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--fink);
         border-left: 4px solid var(--rule); padding-left: .3rem; margin-right: .25rem; }
  #panel { display: none; }
  @media (min-width: 70rem) {
    .page { max-width: 78rem; display: grid; grid-template-columns: 15rem minmax(0, 1fr); gap: 2.5rem; }
    main { max-width: 860px; }
    #panel { display: block; position: sticky; top: 1rem; align-self: start; max-height: calc(100vh - 2rem);
             overflow-y: auto; background: var(--soft); border: 1px solid var(--line); border-radius: 6px;
             padding: .75rem; font-size: .9rem; }
    #panel .panel-h { font-weight: 700; margin: 0 0 .4rem; }
    #panel li { padding: .15rem .3rem; border-radius: 4px; }
    #panel .when { display: block; min-width: 0; font-size: .8rem; }
    #panel a[aria-current] { font-weight: 650; } #panel li:has(a[aria-current]) { background: var(--chip); }
  }
  .meeting { margin: 0 0 2rem; border-left: 4px solid var(--rule); }
  .band { background: var(--tint); padding: .6rem .9rem; border-radius: 0 6px 0 0; }
  .band h3 { margin: 0; font-size: 1.1rem; text-wrap: balance; }
  .fam { display: inline-block; font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
         color: var(--fink); background: var(--bg); border-radius: 3px; padding: 0 .4rem; margin-right: .3rem; vertical-align: .15rem; }
  .mbody { padding: .25rem .9rem .5rem 1rem; }
  .group h4 { margin: 1.25rem 0 .25rem; font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; color: var(--fink); }
  .group h4 .n { font-weight: 400; font-size: .8rem; }
  ul.items { list-style: none; padding: 0; margin: 0; }
  ul.items > li { padding: .75rem 0; border-bottom: 1px solid var(--line); max-width: 70ch; }
  ul.items > li:last-child { border-bottom: 0; }
  ul.items > li > * { margin: .2rem 0 0; }
  .chip { display: inline-block; background: var(--chip); border-radius: 3px; padding: 0 .45rem; margin: .1rem .25rem .1rem 0; font-size: .85rem; }
  .chip b { font-weight: 650; }
  .ai { font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  details.full, div.full { font-size: .9rem; color: var(--muted); } details summary { cursor: pointer; }
  .docs { font-size: .9rem; overflow-wrap: break-word; }
  table.rows { width: 100%; border-collapse: collapse; margin: .5rem 0; font-size: .95rem; }
  table.rows th { text-align: left; font-size: .8rem; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); padding: .2rem .5rem .2rem 0; }
  table.rows td { vertical-align: top; border-bottom: 1px solid var(--line); padding: .4rem .5rem .4rem 0; overflow-wrap: anywhere; }
  table.rows td.facts { font-size: .85rem; color: var(--muted); width: 45%; }
  table.rows details > :not(summary) { margin: .3rem 0 0; }
  .filters { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; margin: 1rem 0 .25rem; }
  select { font: inherit; max-width: 100%; } button { font: inherit; }
  #f-status:empty { display: none; }
  .plain { list-style: none; padding: 0; } .plain > li { margin: 1rem 0; max-width: 70ch; }
</style>
</head>
<body>
{% macro meeting_links() -%}
<ul>{% for x in outline.index %}
  <li data-target="{{ x.anchor }}"><a href="#{{ x.anchor }}" class="{{ 'fam-' ~ x.family.id if x.family else 'fam-none' }}"><span class="when">{{ x.date_label }}</span> {% if x.family %}<span class="tag">{{ x.family.label }}</span> {% endif %}<span class="b">{{ x.body }}</span> <span class="n">{{ x.count }} item{{ 's' if x.count != 1 else '' }}</span></a></li>{% endfor %}
</ul>
{%- endmacro %}

{% macro chips(i) -%}
{% for f in i.facts %}<span class="chip">{% for s in f %}{% if s.bold %}<b>{{ s.text }}</b>{% else %}{{ s.text }}{% endif %}{% endfor %}</span>{% endfor %}
{%- endmacro %}

{% macro details_body(i) -%}
  {% if i.summary %}<div><span class="ai">AI summary</span> {{ i.summary }}</div>{% endif %}
  {% if i.docs %}<div class="docs">Documents: {% for a in i.docs %}<a href="{{ a.url | safe_url }}">{{ a.label }}</a>{% if not loop.last %} · {% endif %}{% endfor %}</div>{% endif %}
{%- endmacro %}

{% macro card(i) -%}
<li id="{{ i.anchor }}" class="row card" data-categories="{{ i.categories }}" data-ward="{{ i.ward }}">
  <strong>{{ i.headline }}</strong> <span class="src">{{ i.item.source_ref }}</span>
  {% if i.full_title %}<details class="full"><summary>Full agenda text</summary>{{ i.full_title }}</details>{% endif %}
  {% if i.facts %}<div class="chips">{{ chips(i) }}</div>{% endif %}
  {{ details_body(i) }}
</li>
{%- endmacro %}

{% macro table_row(i) -%}
<tr id="{{ i.anchor }}" class="row" data-categories="{{ i.categories }}" data-ward="{{ i.ward }}">
  <td><details><summary><strong>{{ i.headline }}</strong></summary>
    <div class="src">{{ i.item.source_ref }}</div>
    {% if i.full_title %}<div class="full">{{ i.full_title }}</div>{% endif %}
    {{ details_body(i) }}</details></td>
  <td class="facts">{{ i.facts_text }}</td>
</tr>
{%- endmacro %}

{% macro meeting_section(v) -%}{% set m = v.meeting %}
<section class="meeting {{ 'fam-' ~ v.family.id if v.family else 'fam-none' }}" id="{{ v.anchor }}" data-body="{{ m.body }}">
  <div class="band">
    <h3>{% if v.family %}<span class="fam">{{ v.family.label }}</span> {% endif %}{{ m.body }}</h3>
    <div class="meta">
      {% if v.when %}{{ v.when }} · {% endif %}
      {% if m.status == 'cancelled' %}<span class="cancelled">Cancelled</span> · {% endif %}
      {{ v.count }} housing item{{ 's' if v.count != 1 else '' }} ·
      {% if m.location %}{{ m.location }} · {% endif %}
      {% if m.agenda_url | safe_url %}<a href="{{ m.agenda_url | safe_url }}">Agenda</a> · {% endif %}
      <a href="{{ v.ics }}">Add to calendar</a>
    </div>
  </div>
  <div class="mbody">
  {% if v.groups is not none %}
    {% for g in v.groups %}
    <div class="group">
      <h4>{{ g.label }} <span class="n">· {{ g.count }}</span></h4>
      {% if g.cards %}<ul class="items">{% for i in g.cards %}{{ card(i) }}{% endfor %}</ul>{% endif %}
      {% if g.rows %}<table class="rows"><thead><tr><th scope="col">Item</th><th scope="col">Facts</th></tr></thead>
        <tbody>{% for i in g.rows %}{{ table_row(i) }}{% endfor %}</tbody></table>{% endif %}
    </div>
    {% endfor %}
  {% elif v.cards %}<ul class="items">{% for i in v.cards %}{{ card(i) }}{% endfor %}</ul>{% endif %}
  {% if v.hidden %}<div class="hidden-count">+{{ v.hidden }} item{{ 's' if v.hidden != 1 else '' }} not about housing</div>{% endif %}
  </div>
</section>
{%- endmacro %}

<div class="page">
<nav id="panel" aria-label="Meetings"><p class="panel-h">This week</p>{{ meeting_links() }}</nav>
<main>
<h1>Chicago Housing Meetings</h1>
<p class="meta">Updated {{ generated.strftime('%B') }} {{ generated.day }}, {{ generated.year }}, {{ generated.strftime('%I:%M %p').lstrip('0') }} Chicago time. Summaries are written by AI from public documents; check the source before relying on them.</p>

<div class="filters">
  <select id="f-cat" aria-label="Category"><option value="">All categories</option>
    {% for c in categories %}<option value="{{ c.id }}">{{ c.label }}</option>{% endfor %}</select>
  <select id="f-body" aria-label="Body"><option value="">All bodies</option>
    {% for b in bodies %}<option value="{{ b }}">{{ b }}</option>{% endfor %}</select>
  <select id="f-ward" aria-label="Ward"><option value="">All wards</option>
    {% for w in range(1, 51) %}<option value="{{ w }}">Ward {{ w }}</option>{% endfor %}</select>
  <button type="button" id="f-clear" hidden>Clear filters</button>
</div>
<p id="f-status" class="meta" role="status"></p>

{% if outline.index %}<nav class="toc" aria-labelledby="toc-h"><h2 id="toc-h">This week</h2>{{ meeting_links() }}</nav>{% endif %}

{% if not outline.has_upcoming %}<p>No upcoming meetings found.</p>{% endif %}
{% for d in outline.days %}
<h2 id="{{ d.id }}">{{ d.label }}</h2>
{% for v in d.meetings %}{{ meeting_section(v) }}
{% endfor %}
{% endfor %}

<h2>Open notices</h2>
<ul class="plain">
{% for n in notices %}
  <li class="row" data-body="{{ n.body }}"
      data-categories="{{ (n.enrichment.categories | map(attribute='id') | join(' ')) if n.enrichment else '' }}" data-ward="">
    <strong>{% if n.url | safe_url %}<a href="{{ n.url | safe_url }}">{{ n.title }}</a>{% else %}{{ n.title }}{% endif %}</strong>
    <span class="src">{{ n.body }}{% if n.deadline %} · deadline {{ n.deadline.strftime('%B %d, %Y') }}{% endif %}</span>
    {% if n.enrichment and n.enrichment.summary %}<div><span class="ai">AI summary</span> {{ n.enrichment.summary }}</div>{% endif %}
  </li>
{% else %}<li>No open notices.</li>{% endfor %}
</ul>

<h2>Recent outcomes</h2>
<ul class="plain">
{% for r in outcomes %}
  <li class="row" data-body="{{ r.m.body if r.m else '' }}"
      data-categories="{{ (r.i.enrichment.categories | map(attribute='id') | join(' ')) if r.i.enrichment else '' }}"
      data-ward="{{ r.i.enrichment.geography.ward if r.i.enrichment and r.i.enrichment.geography.ward else '' }}">
    <strong>{{ r.i.outcome.action }}</strong> ({{ r.i.outcome.date.strftime('%B %d') }}{% if r.m %}, {{ r.m.body }}{% endif %}): {{ r.i.title | short_title }}
    {% if r.i.title | length > LONG_TITLE %}<details class="full"><summary>Full agenda text</summary>{{ r.i.title }}</details>{% endif %}
  </li>
{% else %}<li>No outcomes in the last two weeks.</li>{% endfor %}
</ul>

<p class="meta">Data: <a href="items.json">items.json</a></p>
</main>
</div>
<script>
  const sel = id => document.getElementById(id);
  const anyShown = el => [...el.querySelectorAll('.row')].some(r => !r.hidden);
  function apply() {
    const cat = sel('f-cat').value, body = sel('f-body').value, ward = sel('f-ward').value;
    const filtering = cat || body || ward;
    document.querySelectorAll('.row').forEach(row => {
      const section = row.closest('.meeting');
      const rowBody = row.dataset.body || (section && section.dataset.body) || '';
      const ok = (!cat || (row.dataset.categories || '').split(' ').includes(cat))
        && (!body || rowBody === body) && (!ward || row.dataset.ward === ward);
      row.hidden = !ok;
    });
    document.querySelectorAll('table.rows, .group').forEach(el => { el.hidden = filtering ? !anyShown(el) : false; });
    document.querySelectorAll('.meeting').forEach(s => {
      s.hidden = filtering ? !(anyShown(s) || (body && s.dataset.body === body && !cat && !ward)) : false;
    });
    document.querySelectorAll('[data-target]').forEach(li => { const s = sel(li.dataset.target); li.hidden = !s || s.hidden; });
    const shown = [...document.querySelectorAll('.row')].filter(r => !r.hidden && !(r.closest('.meeting') || {}).hidden).length;
    sel('f-clear').hidden = !filtering;
    sel('f-status').textContent = !filtering ? ''
      : shown ? `${shown} item${shown === 1 ? '' : 's'} match these filters.`
      : 'No items match these filters. Try another category, body, or ward, or clear filters.';
  }
  ['f-cat', 'f-body', 'f-ward'].forEach(id => sel(id).addEventListener('change', apply));
  sel('f-clear').addEventListener('click', () => {
    ['f-cat', 'f-body', 'f-ward'].forEach(id => { sel(id).value = ''; });
    apply();
    sel('f-cat').focus();
  });
  // Mark the meeting in view in the side panel. Without this script the panel is still a list of working links.
  const links = new Map([...document.querySelectorAll('#panel [data-target]')].map(li => [li.dataset.target, li.querySelector('a')]));
  if ('IntersectionObserver' in window && links.size) {
    const io = new IntersectionObserver(entries => entries.forEach(en => {
      if (!en.isIntersecting) return;
      links.forEach(a => a.removeAttribute('aria-current'));
      const a = links.get(en.target.id);
      if (a) a.setAttribute('aria-current', 'location');
    }), { rootMargin: '0px 0px -70% 0px' });
    document.querySelectorAll('.meeting').forEach(s => io.observe(s));
  }
</script>
</body>
</html>
```

- [ ] **Step 5: Run the full suite**

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest -q`
Expected: all pass. If a whitespace-sensitive assertion fails (for example `<b>20</b> homes`), fix the template's whitespace, not the test, unless the test asserts something the spec does not require.

- [ ] **Step 6: Build the real page and look for errors**

Run: `cd ingest && ../.venv/Scripts/python.exe -m digest.site`
Expected: `wrote ...site`. Then:

Run: `cd .. && grep -c 'class="meeting fam-' site/index.html && grep -c '<table class="rows">' site/index.html`
Expected: both counts above 0 (real data has City Council meetings with land sales or rezonings).

- [ ] **Step 7: Commit**

```bash
git add ingest/digest/site.py ingest/digest/templates/index.html.j2 ingest/tests/test_site.py
git commit -m "Render the scannable page: day headings, banded meetings, index, side panel, tables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Visual check and interface review

**Files:**
- Modify (only if the review finds problems): `ingest/digest/templates/index.html.j2`, `ingest/digest/layout.py`, `config/layout.yaml`, with tests for any logic change.

**Interfaces:**
- Consumes: the built `site/index.html` from Task 5.
- Produces: screenshots in the scratchpad and, if needed, a fix commit.

- [ ] **Step 1: Build and take screenshots**

Run from the repo root (Chrome v117 needs plain `--headless`; `--force-dark-mode` makes `prefers-color-scheme: dark` match):

```bash
cd ingest && ../.venv/Scripts/python.exe -m digest.site && cd ..
CHROME="/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"
PAGE="file:///$(pwd -W | sed 's#\\#/#g')/site/index.html"
OUT="$TEMP/claude/C--Users-bpi/0952c58d-1f6c-4878-9c8c-b53028d0672f/scratchpad"
"$CHROME" --headless --disable-gpu --hide-scrollbars --screenshot="$OUT/page-1280-light.png" --window-size=1280,3000 "$PAGE"
"$CHROME" --headless --disable-gpu --hide-scrollbars --screenshot="$OUT/page-320-light.png" --window-size=320,4000 "$PAGE"
"$CHROME" --headless --disable-gpu --hide-scrollbars --force-dark-mode --screenshot="$OUT/page-1280-dark.png" --window-size=1280,3000 "$PAGE"
"$CHROME" --headless --disable-gpu --hide-scrollbars --force-dark-mode --screenshot="$OUT/page-320-dark.png" --window-size=320,4000 "$PAGE"
```

Open each PNG with the Read tool. Check:
- 1280: the side panel shows at the left, the index sits above the first day, each meeting has a tinted band, a colored left rule and a family label.
- 320: no horizontal scroll or clipped text; the table's Facts column wraps; no side panel.
- Dark: bands, labels and rules are readable, with no light tints left on the dark page.

- [ ] **Step 2: Run the interface review**

Invoke the `interfaces:better-interface` skill on `ingest/digest/templates/index.html.j2` as rendered in `site/index.html` (surface audit, not a change review). Any reviewer subagent must be read-only: no worktree, no file writes. Pass the model explicitly (Sonnet).

- [ ] **Step 3: Fix HIGH and MEDIUM findings**

For each HIGH or MEDIUM finding that is in scope for this spec, fix it in the template or layout, add or adjust a test when logic changes, and rerun:

Run: `cd ingest && ../.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

Retake the affected screenshots and check them again.

- [ ] **Step 4: Commit (only if anything changed)**

```bash
git add ingest/digest/templates/index.html.j2 ingest/digest/layout.py config/layout.yaml ingest/tests
git commit -m "Fix interface review findings on the scannable page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After this plan (not part of it)

- Ask Daniel before bumping `PROMPT_VERSION` to `"2"`. That re-summarizes every item once (about $3–5) so existing items gain amounts, zoning changes and corrected unit totals. It is a one-line commit, and then the push.
- Merging and pushing to `master` happens only when Daniel chooses it.
