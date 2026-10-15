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
                entities=["MKR Owner LLC", "Invented Partners LP"], units=1200, affordable_units=999,
                amount=None, amount_kind=None, zoning_from=[], zoning_to=None)
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
    assert c.cache.get("cpc:2026-10-15#a")["failed_attempts"] == 1 and c.cache.get("cpc:2026-10-15#b")["status"] == "ok"
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
    assert {(c.id, c.via) for c in e.categories} == {("housing-agenda", "keyword"), ("property-taxes", "keyword")}
    assert e.source_quality == "none"
    assert "Class 7 incentive guidance" in llm.calls[0]["content"][-1]["text"]


def test_generic_exception_fails_one_record_and_others_stay_ok(tmp_path):
    llm = FakeLLM({"summarize": [RuntimeError("SDK blew up"), summary()]})
    c = ctx(tmp_path, llm, routes=[("pd", PD_HTML)])
    a = pd_item(item_id="cpc:2026-10-15#a")
    b = pd_item(item_id="cpc:2026-10-15#b")
    d = enrich_digest(Digest(generated_at=NOW, meetings=[CPC], items=[a, b]), c)
    assert [i.enrichment.status for i in d.items] == ["failed", "ok"]
    assert "SDK blew up" in d.items[0].enrichment.relevance_reason
    assert [h.id for h in d.items[0].enrichment.categories] == ["ahpaa"]


def test_cache_entry_that_fails_validation_is_re_enriched(tmp_path):
    llm = FakeLLM({"summarize": [summary(), summary(summary="Second pass.")]})
    c = ctx(tmp_path, llm, routes=[("pd", PD_HTML)])
    rec = record_for_item(pd_item(), CPC)
    first = enrich_record(rec, c)
    c.cache.set(rec.id, {**c.cache.get(rec.id), "status": "not-a-status"})  # corrupt, same content_hash
    e = enrich_record(rec, c)
    assert len(llm.calls) == 2 and e.status == "ok" and e.summary == "Second pass."
    assert c.cache.get(rec.id)["content_hash"] == first.content_hash and c.cache.get(rec.id)["status"] == "ok"


def test_cached_ward_less_geography_is_re_resolved_without_the_llm(tmp_path):
    import json
    from digest.geo import Geo
    from digest.http import FetchError
    square = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"ward": "27", "community": "NEAR WEST SIDE"},
              "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}]}
    for name in ("wards.geojson", "areas.geojson"):
        (tmp_path / name).write_text(json.dumps(square), encoding="utf-8")
    geocache = JsonCache(tmp_path / "geocode.json")
    down = FakeFetcher([("geocoding.geo.census.gov", FetchError("census", 503)), ("nominatim", FetchError("osm", 503))])
    hit = json.dumps({"result": {"addressMatches": [{"coordinates": {"x": 0.5, "y": 0.5}}]}}).encode()
    up = FakeFetcher([("geocoding.geo.census.gov", hit)])
    llm = FakeLLM({"summarize": [summary()]})
    c = ctx(tmp_path, llm, geo=Geo(tmp_path / "wards.geojson", tmp_path / "areas.geojson", down, geocache))
    rec = record_for_item(pd_item(), CPC)
    first = enrich_record(rec, c)
    assert first.geography.address == "1133 W. Kinzie St." and first.geography.ward is None
    c.geo = Geo(tmp_path / "wards.geojson", tmp_path / "areas.geojson", up, geocache)
    second = enrich_record(rec, c)
    assert (second.geography.ward, second.geography.community_area) == (27, "Near West Side")
    assert len(llm.calls) == 1
    assert c.cache.get(rec.id)["geography"]["ward"] == 27


def test_failed_record_is_retried_three_times_then_given_up(tmp_path):
    llm = FakeLLM({"summarize": lambda content: (_ for _ in ()).throw(RuntimeError("still broken"))})
    c = ctx(tmp_path, llm)
    rec = record_for_item(pd_item(), CPC)
    for attempt in (1, 2, 3):
        assert enrich_record(rec, c).status == "failed"
        assert len(llm.calls) == attempt
        assert c.cache.get(rec.id)["failed_attempts"] == attempt
    e = enrich_record(rec, c)
    assert len(llm.calls) == 3 and e.status == "failed"
    assert [h.id for h in e.categories] == ["ahpaa"]
    changed = record_for_item(pd_item(url="https://x/pd-v2.html"), CPC)  # new content_hash resets the count
    c.fetcher.routes.append(("pd-v2", PD_HTML))
    enrich_record(changed, c)
    assert len(llm.calls) == 4


