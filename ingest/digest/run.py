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
