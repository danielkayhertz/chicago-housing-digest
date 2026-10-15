# Scannable meeting page: design

Date: 2026-10-14. Status: approved in conversation, awaiting written-spec review.

## Purpose

Readers open the page to skim everything happening this week across all bodies. Success: a reader can take in a week in a minute or two, seeing what each body is doing and which items are big, and read summaries only for items that catch their eye. Today the page is hard to skim because nothing visually marks structure, and the City Council Committee on Zoning alone puts 30+ near-identical rezonings in a row.

This page is also the template for the email digest (Plan 2). Everything here except the side panel and the expandable table rows must have an email equivalent.

## Decisions

| Decision | Choice |
| --- | --- |
| Item layout | Hybrid: big items as cards, routine items of the same type as a compact table (mockup "C") |
| Color | "Banded": one color per family of bodies, on a tinted header band and a left rule down the whole meeting |
| Navigation | A "This week" index at the top for everyone, plus a sticky side panel on wide screens |
| Grouping | Only for bodies with many formulaic items: the three City Council committees. Everyone else is a flat list in agenda order |
| New facts | Dollar amounts and zoning changes come from new fields in the existing AI summary call, grounded against source text |
| "Big" rule | 10+ homes, or $1M+, or the group is Planned developments, Zoning code changes, or Other. Applies in every group, land sales included |

## Page structure

Top to bottom:

1. Title and "Updated" line (unchanged).
2. Filters (category, body, ward) with the status line and Clear filters button (unchanged).
3. **"This week" index**: one line per meeting shown on the page, in page order: date, colored family label, body name, housing item count. Each line links to its meeting's anchor.
4. **Days**: each upcoming day gets a day heading (large, heavy rule beneath). Upcoming meetings first, in date order. Then a single "Recent" day heading containing meetings from the last 14 days, newest first.
5. Open notices, then Recent outcomes (unchanged apart from color tokens).

**Side panel**: at viewport widths of 70rem (about 1100px) and above, the same list as the index renders in a `<nav aria-label="Meetings">` that stays in view (`position: sticky`) in a left column. Below 70rem it is not rendered visibly and the index serves alone. A small script marks the meeting currently in view with `aria-current="location"` and a highlight; without the script the panel is still a working list of links.

## Meetings: banded color

Each meeting is a section with:

