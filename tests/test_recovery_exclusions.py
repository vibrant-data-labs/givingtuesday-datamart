"""``placeholder_recovery.exclusions``: the tracked file, and what makes a
file not the registry."""

from __future__ import annotations

from datetime import date

import pytest

from givingtuesday_datamart.placeholder_recovery import frame as frame
from givingtuesday_datamart.placeholder_recovery import exclusions as ex

HEADER = "filerein,filer_name,evidence,excluded_on\n"


def _file(tmp_path, body):
    path = tmp_path / "exclusions.csv"
    path.write_text(HEADER + body)
    return path


def test_the_tracked_file_names_the_six_filers_each_with_evidence_and_a_date():
    found = ex.load_exclusions()
    assert sorted(found) == ["010575520", "200031992", "262502555", "311810072", "431614543", "460500266"]
    assert found["010575520"] == ex.Exclusion("010575520", "Merck Patient Assistance Program",
                                              "its four itemised years list individuals only", date(2026, 9, 28))
    assert found["460500266"].evidence == (
        'placeholder row "Eligible Patients", purpose "Provide Prescription Drugs"')
    assert all(row.evidence and row.filer_name for row in found.values())


def test_the_file_is_plain_text_in_git_not_an_lfs_pointer():
    assert ex.EXCLUSIONS_CSV.read_text().startswith(HEADER)


def test_the_frame_commands_read_the_same_file():
    assert frame.PATIENT_ASSISTANCE == {ein: row.filer_name for ein, row in ex.load_exclusions().items()}


@pytest.mark.parametrize("body, message", [
    ("46050026,Short,evidence,2026-09-28\n", "not a nine-digit EIN"),
    ("460500266,Genentech,,2026-09-28\n", "excluded without evidence"),
    ("460500266,Genentech,evidence,September 28\n", "is not a date"),
    ("460500266,Genentech,evidence,2026-09-28\n460500266,Again,evidence,2026-09-28\n", "listed twice"),
])
def test_a_row_that_is_not_an_exclusion_is_refused(tmp_path, body, message):
    with pytest.raises(ValueError, match=message):
        ex.load_exclusions(_file(tmp_path, body))


def test_other_columns_are_refused(tmp_path):
    path = tmp_path / "exclusions.csv"
    path.write_text("ein,name\n460500266,Genentech\n")
    with pytest.raises(ValueError, match="has columns"):
        ex.load_exclusions(path)
