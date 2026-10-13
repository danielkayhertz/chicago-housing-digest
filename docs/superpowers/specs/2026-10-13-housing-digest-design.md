# Chicago Housing Digest — Design

Date: 2026-10-13
Status: design approved in brainstorming; awaiting written-spec review

## 1. Purpose

An email digest that keeps housing advocates current on public meetings, agendas, materials, outcomes, and comment periods at the bodies that make housing decisions in Chicago (and, from v1.5, suburban Chicagoland). Each subscriber chooses what they care about; matching items are highlighted with the reason they matched.

**Audience.** Start with a small known group (IFE housing team plus partners, under ~50 people), invite-only. Build so a public signup can be added later without re-architecting.

**Success looks like.** A subscriber learns about a housing-relevant agenda item or comment period early enough to act on it, understands in one sentence what it is, and can click through to the source document.

**Non-goals.** No news coverage. No legislative (Springfield) bill tracking — that is the separate IFE bill tracker. No free-text AI matching per subscriber.

## 2. Decisions made in brainstorming

| Topic | Decision |
|---|---|
| Format | Email digest, plus a public web page generated from the same data |
| Cadence | Per subscriber: **weekly**, **weekly + alerts** (extra email when a newly posted item matches), or **daily when there is something new** |
| Item content | Every item gets one AI plain-language summary, generated once and shared by all subscribers |
| Preferences | **IFE project categories** (assigned per item by the model, plus deterministic body/keyword rules) + wards/community areas + free keywords; matching is deterministic given the categories |
| Architecture | Hybrid: Python ingest in GitHub Actions → `items.json` contract → Cloudflare Worker + D1 for subscribers, matching, sending |
| Sender | `housingdigest.danielkayhertz.com` via Resend; can move to an IFE domain later (sender is config; needs SPF/DKIM/DMARC on the new domain; old link host keeps redirecting) |
| Source health | Admin-only email to **dhertz@impactforequity.org**; never shown to subscribers |
| Suburbs | v1.5, via per-vendor adapters + page-change watcher; starter jurisdiction list chosen later from the survey (§10) |

## 3. Architecture

```
GitHub Actions (Python, 3×/day)                      Cloudflare (TypeScript)
┌──────────────────────────────────────┐            ┌────────────────────────────────┐
│ adapters/ ──► normalize ──► diff      │            │ Cron (daily + Sunday weekly)   │
│      (one per source)     (events)    │  items.json│   read items.json              │
│            └─► enrich (Claude) ───────┼──────────► │   match per subscriber (D1)    │
│               cache by content hash   │  (Pages)   │   render email ──► Resend      │
│ build site (Pages) + .ics feeds       │            │ Preferences page (magic link)  │
│ admin alert on stale/failed source    │            │ Unsubscribe, bounce handling   │
└──────────────────────────────────────┘            └────────────────────────────────┘
```

One repo:

- `ingest/` — Python: adapters, normalization, diff/events, enrichment, site build.
- `worker/` — TypeScript Cloudflare Worker: D1 schema, preferences page, matching, rendering, sending.
- `config/` — shared YAML: `sources.yaml`, `watch.yaml` (page-change watcher list), `categories.yaml`, `overrides.yaml`.
- `schema/items.schema.json` — the contract. Both halves validate against it in tests.

`items.json` is the only interface between the halves. Either half can be rebuilt or fixed without touching the other.

## 4. Data model (the `items.json` contract)

Top level: `{ generated_at, sources: [SourceStatus], meetings: [Meeting], items: [Item], notices: [Notice], events: [ChangeEvent] }`.

**SourceStatus** — `source_id, last_success_at, status (ok | stale | error), message`. Consumed by the admin alert only.

**Meeting** — `id` (stable: `source_id:native_id`), `body` (canonical name), `body_id`, `jurisdiction` (`chicago` | `state` | suburban slug), `start` (ISO, America/Chicago), `status` (scheduled | cancelled | rescheduled | held | unknown), `location`, `agenda_url`, `video_url`, `minutes_url`, `first_seen_at`, `last_changed_at`, `item_count_total`, `item_count_relevant`.

