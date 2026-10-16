"""Parser rules checked against real lines from the sample pages.

Run: .venv/bin/python -m pytest tests/
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from parse_pages import (  # noqa: E402
    classify_page, describe_street, join_lines, normalize_line, parse_listing, parse_row,
    starts_entry,
)


def listing(text):
    return parse_listing(text)[0]


# ------------------------------------------------------------- joining lines

@pytest.mark.parametrize("lines, joined", [
    (["mgr city dept West-", "ern Assurance Co"], "mgr city dept Western Assurance Co"),
    (["attys-at-law, 137-139-", "141 Phelan Bldg"], "attys-at-law, 137-139-141 Phelan Bldg"),
    (["from S s Twenty-", "fourth bet Harrison"], "from S s Twenty-fourth bet Harrison"),
    (["salesman Roberts Mfg", "Co, r 954 Ashbury"], "salesman Roberts Mfg Co, r 954 Ashbury"),
])
def test_join_lines(lines, joined):
    assert join_lines(lines) == joined


# ------------------------------------------------------- listing boundaries

@pytest.mark.parametrize("line, prev, new", [
    ("Adams William, mach opr, r 2 Juri", "Hotel Cumberland", True),
    ("Howard", "Adams William A, glasswkr, r 1891", False),         # outside ADA-ADE
    ("Co, r 954 Ashbury", "Adams William, salesman Roberts Mfg", False),
    ("r 480 Ellis", "Adamson Walter P, reporter Chronicle,", False),
    ("☞ Adamy. See Adami", "Adamy William, r 7 Meacham Pl", True),
    ("**ADDISON THOMAS,** mgr General Elec-", "Co, r 1713 Green", True),
    ("Adelstein", "teacher Irving Insti-", False),                   # after a hyphen
])
def test_starts_entry(line, prev, new):
    assert starts_entry(line, prev, ("ADA", "ADE")) is new


# ------------------------------------------------------------ listings

def test_person_with_employer_and_residence():
    r = listing("Adams William F, bkkpr Broadway Brewery, r 882 Lombard")
    assert (r["entry_type"], r["surname"], r["given_names"]) == ("person", "Adams", "William F")
    assert (r["occupation"], r["employer"], r["residence"]) == ("bkkpr", "Broadway Brewery", "882 Lombard")


def test_bold_caps_name_and_business_address():
    r = listing("ADAMS WILLIAM J, whsle lumber and shipping mer and pres Washington Mill Co, "
                "Schmiedell Bldg, 604 Montgomery, r Menlo Park")
    assert (r["surname"], r["given_names"]) == ("Adams", "William J")
    assert r["employer"] == "Washington Mill Co"
    assert r["business_address"] == "Schmiedell Bldg, 604 Montgomery"
    assert r["residence"] == "Menlo Park"


def test_title_suffix_and_widow():
    r = listing("Adamson W H Mrs, propr The Carlton, 611 Taylor")
    assert (r["name_title"], r["occupation"], r["employer"]) == ("Mrs", "propr", "The Carlton")
    assert listing("Sadler Hermann J Jr, r 906 Van Ness Av")["name_suffix"] == "Jr"
    r = listing("Boardman Carro T, widow, teacher elocution, 102 O'Farrell, r 622 Green")
    assert r["is_widow"] and r["occupation"] == "widow, teacher elocution"
    assert r["business_address"] == "102 O'Farrell"


def test_address_with_ordinal_street():
    assert listing("Sadler William, carp, r 1465, 11th Av")["residence"] == "1465, 11th Av"
    assert listing("Sader Charles, shoemkr, 1534, 20th")["business_address"] == "1534, 20th"


def test_affiliation_and_residence_without_comma():
    r = listing("Addotto Antonio (Addotto & Bona) r 1704 Waller")
    assert (r["affiliation"], r["residence"]) == ("Addotto & Bona", "1704 Waller")


def test_organization_with_comma_in_name_and_phone():
    r = listing("SAGE, DEALEY & CO (O G Sage and G L Dealey) real estate, auctioneers, "
                "insurance and rent collectors, 638 Market")
    assert r["entry_type"] == "organization"
    assert r["name_raw"] == "SAGE, DEALEY & CO"
    assert r["affiliation"] == "O G Sage and G L Dealey"
    assert r["business_address"] == "638 Market"
    r = listing("ADAMS & ADAMS (Amos C and Charles Albert Adams) attys-at-law, "
                "137-139-141 Phelan Bldg, tel Main 953")
    assert (r["phone"], r["business_address"]) == ("Main 953", "137-139-141 Phelan Bldg")


@pytest.mark.parametrize("text, name, see", [
    ("☞ Adamy. See Adami", "Adamy", "Adami"),
    ("Bluxome. See Blocksom", "Bluxome", "Blocksom"),
    ("Sacred Heart Academy. See Academy of the Sacred Heart",
     "Sacred Heart Academy", "Academy of the Sacred Heart"),
])
def test_cross_references(text, name, see):
    r = listing(text)
    assert (r["entry_type"], r["name_raw"], r["see_also"]) == ("cross_reference", name, see)


# ------------------------------------------------------------ street guide

@pytest.mark.parametrize("line, row", [
    ("Stockton ... 200 201", ("Stockton", "200", "201")),
    ("Golden Gate Avenue. 800 801", ("Golden Gate Avenue", "800", "801")),
    ("Lyon ... (e) (e)", ("Lyon", "(e)", "(e)")),
    ("Midway ... —", ("Midway", "—", "")),
    ("Montgomery Av. ...", ("Montgomery Av", "", "")),
])
def test_cross_street_rows(line, row):
    assert parse_row(normalize_line(line)) == row


def test_italic_marks_removed_bold_kept():
    assert normalize_line("*Cross Sts.* N. S.") == "Cross Sts. N. S."
    assert normalize_line("**Bay**, from the bay") == "**Bay**, from the bay"


def test_leader_dots_of_any_length():
    assert parse_row(normalize_line("Geary ..............1300 1301")) == ("Geary", "1300", "1301")


def test_street_description():
    d = describe_street("(late Parnassus Av), from W s Railroad Av at Eighteenth Av South")
    assert d["former_name"] == "Parnassus Av"
    assert d["description"] == "from W s Railroad Av at Eighteenth Av South"
    d = describe_street("(South S. F.), see Thirty-ninth Av South")
    assert (d["district"], d["see_reference"]) == ("South S. F.", "Thirty-ninth Av South")


# ------------------------------------------------------------ page types

def test_classify_page():
    alpha = classify_page("p", "SAC CROCKER-LANGLEY DIRECTORY. SAH 1615", [])
    assert (alpha["page_type"], alpha["printed_page"], alpha["range_end"]) == ("alphabetical", "1615", "SAH")
    classified = classify_page("p", "38 Art Societies. CROCKER-LANGLEY DIRECTORY. Banks.", [])
    assert (classified["page_type"], classified["section_right"]) == ("classified", "Banks")
    street = classify_page("p", "unreadable", [(1, "Pine ... 100 101")] * 5)
    assert street["page_type"] == "street_guide"
