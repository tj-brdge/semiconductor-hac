import numpy as np
import pandas as pd

from themegraph.market.hac import (
    hac_affinity,
    market_snapshot,
    mean_pair_affinity,
    shrunk_correlation,
)
from themegraph.market.returns import window_residuals


def _rets(n=200, seed=0):
    rng = np.random.default_rng(seed)
    mkt = rng.normal(0, 0.01, n)
    g1, g2 = rng.normal(0, 0.01, n), rng.normal(0, 0.01, n)
    cols = {"SPY": mkt}
    for i in range(4):
        cols[f"A{i}"] = mkt + g1 + rng.normal(0, 0.005, n)
        cols[f"B{i}"] = mkt + g2 + rng.normal(0, 0.005, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(cols, index=idx)


def test_residuals_orthogonal_to_benchmark():
    r = _rets()
    res = window_residuals(r, ["SPY"])
    assert "SPY" not in res
    assert abs(np.corrcoef(res["A0"], r["SPY"])[0, 1]) < 1e-8


def test_affinity_recovers_groups():
    r = _rets()
    corr = shrunk_correlation(window_residuals(r, ["SPY"]))
    aff, _ = hac_affinity(corr)
    assert np.allclose(aff, aff.T) and np.allclose(np.diag(aff), 1)
    within = mean_pair_affinity(aff, ["A0", "A1", "A2", "A3"])
    across = mean_pair_affinity(aff, ["A0", "B0", "A1", "B1"])
    assert within > across


def test_snapshot_ignores_future_sessions(cfg):
    r = _rets()
    t = r.index[150]
    a = market_snapshot(r, t, 63, cfg.market, [c for c in r if c != "SPY"], ["SPY"])
    noisy = r.copy()
    noisy.iloc[151:] = np.random.default_rng(9).normal(0, 0.05, noisy.iloc[151:].shape)
    b = market_snapshot(noisy, t, 63, cfg.market, [c for c in r if c != "SPY"], ["SPY"])
    pd.testing.assert_frame_equal(a["affinity"], b["affinity"])