**Item** — `id`, `meeting_id`, `source_ref` (e.g. `O2026-0029184`, CHA `26-112`), `title` (verbatim), `attachments: [{label, url}]`, `outcome` (`{action, date, tally}` or null), `extraction` (`structured` | `ai_extracted`), `enrichment` (below), `first_seen_at`, `last_changed_at`.

**Notice** — `id`, `source_id`, `kind` (comment_period | hearing | funding_round | report | page_change), `title`, `url`, `posted_at`, `deadline` (nullable), `enrichment`, `first_seen_at`.

**Enrichment** — `housing_relevant` (bool), `relevance_reason`, `summary` (≤ ~40 words, plain language), `categories: [category_id]`, `geography: {address, ward, community_area}`, `entities: [string]`, `figures: {units, affordable_units}` (each nullable), `source_quality` (full | partial | scanned), `model`, `content_hash`, `overridden` (bool).

**ChangeEvent** — `{type: meeting_new | agenda_posted | item_added | meeting_cancelled | meeting_rescheduled | outcome_recorded | notice_new, ref_id, at}`. Drives alerts and daily mode.

Items with `housing_relevant = false` stay in `items.json` (so counts are honest) but are excluded from emails and the default web view.

## 5. Ingest (v1 sources)

All endpoints below were verified by live fetch on 2026-10-13 unless noted.

| Source | Adapter approach |
|---|---|
| **City Council — Committee on Zoning, Landmarks and Building Standards; Committee on Housing and Real Estate; Committee on Finance** | eLMS API `https://api.chicityclerkelms.chicago.gov/meeting?filter=body eq '<exact body name>'` (keyless, `top`/`skip` paging; use modest page size and a timeout — large pages time out). Meetings carry `files[]` with Agenda PDFs (text layer). Meeting `agenda` field is empty, so parse record numbers (`O`, `SO`, `R`, `Or`, etc. + year + number) from the agenda PDF text, then fetch `GET /matter/recordNumber/{recordNumber}` (verified 2026-10-13; `/matter?q=` is silently ignored and returns the newest matters) for title, sponsors, attachments, `actions[]` with votes. Committee outcomes joined by (body, action date) because committee actions often lack `meetingId`. Don't use `status eq 'Held'` (returns nothing); statuses seen: Scheduled, Scheduled & Published, Cancelled, Recessed, Reconvened, Rescheduled, Revised, Adjourned. |
| **CHA Board of Commissioners + Finance & Audit, Real Estate Operations Development, Tenant Services committees** | Legistar Web API `https://webapi.legistar.com/v1/thecha/` — `events` (filter by `EventBodyId` 138/188/189/190 and date), `events/{id}/eventitems` (title, matter file, action, passed flag, tally), `matters/{id}/attachments`. Ignore events with agenda status Hidden until published. This adapter is written generically (client name as a parameter) so v1.5 reuses it for suburban Legistar clients. |
| **Chicago Plan Commission** | Year listing page `.../depts/dcd/chicago-plan-commission/meetings--agendas---video-archives.html` → monthly hearing pages (stable pattern `supp_info/chicago_plan_commission/<Month>_<YYYY>_Plan_Commission_Hearing.html`) with a per-project table (address, ward, community area, description, application/presentation PDFs). Materials post ~2 weeks ahead. No recent minutes → no outcomes. |
| **Community Development Commission** | Listing `.../depts/dcd/community-development-commission/meetings--agendas-and-video-archives.html` (full-year schedule, cancellations marked) → monthly agenda HTML pages (inconsistent filenames: discover links from the listing, never construct URLs). No recent minutes. |
| **IHDA Board + Finance, Audit, Asset Management committees** | `https://www.ihda.org/about-ihda/public-meetings-and-notices/` — one WordPress page of notice/agenda/minutes PDFs. The agenda PDFs are multi-column tables whose text layer comes out scrambled, so items are extracted by Claude reading the PDF directly (`extraction = ai_extracted`) — the same component v1.5 uses for suburban agendas. Minutes lag ~2 months; outcomes from minutes when present. The same page supplies **Notices** (TEFRA hearings, QAP). |
| **CLIHTF** | The Events Calendar REST API `https://clihtf.org/wp-json/tribe/events/v1/events` (keyless JSON; verified) — board meetings are events titled "Board Meeting" whose `description` HTML holds the agenda; no minutes. |