def _page_notice(**kw):
    base = dict(id="watch:doh-home:abc", source_id="watch", body="Chicago Department of Housing", kind="page_change",
                title="New on DOH home: 2027 RFP", url="https://x/new-rfp.html", detail="2027 RFP", new_link=True)
    return Notice(**{**base, **kw})


def test_new_link_html_notice_reads_the_linked_page(tmp_path):
    llm = FakeLLM({"summarize": [summary(categories=[], entities=[], units=None, affordable_units=None, address=None)]})
    c = ctx(tmp_path, llm, routes=[("new-rfp.html", b"<html><body><p>RFP text about 300 homes.</p></body></html>")])
    e = enrich_record(record_for_notice(_page_notice()), c)
    assert c.fetcher.calls == ["https://x/new-rfp.html"]
    assert "RFP text about 300 homes." in llm.calls[0]["content"][-1]["text"]
    assert e.source_quality == "full"


def test_text_diff_notice_does_not_fetch_the_page(tmp_path):
    llm = FakeLLM({"summarize": [summary(categories=[], entities=[], units=None, affordable_units=None, address=None)]})
    n = _page_notice(title="DOH home page updated", url="https://x/doh.html", detail="Comment period open", new_link=False)
    c = ctx(tmp_path, llm, routes=[])
    e = enrich_record(record_for_notice(n), c)
    assert c.fetcher.calls == [] and e.source_quality == "none"
    assert "Comment period open" in llm.calls[0]["content"][-1]["text"]


def test_overrides_accept_entities_figures_and_geography_merge(tmp_path):
    llm = FakeLLM({"summarize": [summary()]})
    rid = "cpc:2026-10-15#1133-w-kinzie-st"
    ov = {rid: {"entities": ["Corrected Owner LLC"], "figures": {"affordable_units": 240}, "geography": {"ward": 2}}}
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, overrides=ov))
    assert e.entities == ["Corrected Owner LLC"] and e.overridden
    assert (e.figures.units, e.figures.affordable_units) == (1200, 240)   # figures merge key by key
    assert e.geography.ward == 2 and e.geography.address is None


def test_unknown_override_key_warns_and_is_ignored(tmp_path, caplog):
    llm = FakeLLM({"summarize": [summary()]})
    rid = "cpc:2026-10-15#1133-w-kinzie-st"
    ov = {rid: {"status": "failed", "summary": "Fixed summary."}}
    with caplog.at_level("WARNING", logger="digest.enrich"):
        e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, overrides=ov))
    assert e.status == "ok" and e.summary == "Fixed summary."           # the valid key still applies
    assert any(rid in r.message and "'status'" in r.message for r in caplog.records)


def test_invalid_override_value_warns_and_is_ignored(tmp_path, caplog):
    llm = FakeLLM({"summarize": [summary()]})
    rid = "cpc:2026-10-15#1133-w-kinzie-st"
    ov = {rid: {"figures": {"units": "lots"}, "categories": "ahpaa", "entities": ["Fine LLC"]}}
    with caplog.at_level("WARNING", logger="digest.enrich"):
        e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, overrides=ov))
    assert e.figures.units == 1200 and [c.via for c in e.categories] != ["override"] * len(e.categories)
    assert e.entities == ["Fine LLC"]
    msgs = [r.message for r in caplog.records]
    assert any(rid in m and "'figures'" in m for m in msgs) and any(rid in m and "'categories'" in m for m in msgs)


def _api_errors():
    import anthropic
    import httpx
    from digest.http import FetchError
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(cls, code):
        return cls("boom", response=httpx.Response(code, request=req), body=None)
    return [
        anthropic.APIConnectionError(request=req),
        anthropic.APITimeoutError(request=req),
        status(anthropic.RateLimitError, 429),
        status(anthropic.InternalServerError, 500),
        status(anthropic.APIStatusError, 529),
        FetchError("https://x/doc", 503),
    ]


