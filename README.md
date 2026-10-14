# Chicago Housing Digest

Collects Chicago housing-related public meetings, agenda items and notices into `data/items.json`, summarizes each item once with Claude, and publishes a page with calendar feeds. The email digest (Plan 2) reads `data/items.json`.

Sources: City Council committees on Zoning, Housing and Real Estate, and Finance (eLMS); CHA board and committees (Legistar); Plan Commission and Community Development Commission (chicago.gov); IHDA board, committees, TEFRA hearings and notices; CLIHTF board; and a page watcher for DOH and CLIHTF pages (`config/watch.yaml`; CHA pages are not watched because thecha.org blocks automated fetches).

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