chicago.gov blocks default clients: every chicago.gov request sends a full browser User-Agent.

**Page-change watcher (Notices).** `watch.yaml` lists URLs (initially: Department of Housing pages for QAP, RFPs, Data/Plans/Reports; CHA plan and public-comment pages; CLIHTF pages). Each run fetches, extracts main-content text and linked document list, and compares to the stored snapshot. A change produces a `page_change` Notice whose enrichment describes *what changed* (new linked documents are fetched and summarized). Snapshots are stored in the repo for auditability.

**Change detection.** Each run loads the previous snapshot (committed JSON), normalizes new data, and emits ChangeEvents for differences. `first_seen_at` is set once and never rewritten.

**Failure handling.** Each adapter runs in isolation; an exception or an implausible result (zero meetings where the source normally has some, or a parse yielding zero items from a non-empty agenda) marks that source `stale`, keeps its last good data, and triggers an admin email. One broken source never blocks the others.

**Politeness.** Cached HTTP with conditional requests where supported, a per-host delay, and a descriptive contact in the User-Agent comment where the host allows it.

## 6. Enrichment

- **Unit:** each Item and Notice; cached by `(id, content_hash)` — recomputed only when source text changes.
- **Input:** verbatim title + extracted attachment text (capped length). Scanned PDFs (no text layer) are sent as PDF documents so the model reads page images; `source_quality = scanned`.
- **Two-step models:** `claude-haiku-5-5` for the relevance screen on long, mostly-irrelevant agendas (Committee on Finance; later suburban agendas); `claude-sonnet-5-5` for summary, categories, entities, figures on items that pass. Structured output enforced by JSON schema.
- **Geography:** prefer source fields (Plan Commission rows carry ward and community area). Otherwise geocode the extracted address and assign ward/community area by point-in-polygon against city boundary files — never model-guessed.
- **Grounding check:** every extracted entity and figure must appear (normalized) in the source text; otherwise it is dropped and logged.
- **Overrides:** `overrides.yaml` keyed by item/notice id; any field set there wins and survives re-runs (`overridden = true`).
- **Labeling:** summaries are shown as "AI summary" with the source link beside them.
- **Cost control:** the cache means each item is enriched once; the plan includes a measurement step on one month of real items to produce a cost estimate before launch.

### Categories (`categories.yaml`)

The categories are IFE's housing projects. The model assigns zero or more categories to each relevant item; subscribers choose from the same list. There is no separate generic tag layer. Each category has: a `description` (the classification guidance given to the model — the scope text below), `bodies` (items from these bodies always get the category, deterministically, regardless of the model), and `keywords` (deterministic matches on title, summary, or entities, which also add the category). Changing a category definition triggers re-enrichment of affected items (a cost, not a migration).