def test_transient_failures_never_exhaust_the_retry_cap(tmp_path):
    import pytest
    for n, err in enumerate(_api_errors()):
        llm = FakeLLM({"summarize": [err, err, err, summary()]})
        c = ctx(tmp_path / f"t{n}", llm)
        rec = record_for_item(pd_item(), CPC)
        for attempt in (1, 2, 3):
            assert enrich_record(rec, c).status == "failed", type(err).__name__
            assert c.cache.get(rec.id)["failed_attempts"] == 0
        assert enrich_record(rec, c).status == "ok", type(err).__name__
        assert len(llm.calls) == 4


def test_deterministic_api_errors_count_toward_the_cap(tmp_path):
    import anthropic
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    bad = anthropic.BadRequestError("prompt is too long", response=httpx.Response(400, request=req), body=None)
    for n, err in enumerate([bad, LLMRefused("refusal"), ValueError("bad")]):
        llm = FakeLLM({"summarize": lambda content, err=err: (_ for _ in ()).throw(err)})
        c = ctx(tmp_path / f"d{n}", llm)
        rec = record_for_item(pd_item(), CPC)
        for attempt in (1, 2, 3):
            assert enrich_record(rec, c).status == "failed"
        assert enrich_record(rec, c).status == "failed"
        assert len(llm.calls) == 3, type(err).__name__


def test_new_link_to_a_non_document_is_not_fetched_but_pages_and_pdfs_are(tmp_path):
    llm = FakeLLM({"summarize": lambda content: summary(categories=[], entities=[], units=None, affordable_units=None,
                                                          address=None)})
    c = ctx(tmp_path, llm, routes=[("", PD_HTML)])
    assert record_for_notice(_page_notice(url="https://x/form.docx")).urls == []
    assert record_for_notice(_page_notice(url="https://x/sheet.xlsx?dl=1#top")).urls == []
    for ok in ("https://x/a.pdf?x=1", "https://x/a.HTM", "https://x/a.html#frag", "https://x/programs/rfp", "https://x/"):
        assert record_for_notice(_page_notice(url=ok)).urls == [ok]
    enrich_record(record_for_notice(_page_notice(url="https://x/form.docx")), c)
    assert c.fetcher.calls == []


def test_timeout_status_408_is_transient(tmp_path):
    import anthropic
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.APIStatusError("timeout", response=httpx.Response(408, request=req), body=None)
    llm = FakeLLM({"summarize": [err, err, err, summary()]})
    c = ctx(tmp_path, llm)
    rec = record_for_item(pd_item(), CPC)
    for _ in range(3):
        assert enrich_record(rec, c).status == "failed"
    assert enrich_record(rec, c).status == "ok" and len(llm.calls) == 4


def test_auth_failure_halts_enrichment_for_the_run_without_counting(tmp_path, caplog):
    import anthropic
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    for cls, code in ((anthropic.AuthenticationError, 401), (anthropic.PermissionDeniedError, 403)):
        llm = FakeLLM({"summarize": [cls("bad key", response=httpx.Response(code, request=req), body=None), summary()]})
        c = ctx(tmp_path / str(code), llm)
        items = [pd_item(item_id=f"cpc:2026-10-15#{k}") for k in "abc"]
        with caplog.at_level("ERROR", logger="digest.enrich"):
            d = enrich_digest(Digest(generated_at=NOW, meetings=[CPC], items=items), c)
        assert [i.enrichment.status for i in d.items] == ["failed"] * 3
        assert len(llm.calls) == 1                                          # nothing after the first rejection
        assert len([r for r in caplog.records if r.levelname == "ERROR" and r.name == "digest.enrich"]) == 1
        assert c.cache.get("cpc:2026-10-15#a")["failed_attempts"] == 0     # not counted
        assert c.cache.get("cpc:2026-10-15#b") is None                      # skipped records leave no marker
        assert "skipped this run" in d.items[1].enrichment.relevance_reason
        caplog.clear()
        c.halted = ""                                                       # next run: a fresh context retries them
        assert enrich_record(record_for_item(items[0], CPC), c).status == "ok"


