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
