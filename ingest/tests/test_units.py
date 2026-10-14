import pytest

from digest.units import parse_units

# The first block is ported from the Chicago 5-Story Permits test suite (test_parsers.py).
PERMIT_CASES = [
    ("5 STORY 32 DU BUILDING", 32),
    ("5 STORY 32 D.U. BUILDING", 32),
    ("ERECT 12 DUS OVER RETAIL", 12),
    ("NEW III-A CONSTRUCTION OF 1 COMMERCIAL UNIT (RETAIL) AND 24 DWELLING UNITS 5 STORY MASONRY BUILDING", 24),
    ("NEW CONSTRUCTION 50 UNIT, 5 STORY RESIDENTIAL APARTMENT BUILDING", 50),
    ("ERECT 3 STORY DUPLEX RESIDENCE", None),
    ("NEW 5 STORY SELF STORAGE BUILDING", None),
    ("SELF CERTIFICATION W/ PROTOTYPE PLANS - NEW 4 STORY SLAB ON GRADE MASONRY BUILDING. 1,014 SF COMMERCIAL UNIT "
     "ON GROUND FLOOR. 2ND & 3RD FLOORS, 3 BEDROOM 2 BATH 1,450 SF RESIDENTIAL UNITS. 4TH FLOOR 3 BEDROOM 2 BATH WITH "
     "LOFT, 1,750 SF RESIDENTIAL UNIT.", None),
    ("NEW 4 STORY BUILDING WITH 850 SF RESIDENTIAL UNITS PER PLANS", None),
    ("NEW 5 STORY BUILDING, 1200 SQ FT DWELLING UNITS AS PER PLANS", None),
    ("NEW 5 STORY BUILDING WITH 24 DWELLING UNITS, EACH 1,450 SF, AND 1,014 SF COMMERCIAL UNIT ON GROUND FLOOR", 24),
    ("NEW 5 STORY BUILDING WITH 1,200 DWELLING UNITS", 1200),
    ("SPR 2019 CBC, A-2 ERECT A 3-STORY,3 DWELLING UNITS, W/ REAR OPEN PORCH AND A DETACHED 3-CAR GARAGE.", 3),
    ("ERECT  4 STORY ,8 DWELLING UNIT IIIB BUILDING WITH PRIVATE ROOF DECK", 8),
    ("DDS CBC 2019, CONSTRUCT NEW 5 STORY RESIDENTIAL BUILDING, FIRST FLOOR INTERNAL PARKING 25 SPACES,28 DWELLING "
     "UNITS PER PLANS", 28),
    ("5 STORY BUILDING, 19 RESIDENTIAL DWELLING UNITS, 2 UNIT EACH FLOOR", 19),
    ("NEW CONSTRUCTION 34 STANDARD DWELLING UNITS", 34),
    ("(10) TEN RESIDENTIAL DWELLING UNIT BUILDING", 10),
]

AGENDA_CASES = [
    ("Ordinance rezones the site to allow a four-story building with three dwelling units above retail.", 3),
    ("would allow a new 8-unit residential building on a vacant lot", 8),
    ("The rezoning would allow a new 37-unit residential building with ground-floor commercial space.", 37),
    ("legalize the fifth existing dwelling unit", None),
    ("a new four-story mixed-use building with 2 retail units and no homes", None),
    ("A sign permit at 100 W. Madison St.", None),
    ("", None),
]


@pytest.mark.parametrize("text,expected", PERMIT_CASES + AGENDA_CASES)
def test_parse_units(text, expected):
    assert parse_units(text) == expected
