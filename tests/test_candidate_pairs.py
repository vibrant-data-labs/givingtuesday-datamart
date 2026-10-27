"""The matcher's candidate pairs in integers must be the pairs recordlinkage's
two blocks make, in the order pandas' MultiIndex.union returns them: the
stable tie-break in resolve_matches depends on that order."""
import numpy as np
import pandas as pd
import pytest

recordlinkage = pytest.importorskip("recordlinkage")

from givingtuesday_datamart.grant_matching import candidate_pairs  # noqa: E402


def _frames(seed: int, n_universe: int, n_grants: int):
    rng = np.random.default_rng(seed)
    zips = [f"{z:05d}" for z in rng.integers(10000, 10040, 40)]
    names = [f"org {i}" for i in range(60)]

    def frame(n):
        df = pd.DataFrame({
            "clean_zip": rng.choice(zips, n),
            "full_name": rng.choice(names, n).astype(object),
            # a quarter without an address: left out of the zip block
            "compare_addr": np.where(rng.random(n) < 0.25, "", "1 main st"),
        })
        # a few missing names: left out of the name block
        df.loc[rng.random(n) < 0.03, "full_name"] = np.nan
        return df

    return frame(n_universe), frame(n_grants)


def _recordlinkage_pairs(universe_df: pd.DataFrame, grants_df: pd.DataFrame) -> pd.MultiIndex:
    """The three lines candidate_pairs used until 2026-10-27, on the frames
    as candidate_pairs prepares them."""
    indexer = recordlinkage.Index()
    indexer.block(left_on=["block_zip"], right_on=["block_zip"])
    indexer.block(left_on=["full_name"], right_on=["full_name"])
    return indexer.index(universe_df, grants_df)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_same_pairs_same_order_as_recordlinkage(seed):
    universe_df, grants_df = _frames(seed, 300, 2000)
    ours = candidate_pairs(universe_df, grants_df)
    # candidate_pairs added block_zip and made clean_zip categorical on both
    # frames, exactly what the old code handed recordlinkage.
    theirs = _recordlinkage_pairs(universe_df, grants_df)
    assert len(ours) > 1000
    assert ours.equals(theirs)
    assert list(ours.get_level_values(0)[:5]) == list(theirs.get_level_values(0)[:5])


def test_no_address_rows_pair_by_name_only():
    universe_df, grants_df = _frames(3, 50, 200)
    grants_df["compare_addr"] = ""  # no grant has an address: the zip block is empty
    ours = candidate_pairs(universe_df, grants_df)
    theirs = _recordlinkage_pairs(universe_df, grants_df)
    assert set(ours) == set(theirs)
    joined = pd.DataFrame({"u": ours.get_level_values(0), "g": ours.get_level_values(1)})
    assert (universe_df.loc[joined["u"], "full_name"].to_numpy() == grants_df.loc[joined["g"], "full_name"].to_numpy()).all()
