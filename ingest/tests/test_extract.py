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
    assert [i.id for i in items] == ["clihtf:319068#allocations-committee-report", "clihtf:319068#2027-budget"]
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
    assert items[0].id == "ihda:ihda-board:2026-09-18#pheasant-ridge-hunter-apartments-tax-exempt-bond-financing"


def test_unnumbered_items_keep_a_number_as_source_ref_and_repeated_titles_get_unique_ids(tmp_path):
    llm = FakeLLM({"extract": [rows(("", "Staff report"), ("", "Staff report"), ("A.", "Budget"))]})
    items = extract_items(clihtf_meeting(), FakeFetcher([]), llm, JsonCache(tmp_path / "e.json"))
    assert [i.id.split("#")[1] for i in items] == ["staff-report", "staff-report-2", "budget"]
    assert [i.source_ref for i in items] == ["1", "2", "A"]


def test_ids_survive_an_agenda_revision_that_drops_an_earlier_item(tmp_path):
    before = rows(("IV.A.1", "Mythics Sole Source"), ("IV.A.2", "Resolution Ratifying Permitted Financial Activities"))
    after = rows(("IV.A.1", "Resolution Ratifying Permitted Financial Activities"))
    llm = FakeLLM({"extract": [before, after]})
    cache = JsonCache(tmp_path / "e.json")
    first = extract_items(clihtf_meeting(), FakeFetcher([]), llm, cache)
    second = extract_items(clihtf_meeting(AGENDA + " (revised)"), FakeFetcher([]), llm, cache)
    assert first[1].id == second[0].id == "clihtf:319068#resolution-ratifying-permitted-financial-activities"
    assert second[0].source_ref == "IV.A.1"


def test_corrupt_cache_entry_is_a_miss(tmp_path):
    cache = JsonCache(tmp_path / "e.json")
    llm = FakeLLM({"extract": [rows(("1", "A"))]})
    m = clihtf_meeting()
    import hashlib
    h = hashlib.sha256(m.agenda_text.encode("utf-8")).hexdigest()[:16]
    cache.set(m.id, {"hash": h, "items": [{"bogus": 1}]})
    items = extract_items(m, FakeFetcher([]), llm, cache)
    assert [i.title for i in items] == ["A"] and len(llm.calls) == 1


def test_long_titles_with_a_shared_prefix_get_order_independent_ids(tmp_path):
    a = "An ordinance authorizing the execution of an intergovernmental agreement with the Board of Education for Smith"
    b = "An ordinance authorizing the execution of an intergovernmental agreement with the Board of Education for Jones"
    llm = FakeLLM({"extract": [rows(("1", a), ("2", b)), rows(("1", b))]})
    cache = JsonCache(tmp_path / "e.json")
    first = extract_items(clihtf_meeting(), FakeFetcher([]), llm, cache)
    second = extract_items(clihtf_meeting(AGENDA + " (revised)"), FakeFetcher([]), llm, cache)
    assert first[0].id != first[1].id and second[0].id == first[1].id
    assert all(len(i.id.split("#")[1]) <= 80 for i in first)