def _pdf(pages: int, text: str | None = None) -> bytes:
    """A blank (scanned-looking) PDF, or a one-page PDF whose text layer holds `text`."""
    if text is None:
        w = PdfWriter()
        for _ in range(pages):
            w.add_blank_page(612, 792)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()
    lines = "".join(f"({line}) Tj T* " for line in text.split("\n"))
    stream = f"BT /F1 10 Tf 12 TL 40 750 Td {lines}ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % x for x in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


def _pages_sent(block) -> int:
    import base64
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(base64.b64decode(block["source"]["data"]))).pages)


def _docs(call) -> list:
    return [b for b in call["content"] if isinstance(b, dict) and b.get("type") == "document"]


def zoning_item(attachments, title="Zoning Reclassification Map No. 1-H at 1921 W Chicago Ave - App No. 23153T1"):
    return Item(id="elms:Z1#O2026-1", meeting_id=ZONING.id, source_ref="O2026-1", title=title,
                attachments=[Attachment(label=label, url=url) for label, url in attachments])


def bare_summary(**kw):
    return summary(**{"units": None, "affordable_units": None, "address": None, "entities": [], **kw})


def test_screen_reads_the_start_of_the_document_and_the_summary_reuses_the_fetch(tmp_path):
    narrative = "\n".join(["The applicant proposes a four-story building with three dwelling units above retail."] * 5)
    llm = FakeLLM({"screen": [Screen(housing_relevant=True, reason="Adds homes.")], "summarize": [bare_summary()]})
    c = ctx(tmp_path, llm, routes=[("app.pdf", _pdf(1, narrative))])
    enrich_record(record_for_item(zoning_item([("Application", "https://x/app.pdf")]), ZONING), c)
    assert llm.calls[0]["purpose"] == "screen" and "three dwelling units" in str(llm.calls[0]["content"])
    assert c.fetcher.calls.count("https://x/app.pdf") == 1


def test_scanned_document_goes_to_the_screen_as_its_first_pages_only(tmp_path):
    llm = FakeLLM({"screen": [Screen(housing_relevant=False, reason="A car wash.")], "summarize": []})
    c = ctx(tmp_path, llm, routes=[("app.pdf", _pdf(9))])
    e = enrich_record(record_for_item(zoning_item([("Application", "https://x/app.pdf")]), ZONING), c)
    assert e.status == "screened_out" and [x["purpose"] for x in llm.calls] == ["screen"]
    assert [_pages_sent(d) for d in _docs(llm.calls[0])] == [2]


def test_scanned_pdf_sent_to_the_summary_is_trimmed_to_the_page_cap(tmp_path):
    llm = FakeLLM({"summarize": [bare_summary()]})
    c = ctx(tmp_path, llm, routes=[("scan.pdf", _pdf(12))])
    c.scanned_page_cap = 4
    enrich_record(record_for_item(pd_item(url="https://x/scan.pdf"), CPC), c)
    assert [_pages_sent(d) for d in _docs(llm.calls[0])] == [4]


def test_scanned_pdf_too_large_even_after_trimming_is_skipped(tmp_path):
    llm = FakeLLM({"summarize": [bare_summary()]})
    c = ctx(tmp_path, llm, routes=[("scan.pdf", _pdf(3))])
    c.max_doc_bytes = 100
    e = enrich_record(record_for_item(pd_item(url="https://x/scan.pdf"), CPC), c)
    assert e.status == "ok" and e.source_quality == "partial" and _docs(llm.calls[0]) == []


def test_disclosure_forms_and_cover_letters_are_not_fetched(tmp_path):
    llm = FakeLLM({"screen": [Screen(housing_relevant=True, reason="Adds homes.")], "summarize": [bare_summary()]})
    c = ctx(tmp_path, llm, routes=[("ord.pdf", _pdf(1))])
    it = zoning_item([("Or28250 EDS - Prime - redacted", "https://x/eds.pdf"),
                      ("O2026-0025391 Mayoral Letter", "https://x/letter.pdf"),
                      ("O2026-0025391 Ordinance", "https://x/ord.pdf")])
    enrich_record(record_for_item(it, ZONING), c)
    assert c.fetcher.calls == ["https://x/ord.pdf"]