| Category | IFE project | Scope (model guidance) | Keywords (initial) | Bodies |
|---|---|---|---|---|
| CHA & public housing | cha | CHA actions, redevelopment and production, RAD and Section 18 conversions, disposition, public-housing property tax/PILOT, Gautreaux | CHA, Chicago Housing Authority, RAD, Section 18, disposition, PILOT, Gautreaux, ACOP | all CHA bodies |
| Social housing | social-housing | Public or mixed-income developer models, acquisition and revolving funds, land trusts, disposition of public land for housing, CLIHTF | social housing, mixed-income, land trust, revolving fund, acquisition fund, CLIHTF | CLIHTF |
| Zoning & housing supply | ahpaa | Rezonings and planned developments with residential units, city-initiated rezonings, ADUs and missing middle, zoning text amendments; suburban housing plans and AHPAA actions | rezoning, planned development, upzoning, AHPAA, missing middle | Plan Commission |
| Property taxes | property-taxes | Property tax incentive classes for housing, tax sale and scavenger sale, Land Bank, PILOT agreements, assessment appeals | Class 7, Class 9, Class L, tax sale, scavenger, Land Bank, PILOT | — |
| State housing finance | state-agenda | IHDA actions, tax-exempt bonds and TEFRA hearings, QAPs, private activity bond volume cap, LIHTC awards | IHDA, TEFRA, QAP, volume cap, LIHTC, private activity bond | all IHDA bodies |
| Healthy homes & tenant protections | healthy-homes | Building code, lead, inspections, receivership, demolition, heat and habitability; eviction, right to counsel, RLTO, relocation, foreclosure tenant protections | lead, receivership, heat, habitability, eviction, right to counsel, Keep Chicago Renting | — |
| Affordable housing deals & policy | housing-agenda | City financing for affordable deals, the ARO, TIF for housing, Department of Housing plans and RFPs, citywide housing ordinances and budget | Department of Housing, ARO, Affordable Requirements Ordinance, TIF | — |
| Criminal legal system & housing | cls-housing | Criminal-background screening in housing, Just Housing, crime-free and nuisance-property ordinances, reentry and supportive housing, sealing/expungement as it bears on housing | Just Housing, criminal background, crime-free, nuisance, reentry, sealing, expungement | — |

## 7. Subscribers, matching, delivery (Cloudflare Worker + D1)

**Tables.** `subscribers (id, email, name, status: invited|active|unsubscribed|suspended, cadence: weekly|weekly_alerts|daily, created_at)`; `preferences (subscriber_id, categories[], wards[], community_areas[], keywords[], muted_bodies[])`; `tokens (subscriber_id, token_hash, expires_at)`; `sends (subscriber_id, ref_id, mode, sent_at)` — prevents re-sending the same item across modes; `send_log (id, subscriber_id, mode, provider_id, status, at)`.

**Onboarding (invite-only v1).** Admin adds an email (admin route protected by a secret, or a CLI script against D1). The Worker emails a magic link; the link opens a one-page preferences form (category checkboxes, ward/community-area picker, keyword box, cadence radio). No passwords. Every email footer has a fresh preferences link and one-click unsubscribe (`List-Unsubscribe` header). **Public signup later** = a form that creates an `invited` row and sends the same confirmation link.

**Matching (pure function, shared test fixtures).** An item/notice is *highlighted* for a subscriber if any of: one of its categories is chosen; a personal keyword matches (case-insensitive, word-boundary) the title, summary, or entities; its ward/community area is chosen. Each category on an item records why it was assigned (model, body rule, or keyword) so the reason can be shown. The function returns the list of reasons, rendered as "Matches **CHA & public housing**: CHA board item; keyword 'Section 18'." Muted bodies are dropped entirely.

**Modes.**
- *Weekly* (Sunday evening, America/Chicago): window = next 8 days of meetings + notices with open deadlines + outcomes from the past 7 days.
- *Weekly + alerts*: weekly, plus a short email after any ingest that produces ChangeEvents that match the subscriber and haven't been sent.
- *Daily*: one morning email if there are any new ChangeEvents since the last daily send that match the subscriber (their categories, wards/community areas, or keywords); nothing sent on days with no matches.

**Email layout (weekly).** 1) Your highlights; 2) Coming up (by date, with body, time, location, cancellations flagged); 3) New notices and open comment periods (deadline first); 4) Last week's outcomes (only where the body publishes them); 5) Everything else — compact list grouped by body, with "+N items not about housing" counts per meeting. Every item links to the web page entry and source documents. Alerts and daily emails contain only new/changed items that match the subscriber.

**Sending.** Resend from `housingdigest.danielkayhertz.com`. Worker cron runs daily (morning: daily + alerts after the morning ingest; Sunday evening: weekly). Bounce/complaint webhooks mark subscribers `suspended` after repeated hard bounces. Resend free-tier limits are checked at setup against subscriber count × cadence.

