"""Dwelling-unit counts from free text.

Ported from parse_units() in the Chicago 5-Story Permits project (parsers.py), where it was hand-checked against
permit work descriptions. One addition for agenda documents: spelled-out counts ("three dwelling units").
"""
import re

_WORD_NUMBERS = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6,
    "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10, "ELEVEN": 11,
    "TWELVE": 12, "THIRTEEN": 13, "FOURTEEN": 14, "FIFTEEN": 15,
    "SIXTEEN": 16, "SEVENTEEN": 17, "EIGHTEEN": 18, "NINETEEN": 19,
    "TWENTY": 20,
}

# Tier 1: unambiguous dwelling-unit vocabulary. Tier 2: generic "N UNITS", weaker evidence. Tier 1 wins outright.
# A count may carry thousands separators. Capture the separated form whole and refuse to start mid-number:
# without the lookbehinds, "1,750 SF RESIDENTIAL UNIT" matches at the "750" and reports a 3-unit building as 750.
# Only a comma between digits is a thousands separator; "ERECT A 3-STORY,3 DWELLING UNITS" uses it as a clause break.
_NUM = r"(?<!\d)(?<!\d,)(\d{1,3}(?:,\d{3})+|\d+)"
_WORD = r"\b(" + "|".join(_WORD_NUMBERS) + r")"

_STRONG_SUFFIXES = [
    # An adjective may sit between the count and the phrase: "19 RESIDENTIAL DWELLING UNITS".
    r"\s*(?:[A-Z]+\s+){0,2}?(?:DWELLING|RESIDENTIAL|LIVING)\s+UNITS?\b",
    r"\s*(?:CONDO(?:MINIUM)?|APARTMENT)\s+UNITS?\b",
    r"\s*[- ]?UNIT\s+(?:APARTMENT|RESIDENTIAL|CONDO|MULTI)",
]
_UNIT_PATTERNS_STRONG = [_NUM + s for s in _STRONG_SUFFIXES] + [
    _NUM + r"\s*D\.\s?U\.?(?:'?S)?",          # "32 D.U." / "32 D.U'S"
    _NUM + r"\s*DUS?\b",                       # "32 DU" / "32 DUS"
    # "(10) TEN RESIDENTIAL DWELLING UNIT BUILDING": the word defeats the patterns above, so read the numeral.
    r"\((\d+)\)\s*[A-Z\- ]{0,24}?(?:DWELLING|RESIDENTIAL)\s+UNITS?\b",
] + [_WORD + s for s in _STRONG_SUFFIXES]
_UNIT_PATTERNS_WEAK = [
    _NUM + r"\s*[- ]?UNITS?\b",
    _NUM + r"\s*APARTMENTS?\b",
    _WORD + r"\s*[- ]?UNITS?\b",
]

# An area figure standing where a count would stand: "1,450 SF RESIDENTIAL UNITS" is a size, not a count.
_AREA_SUFFIX = re.compile(r"\s*(?:SQ\.?\s*(?:FT|F)\b|SQUARE\s+(?:FEET|FOOT)|S\.?F\.?(?![A-Z]))")

# "2 RETAIL UNITS", "1 COMMERCIAL UNIT": not dwellings, and must not reach the weak tier.
_NON_DWELLING = re.compile(r"\s*(?:COMMERCIAL|RETAIL|OFFICE|STORE|PARKING|STORAGE)\s+UNITS?\b")


def _value(token: str) -> int:
    return _WORD_NUMBERS[token] if token in _WORD_NUMBERS else int(token.replace(",", ""))


def parse_units(text: str) -> int | None:
    """The dwelling-unit count stated in the text (the largest, if several), or None."""
    desc = (text or "").upper()
    for tier in (_UNIT_PATTERNS_STRONG, _UNIT_PATTERNS_WEAK):
        found = []
        for pat in tier:
            for m in re.finditer(pat, desc):
                if _AREA_SUFFIX.match(desc, m.end(1)) or _NON_DWELLING.match(desc, m.end(1)):
                    continue
                n = _value(m.group(1))
                if 1 <= n <= 2000:
                    found.append(n)
        if found:
            return max(found)
    return None
