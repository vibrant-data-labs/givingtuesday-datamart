"""The matcher's chunk workers must produce what the serial loop produces:
the same scored rows per chunk, each chunk's checkpoint written."""
import concurrent.futures as cf
import multiprocessing

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("recordlinkage")

from givingtuesday_datamart import grant_matching as gm  # noqa: E402
from givingtuesday_datamart._internal import parquet_cache as pqc  # noqa: E402


def _frames(seed: int = 0):
    rng = np.random.default_rng(seed)
    names = [f"org {i}" for i in range(40)]
    zips = [f"{z:05d}" for z in rng.integers(10000, 10010, 10)]

    def frame(n):
        return pd.DataFrame({
            "clean_zip": rng.choice(zips, n),
            "full_name": rng.choice(names, n).astype(object),
            "compare_name": rng.choice(names, n).astype(object),
            "compare_addr": np.where(rng.random(n) < 0.3, "", "1 main st"),
            "compare_state": rng.choice(["NY", "CA", "TX"], n),
            "filerein_key": [f"{i:09d}" for i in range(n)],
        })

    return frame(60), frame(400)


def test_pool_matches_serial_loop(tmp_path):
    universe_df, grants_df = _frames()
    pairs = gm.candidate_pairs(universe_df, grants_df)
    chunk_size = 500
    total = len(pairs)
    assert total > chunk_size * 3
    n_chunks = (total + chunk_size - 1) // chunk_size

    serial = {}
    for idx in range(n_chunks):
        chunk = pairs[idx * chunk_size: min((idx + 1) * chunk_size, total)]
        serial[idx] = gm.score_pairs(chunk, universe_df, grants_df)

    gm._POOL_STATE.update(chunk_size=chunk_size, total_pairs=total, candidate_links=pairs,
                          universe_df=universe_df, grants_df=grants_df, uri_base=str(tmp_path))
    try:
        with cf.ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("fork")) as pool:
            pooled = dict(pool.map(gm._chunk_job, range(n_chunks), chunksize=2))
    finally:
        gm._POOL_STATE.clear()

    assert sorted(pooled) == list(range(n_chunks))
    for idx in range(n_chunks):
        pd.testing.assert_frame_equal(pooled[idx].reset_index(drop=True), serial[idx].reset_index(drop=True))
        written = pqc.read_dataframe(tmp_path / f"chunk_{idx:05d}.parquet")
        assert len(written) == len(serial[idx])