def test_skipping_boilerplate_does_not_change_the_content_hash():
    rec = record_for_item(zoning_item([("EDS", "https://x/eds.pdf"), ("Ordinance", "https://x/ord.pdf")]), ZONING)
    assert rec.urls == ["https://x/eds.pdf", "https://x/ord.pdf"] and rec.fetch_urls == ["https://x/ord.pdf"]


def test_figures_from_a_scanned_source_count_when_the_summary_states_them(tmp_path):
    llm = FakeLLM({"summarize": [bare_summary(summary="Two buildings with three dwelling units each.", units=3,
                                              affordable_units=7)]})
    e = enrich_record(record_for_item(pd_item(url="https://x/scan.pdf"), CPC),
                      ctx(tmp_path, llm, routes=[("scan.pdf", _pdf(1))]))
    assert (e.figures.units, e.figures.affordable_units) == (3, None)


def test_spelled_out_numbers_in_document_text_ground_figures(tmp_path):
    html = b"<html><body><p>MKR Owner LLC proposes six dwelling units at 1133 W. Kinzie St.</p></body></html>"
    llm = FakeLLM({"summarize": [summary(units=6, affordable_units=None)]})
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, routes=[("pd.html", html)]))
    assert e.figures.units == 6


def test_unit_parser_fills_units_the_model_left_empty(tmp_path):
    html = b"<html><body><p>Erect a 4 story, 8 dwelling unit building at 1133 W. Kinzie St.</p></body></html>"
    llm = FakeLLM({"summarize": [summary(units=None, affordable_units=None)]})
    e = enrich_record(record_for_item(pd_item(), CPC), ctx(tmp_path, llm, routes=[("pd.html", html)]))
    assert e.figures.units == 8


def test_unit_parser_reads_the_summary_of_a_scanned_source(tmp_path):
    llm = FakeLLM({"summarize": [bare_summary(summary="Would allow a new 37-unit residential building.")]})
    e = enrich_record(record_for_item(pd_item(url="https://x/scan.pdf"), CPC),
                      ctx(tmp_path, llm, routes=[("scan.pdf", _pdf(1))]))
    assert e.figures.units == 37


def test_cached_record_without_units_is_backfilled_from_its_summary_without_the_llm(tmp_path):
    llm = FakeLLM({"summarize": [summary(summary="MKR Owner LLC proposes homes.", units=None, affordable_units=None)]})
    c = ctx(tmp_path, llm, routes=[("pd.html", b"<p>MKR Owner LLC proposes homes at 1133 W. Kinzie St.</p>")])
    rec = record_for_item(pd_item(), CPC)
    assert enrich_record(rec, c).figures.units is None
    cached = c.cache.get(rec.id)
    cached["summary"] = "Would allow a new six-unit residential building."
    c.cache.set(rec.id, cached)
    e = enrich_record(rec, c)
    assert e.figures.units == 6 and len(llm.calls) == 1 and c.cache.get(rec.id)["figures"]["units"] == 6


def test_address_grounding_ignores_street_suffix_spelling(tmp_path):
    geo = StubGeo()
    llm = FakeLLM({"screen": [Screen(housing_relevant=True, reason="Adds homes.")],
                   "summarize": [bare_summary(address="1921 W. Chicago Avenue")]})
    e = enrich_record(record_for_item(zoning_item([]), ZONING), ctx(tmp_path, llm, geo=geo))
    assert e.geography.address == "1921 W. Chicago Avenue" and e.geography.ward == 27


def test_address_falls_back_to_the_item_title(tmp_path):
    geo = StubGeo()
    llm = FakeLLM({"screen": [Screen(housing_relevant=True, reason="Adds homes.")], "summarize": [bare_summary()]})
    e = enrich_record(record_for_item(zoning_item([]), ZONING), ctx(tmp_path, llm, geo=geo))
    assert geo.calls == ["1921 W Chicago Ave"] and e.geography.ward == 27


