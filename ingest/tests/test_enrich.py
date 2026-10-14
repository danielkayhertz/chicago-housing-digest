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
