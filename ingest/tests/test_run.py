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
