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
    it = item("Sale of City-owned property at 2054 W Warren Blvd to EGP Warren Apartments LLC", geography=Geography(ward=27),
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


import datetime as dt  # noqa: E402

from digest.layout import build_outline  # noqa: E402
from digest.models import CHI, Attachment, CategoryHit, Meeting  # noqa: E402


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
    assert [x.recent for x in o.index] == [False, False, False, True]
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


def test_party_must_be_named_in_the_title():
    # sponsors, presenters and the appointing mayor are entities the model finds, but not the buyer or applicant
    rez = item("Zoning Reclassification Map No. 10-F at 4639-4643 S Wallace St", entities=["Raymond A. Lopez"])
    assert facts(rez, "4639-4643 S Wallace St") == []
    talk = item("Multifamily Update", entities=["S. Bannon"])
    assert facts(talk, "Multifamily Update") == []
    pd = item("A proposed Planned Development, submitted by MKR Owner LLC for 1133 W. Kinzie St.",
              entities=["MKR Owner LLC"])
    assert [fact_text(f) for f in facts(pd, "1133 W. Kinzie St.")] == ["MKR Owner LLC"]


def test_address_bodies_lead_with_the_address_in_a_flat_body():
    pd = item("A proposed Planned Development, submitted by MKR Owner LLC for the property generally located at "
              "1133 W. Kinzie St.", geography=Geography(address="1133 W. Kinzie St."))
    assert headline(L, pd, None, "Chicago Plan Commission") == "1133 W. Kinzie St."
    assert headline(L, item("Item 3 South Chicago Land Use Framework"), None, "Chicago Plan Commission")         == "Item 3 South Chicago Land Use Framework"                          # no address: the title
    assert headline(L, item("Pheasant Ridge", geography=Geography(address="1 Main St")), None, "IHDA Board")         == "Pheasant Ridge"                                                   # not an address body


from digest.layout import important  # noqa: E402
from digest.models import Outcome  # noqa: E402


def test_importance_rules_and_hand_flags():
    assert important(L, item("x", figures=Figures(units=10)), None)
    assert important(L, item("x", amount=1_000_000), "Sales of city land")
    assert important(L, item("x"), "Planned developments") and important(L, item("x"), "Zoning code changes")
    assert not important(L, item("x", figures=Figures(units=3)), "Single-site rezonings")
    assert important(L, item("x", categories=[CategoryHit(id="cha", via="model")]), None)
    assert not important(L, item("x", categories=[CategoryHit(id="cha", via="body")]), None)   # body-wide tag
    assert not important(L, item("x", categories=[CategoryHit(id="ahpaa", via="model")]), None)
    assert important(L, item("x", important=True), "Single-site rezonings")                   # flagged in
    assert not important(L, item("x", important=False, figures=Figures(units=99)), None)       # flagged out
    assert not important(L, item("no enrichment"), None)


def test_recent_meetings_carry_outcomes_and_a_tally():
    z = meeting("elms:Z", ZON, dt.datetime(2026, 10, 1, 10, 0, tzinfo=CHI))
    z.source_id = "elms-zoning"
    z.minutes_url = "https://x/summary.pdf"
    a, b, c, d = (item(f"Zoning Reclassification Map No. 1-H at {n} W A St", id=f"z#{n}") for n in (1, 2, 3, 4))
    a.outcome = Outcome(action="Recommended to Pass", date=dt.date(2026, 10, 1))
    b.outcome = Outcome(action="Recommended to Pass", date=dt.date(2026, 10, 1))
    c.outcome = Outcome(action="Held in Committee", date=dt.date(2026, 10, 1))
    up = meeting("elms:U", ZON, dt.datetime(2026, 10, 20, 10, 0, tzinfo=CHI))
    up.source_id = "elms-zoning"
    cpc = meeting("cpc:1", "Chicago Plan Commission", dt.datetime(2026, 10, 2, 10, 0, tzinfo=CHI))
    cpc.source_id = "cpc"
    o = build_outline([(up, [], 0)], [(z, [a, b, c, d], 0), (cpc, [item("Kinzie PD", id="c#1")], 0)], L, {})
    upv, (cv, zv) = o.days[0].meetings[0], o.days[1].meetings              # Recent is newest first
    assert zv.outcomes and zv.tally == "2 recommended to pass · 1 held in committee · 1 no outcome posted yet"
    assert not upv.outcomes and upv.tally == ""                       # upcoming: nothing to report yet
    assert not cv.outcomes and cv.tally == ""                         # a body that publishes no outcomes
    assert [v.item.outcome.action if v.item.outcome else None for g in zv.groups for v in g.cards + g.rows] \
        == ["Recommended to Pass", "Recommended to Pass", "Held in Committee", None]