def test_title_address_handles_aka_and_titles_without_one():
    from digest.enrich import title_address
    assert title_address("Zoning Reclassification Map No. 2-I at 2400 W Grenshaw St a.k.a. 1124 S Western Ave - App No. 2") \
        == "2400 W Grenshaw St"
    assert title_address("Historical landmark fee waiver for property at 208 S LaSalle St") == "208 S LaSalle St"
    assert title_address("Zoning Reclassification Map No. 10-E at 3927-3933 S Martin Luther King, Jr. Dr. - App No. 23135") \
        == "3927-3933 S Martin Luther King, Jr. Dr."
    assert title_address("Sale of City-owned property at 2054 and 2058 W Warren Blvd to EGP Warren Apartments LLC under "
                         "ChiBlockBuilder") == "2054 and 2058 W Warren Blvd"
    assert title_address("Sale of City-owned property at 4549 S Indiana Ave to Click Development, LLC 4549 Indiana") \
        == "4549 S Indiana Ave"
    assert title_address("Repeal of historical landmark designation for Cortland Bridge") is None
    assert title_address("Meeting at 10 a.m. on the budget") is None


def test_cached_record_without_an_address_takes_the_title_address_without_the_llm(tmp_path):
    llm = FakeLLM({"screen": [Screen(housing_relevant=True, reason="Adds homes.")], "summarize": [bare_summary()]})
    c = ctx(tmp_path, llm)
    rec = record_for_item(zoning_item([]), ZONING)
    enrich_record(rec, c)
    cached = c.cache.get(rec.id)
    cached["geography"] = {"address": None, "ward": None, "community_area": None}  # as saved by the old code
    c.cache.set(rec.id, cached)
    c.geo = StubGeo()
    e = enrich_record(rec, c)
    assert (e.geography.address, e.geography.ward) == ("1921 W Chicago Ave", 27) and len(llm.calls) == 2


def test_cached_over_long_title_address_is_trimmed_without_the_llm(tmp_path):
    title = "Sale of City-owned property at 4549 S Indiana Ave to Click Development LLC under ChiBlockBuilder"
    llm = FakeLLM({"screen": [Screen(housing_relevant=True, reason="Adds homes.")], "summarize": [bare_summary()]})
    c = ctx(tmp_path, llm, geo=StubGeo())
    rec = record_for_item(zoning_item([], title=title), ZONING)
    enrich_record(rec, c)
    cached = c.cache.get(rec.id)
    cached["geography"]["address"] = "4549 S Indiana Ave to Click Development LLC under ChiBlockBuilder"
    c.cache.set(rec.id, cached)
    e = enrich_record(rec, c)
    assert e.geography.address == "4549 S Indiana Ave" and e.geography.ward == 27 and len(llm.calls) == 2


def test_agency_office_address_is_not_used_as_the_site(tmp_path):
    geo = StubGeo()
    office = "111 E. Wacker, Suite 1000, Chicago, IL 60601"
    llm = FakeLLM({"summarize": [bare_summary(address=office)]})
    n = Notice(id="ihda:notice:1", source_id="ihda", body="IHDA", kind="public_notice", title="Public notice",
               url="https://x/n.html", detail=f"Send comments to {office}.")
    c = ctx(tmp_path, llm, routes=[("n.html", f"<p>Send comments to {office}.</p>".encode())], geo=geo)
    c.office_addresses = ["111 E Wacker", "225 N Michigan"]
    e = enrich_record(record_for_notice(n), c)
    assert e.geography.address is None and geo.calls == []


def test_cached_office_address_is_cleared_without_the_llm(tmp_path):
    llm = FakeLLM({"summarize": [summary()]})
    c = ctx(tmp_path, llm, geo=StubGeo())
    rec = record_for_item(pd_item(), CPC)
    assert enrich_record(rec, c).geography.address == "1133 W. Kinzie St."
    c.office_addresses = ["1133 W Kinzie"]
    e = enrich_record(rec, c)
    assert e.geography == Geography() and len(llm.calls) == 1
    assert c.cache.get(rec.id)["geography"]["address"] is None


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