- A **header band**: the family label (text, in the family's ink color on white), the body name, the time, "N housing items", Agenda link, Add to calendar link. Band background is the family tint.
- A **left rule** (4px, family rule color) down the band and the whole meeting body.
- The existing "+N items not about housing" line at the end.

Family is derived from the body. Families and tokens (oklch-generated, measured):

| Family | Bodies | Light tint / rule / ink | Dark tint / rule / ink |
| --- | --- | --- | --- |
| council | City Council committees (Zoning, Housing and Real Estate, Finance) | `#e7f5ff` / `#3a84ca` / `#0e4f86` | `#1e2f41` / `#67aaed` / `#a6d2ff` |
| ihda | IHDA Board, IHDA Finance Committee | `#e5f9ef` / `#00986c` / `#005f3e` | `#193429` / `#4dbc92` / `#9cdfc1` |
| clihtf | CLIHTF Board of Directors | `#fff0e2` / `#b46d10` / `#753d00` | `#3c2a18` / `#d8944d` / `#f3c497` |
| planning | Chicago Plan Commission, Community Development Commission | `#f7f0ff` / `#926abe` / `#5b3b7c` | `#32293e` / `#b691e1` / `#dac1f8` |

Measured contrast: ink on tint at least 7.07:1 (light) and 8.52:1 (dark); rule against page background at least 3.68:1 (light) and 6.23:1 (dark). The family is always named in text next to its color; color is never the only signal. A body with no family mapping uses neutral tokens.

The family mapping lives in config (see Configuration).

## Items

### Grouped bodies

For bodies listed in the grouping config, items are grouped by title rules into type groups, in the order the groups are listed in config, with "Other" last. Each group has a heading in the family ink color: label plus count ("Sales of city land · 8").

Within a group:

- **Cards** for big items: `units >= 10`, or `amount >= 1_000_000`, or the group is one of the always-card groups (Planned developments, Zoning code changes, Other). Cards first, in agenda order.
- **Table** for the rest, in agenda order, two columns: Item (headline) and Facts (the fact chips' text joined by " · "). Each row's headline is a native `<details>`/`<summary>`; opening it shows the AI summary and the documents line.

Group rules for the three City Council committees (title prefixes, first match wins):

| Body | Group | Rule |
| --- | --- | --- |
| Housing and Real Estate | Sales of city land | title starts "Sale of City-owned" |
| Housing and Real Estate | CHA board appointments | title starts "Appointment of" |
| Zoning | Planned developments | title starts "Zoning Reclassification" and title or summary mentions "Planned Development" or the standalone word "PD" |
| Zoning | Single-site rezonings | title starts "Zoning Reclassification" |
| Zoning | Zoning code changes | title starts "Amendment of Municipal Code" or "Designation of" |
| Finance | (no rules yet; everything is Other until Finance agendas show a pattern) | |

Unmatched items go to Other.

### Flat bodies

Every body not in the grouping config: all items as cards, in agenda order.

### Card

1. **Headline**: the address for land sales, rezonings and planned developments (geography address, else title address); the person's name for appointments ("Appointment of X as ..."); and whenever no address or name is found, the existing shortened title, with "Full agenda text" disclosure when the title is over 120 characters. The agenda number (source_ref) stays beside the headline.
2. **Fact chips** (neutral chip background, numbers in bold), each only when known, in this order:
   - Ward N
   - N homes · M affordable
   - Dollar amount with its kind ("$315,315 sale price", "$51.5M conduit loan"). Amounts of $1M or more are shown in millions with one decimal.
   - Zoning change: "RS-2 → RT-4" (several from-districts joined by ", ")
   - Buyer or applicant: the first entity that is not a public body or elected official
3. The AI summary (with its "AI summary" label) and the "Documents:" line, as today.

## Data changes

### New enrichment fields

Added to `Enrichment` (in `models.py`) and to the summary call's schema and prompt:

- `amount: int | None` and `amount_kind: str | None`. The prompt asks for the single dollar figure the item turns on (sale price, loan, bond cap, grant, fee waiver), in whole dollars, and a two-to-four-word kind. Kept only when the number appears in the source text or, for scanned sources, the model's own summary (same grounding as units).
- `zoning_from: list[str]` and `zoning_to: str | None`. District codes as written (e.g. "RS-3", "PD 1143", "Business Planned Development No. 1143"). Kept only when each code appears in the source text.

Existing items have these fields empty until re-summarized.

### Units across several buildings

The prompt's units line says to give the total homes across all buildings. `parse_units` multiplies "N M-unit building(s)" (N as a numeral or a spelled-out number) so "two 8-unit masonry residential buildings" yields 16.

### Re-run

`PROMPT_VERSION` goes from "1" to "2", so the next run re-summarizes every item once. Estimated cost roughly $3–5 (the first full run was $3.89). The code can merge before this; the paid run happens only after Daniel says yes.

### Computed at render time, never stored

Group, family, headline, card-or-row, and fact chip text are all computed in `site.py` when the page is built. Changing a title rule, a threshold, or a color never needs a re-run.

## Shared page outline

One function in `site.py` builds the outline the template renders: index entries, then days, each with meetings, each with either groups (cards plus table rows) or a flat card list, with every item's headline and fact list resolved. The website template renders it now. Plan 2's email renderer will render the same outline: index kept, side panel dropped, table rows linking to the item's anchor on the website instead of expanding.

## Configuration

New `config/layout.yaml`:

- `families`: family id → list of body names (the table above).
- `grouping`: body name → ordered list of `{label, title_prefix, summary_pattern?}` rules; bodies not listed are flat.
- `always_card_groups`: [Planned developments, Zoning code changes, Other].
- `card_min_units: 10`, `card_min_amount: 1000000`.

Color values stay in the template's CSS tokens, not in config.

## Filters

The existing filters keep working on the new structure:

- Table rows carry the same `data-categories` and `data-ward` attributes as cards, and are hidden the same way.
- A group whose cards and rows are all hidden is hidden, and so is its table when all its rows are hidden.
- A meeting with nothing visible is hidden, as today; its lines in the index and side panel are hidden with it.
- Counts (index, band, group headings) show the unfiltered totals; the status line already reports how many items match.

## Accessibility

- Table-row disclosure uses native `<details>`/`<summary>`; no custom keyboard handling.
- Side panel is a labeled `<nav>`; the current meeting gets `aria-current="location"`.
- The index is a list of links before the first day heading.
- Heading outline: h1 page, h2 for each day (and "Open notices", "Recent outcomes"), h3 for each meeting, h4 for each group.
- Every color pair measured (above); family names always in text.
- Works at 320px width without horizontal scroll; the table's Facts column wraps.

## Testing

Unit tests (pytest, `ingest/tests/`):

- Group rules: each rule matches its sample titles; unmatched titles go to Other; flat bodies get no groups.
- Card-or-row: 10 homes is a card, 9 is a row; $1,000,000 is a card; always-card groups are cards regardless of size; the rule applies to land sales.
- Headlines: address for land sale and rezoning; name for appointment; shortened title otherwise.
- Fact formatting: amount display under and over $1M; zoning change with several from-districts; buyer skips public bodies.
- Grounding: an amount or district not in the source text is dropped.
- `parse_units`: "two 8-unit buildings" gives 16; existing cases still pass.
- Rendering: index lists every shown meeting in page order with counts; side panel nav present with `aria-label`; meeting carries its family class and label text; grouped body renders group headings, cards and a table; flat body renders only cards.

Visual check: headless Chrome screenshots at 1280px and 320px, light and dark mode; then rerun the interface review on the result.

## Out of scope

- The email itself (Plan 2).
- Linking IHDA bond hearing (TEFRA) notices to agenda items (declined 2026-10-14).
- Grouping IHDA, CLIHTF, Plan Commission or CDC.
- Running the paid re-summary (separate approval).