**Admin email (dhertz@impactforequity.org only).** Stale or failed sources, ingest errors, grounding-check drops above a threshold, bounce spikes.

## 8. Public web page

Built by the ingest Action to GitHub Pages from `items.json`: upcoming meetings by date, notices with deadlines, recent outcomes; filters by category, body, ward; each entry shows the AI summary label, outcome, and source links; anchors per item so emails deep-link. Per-body `.ics` calendar feeds. Static, no tracking.

## 9. Testing

- **Adapters:** fixture tests against saved real responses/pages for each source (including a cancelled meeting, a rescheduled meeting, an agenda with zero parseable items).
- **Diff/events:** fixture pairs of snapshots → expected ChangeEvents.
- **Contract:** `items.json` validated against `schema/items.schema.json` in both Python and TypeScript test suites.
- **Enrichment eval:** ~30 real items hand-labeled by Daniel (relevance + categories); run on any prompt or model change; report precision/recall for relevance and per-category agreement.
- **Grounding check:** unit tests with planted hallucinated figures.
- **Matching:** fixture items × fixture preferences → expected highlights and reason strings.
- **Rendering:** snapshot tests of email HTML for each mode.
- **Dry run:** a `send-to-admin-only` mode that renders real data and sends every mode to Daniel before any subscriber is activated.

## 10. Phasing

**v1 — Chicago + state.** Sources in §5; notices (IHDA + watcher on DOH, CHA, CLIHTF pages); enrichment; 8 categories; web page; Worker with invite-only onboarding and all three cadences; admin alerts.

**v1.5 — Suburbs.** Survey of 35 sample jurisdictions (2026-10-13; notes in session scratchpad) found:
- *Legistar* (keyless API): Cook, DuPage, Lake, Kane counties; Oak Park, Naperville, Aurora, Joliet. Reuse the generic Legistar adapter. Some clients have placeholder late-December events — filter by date and non-null agenda file.
- *CivicClerk* (keyless OData `{tenant}.api.civicclerk.com/v1/Events`): Arlington Heights, Westmont, Western Springs.
- *CivicPlus AgendaCenter* (RSS): Skokie, Wheaton, Park Forest.
- *Others*: Will County (IQM2, RSS), Evanston (CivicWeb), Bolingbrook (OpenGov), Cicero (JSON table), Downers Grove, Round Lake (custom).
- *Housing authorities* (HACC, DuPage, Joliet, Elgin, Oak Park, Waukegan, Lake County): all plain CMS pages of PDFs → page-change watcher.
- *Blocked to scripted fetch*: Berwyn, McHenry County, Schaumburg, Aurora main site, Waukegan.
- City Bureau's City Scrapers covers no suburban bodies; not a data source.

v1.5 adds the CivicClerk and AgendaCenter adapters, suburban Legistar clients via config, whole-agenda AI extraction behind a keyword screen (`extraction = ai_extracted`), suburban jurisdictions as a preference dimension, and a starter jurisdiction list Daniel selects from the survey. The Criminal legal system & housing category gains most here (Cook County Board, crime-free ordinances).

**v2 candidates.** Public signup; headless-browser fetching for blocked sites; moving the sender to an IFE domain.

## 11. Risks

- **Silent source breakage** (chicago.gov layout changes, CDC filename churn) — mitigated by implausible-result checks and admin alerts.
- **Late postings** (48-hour Open Meetings Act minimum) — 3×/day ingest plus alert/daily modes; weekly subscribers may miss late items, which the preferences page states.
- **AI summary errors** — grounding check, "AI summary" label, source link on every item, overrides file, eval set.
- **Outcome coverage is uneven** — only Council, CHA, and IHDA (lagged) publish outcomes; the email says nothing rather than implying no action.
- **eLMS substitute records:** a committee may act on an `SO…` substitute rather than the `O…` on the agenda; outcomes are looked up on the agenda record and, if absent, on records listed in its `supersededBy` field.
