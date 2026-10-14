from pathlib import Path

from digest.categories import Categories, keyword_match, merge_hits
from digest.models import CategoryHit

CATS = Categories(Path(__file__).resolve().parents[2] / "config" / "categories.yaml")


def test_eight_ife_categories():
    assert [c.id for c in CATS.items] == [
        "cha", "social-housing", "ahpaa", "property-taxes", "state-agenda",
        "healthy-homes", "housing-agenda", "cls-housing"]


def test_body_rule_wins_over_keyword():
    hits = CATS.rule_hits("CHA Board of Commissioners", ["Section 18 disposition"])
    assert CategoryHit(id="cha", via="body", detail="CHA Board of Commissioners") in hits


def test_keyword_rules():
    hits = {h.id: h for h in CATS.rule_hits("City Council Committee on Finance", ["Class 7 incentive for 1200 S. State"])}
    assert hits["property-taxes"].via == "keyword" and hits["property-taxes"].detail == "Class 7"


def test_acronyms_are_case_sensitive_and_word_bounded():
    assert not keyword_match("PILOT", "a pilot program")
    assert keyword_match("PILOT", "payment in lieu of taxes (PILOT) agreement")
    assert not keyword_match("CHA", "CHAIR of the committee")
    assert keyword_match("eviction", "Eviction diversion")
    assert not keyword_match("lead", "leadership")


def test_merge_hits_first_per_id_wins():
    a = [CategoryHit(id="cha", via="body")]
    b = [CategoryHit(id="cha", via="model"), CategoryHit(id="ahpaa", via="model")]
    assert [(h.id, h.via) for h in merge_hits(a, b)] == [("cha", "body"), ("ahpaa", "model")]


def test_prompt_block_and_fingerprint():
    assert "- cha: CHA & public housing." in CATS.prompt_block()
    assert len(CATS.fingerprint()) == 12


def test_fingerprint_ignores_comments_and_whitespace_but_not_meaning(tmp_path):
    raw = (Path(__file__).resolve().parents[2] / "config" / "categories.yaml").read_text(encoding="utf-8")
    base = tmp_path / "a.yaml"
    base.write_text(raw, encoding="utf-8")
    cosmetic = tmp_path / "b.yaml"
    cosmetic.write_text("# a new header comment\n\n" + raw.replace("\n", "\n\n", 3) + "\n# trailing\n", encoding="utf-8")
    assert Categories(cosmetic).fingerprint() == Categories(base).fingerprint()
    first_kw = CATS.items[0].keywords[0]
    changed = tmp_path / "c.yaml"
    changed.write_text(raw.replace(first_kw, first_kw + " extra", 1), encoding="utf-8")
    assert Categories(changed).fingerprint() != Categories(base).fingerprint()
