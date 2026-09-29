"""``placeholder_recovery.classifier``: the SQL pattern is the measured one,
reads the same names as ``attachment_grants.is_pointer``, and the prefilter
lets every pointer through. No database: the patterns are read as Python
regular expressions, with Postgres's ``\\y`` as ``\\b``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from givingtuesday_datamart.attachment_grants import is_pointer
from givingtuesday_datamart.placeholder_recovery import classifier

MEASURED = Path(__file__).resolve().parents[1] / "data" / "exploratory" / "placeholder_population_by_year.sql"
POINTERS = ["SEE ATTACHED", "See Attachment 22", "SEE GRANTS PAID ATTACHMENT", "REFER TO STATEMENT 12",
            "SCHEDULE ATTACHED", "STATEMENT 25", "ATCH 4", "Please see attached list", "EXHIBIT A", "GRANTS",
            "TOTAL", "Contributions", "Grants Paid", "see", "PER ATTACHED SCHEDULE", "SUPPLEMENTAL SCHEDULE 3"]
NOT_POINTERS = ["AVAILABLE UPON REQUEST", "SEE ATTACHED - KEPT ON FILE", "United Way of Greater Atlanta",
                "Various", "Tennessee Aquarium", "The Seeing Eye Inc", "Grant Elementary School PTA",
                "Statement of Faith Ministries International Outreach Center", "", "Listening Post Collective Fund"]


def _python(pattern: str) -> re.Pattern:
    return re.compile(pattern.replace(r"\y", r"\b"), re.I)


def _sql_says_pointer(name: str) -> bool:
    return not _python(classifier.WITHHELD).search(name) and any(_python(p).search(name) for p in classifier.POINTER)


def test_the_patterns_are_the_ones_the_population_sql_measured():
    measured = MEASURED.read_text()
    for pattern in (classifier.WITHHELD, *classifier.POINTER):
        assert f"'{pattern}'" in measured


@pytest.mark.parametrize("name", POINTERS + NOT_POINTERS)
def test_the_sql_pattern_and_is_pointer_agree(name):
    assert _sql_says_pointer(name) == is_pointer(name) == (name in POINTERS)


@pytest.mark.parametrize("name", POINTERS)
def test_the_prefilter_lets_every_pointer_through(name):
    assert _python(classifier.PREFILTER).search(name)


def test_every_alternative_of_the_pattern_needs_a_prefilter_word():
    """Take the prefilter's words out of a pattern and nothing it could match is left to find."""
    words = classifier.PREFILTER.split("|")
    for pattern in classifier.POINTER:
        for branch in re.split(r"\|(?![^(]*\))", pattern):           # the top-level alternatives
            assert any(word in branch for word in words), branch


def test_pointer_sql_tests_the_withheld_pattern_first_and_every_alternative():
    sql = classifier.pointer_sql("n.name")
    assert sql.startswith(f"(n.name !~* '{classifier.WITHHELD}' AND (")
    assert sql.count("n.name ~* '") == len(classifier.POINTER)


def test_name_sql_joins_the_person_and_business_names():
    assert classifier.name_sql("g") == (
        r"trim(regexp_replace(concat_ws(' ', g.sigocpyrpnam, g.sigocpyrbnbn1, g.sigocpyrbnbn2), '\s+', ' ', 'g'))")
