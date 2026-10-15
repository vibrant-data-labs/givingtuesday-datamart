"""``corrections_scout``: the names it scores. No database."""

from __future__ import annotations

import pandas as pd

from givingtuesday_datamart import corrections_scout as scout


def pair(name1: str, recip_name: str, name_jw: float, dollars: float) -> dict:
    return {"funder_ein": "1", "recip_ein": "9", "recip_name": recip_name, "name1_key": name1, "name2_key": "",
            "dollars": dollars, "name_jw": name_jw, "geometry": "cross_state", "addresscity_key": "hagerstown",
            "addressstate_key": "md", "evidence_url": "https://example.org/9"}


def test_the_digest_shows_the_compared_name_and_keeps_a_tuple_that_differs_by_a_legal_ending(tmp_path):
    """The matcher takes "river fund inc" and "river fund" for one name
    now, so what still keeps such a tuple unmatched is its address: a
    correction row's work, and the digest keeps it."""
    report = pd.DataFrame([pair("river fund", "river fund inc", 0.96, 500.0),
                           pair("river fnd of ny", "river fund inc", 0.80, 900.0)])
    digest = scout._write_labeled_digest(report, tmp_path / "digest.csv", jw_min=0.95)
    assert digest[["recip_ein", "tuples", "dollars", "example_tuple"]].values.tolist() == [["9", 1, 500.0, "river fund"]]
    assert (tmp_path / "digest.csv").exists()


def test_a_near_miss_in_the_state_is_scored_on_the_compared_name():
    universe = pd.DataFrame({"addressstate_key": ["ny", "ny", "ca"],
                             "compare_name": ["river fund inc", "open door", "river fund inc"],
                             "full_name": ["river fund", "open door", "river fund"]})
    score, row = scout._best_same_state(universe, "river fund incorporated", "ny")
    assert row == 0 and 0.9 < score < 1.0
