""""Trading together" must not turn rounding noise or ties into a verdict
(monitor/comovement.py; market/hac.single_index_shrinkage)."""

import zlib

import numpy as np
import pandas as pd
import pytest

from themegraph.config import Issuer, UniverseConfig
from themegraph.market.hac import single_index_shrinkage
from themegraph.monitor.baskets import PriceBook
from themegraph.monitor.comovement import _measure, trading_together
from themegraph.monitor.export import together_sentence

SEED = zlib.crc32(b"th-00001")


def _full_shrinkage_case():
    """The reported reproduction: 76 stocks driven by the market plus independent
    noise, 126 sessions. Shrinkage reaches 1."""
    rng = np.random.default_rng(25)
    m = rng.normal(0, .01, 126)
    X = m[:, None] * rng.uniform(.5, 1.5, 76) + rng.normal(0, .015, (126, 76))
    cols = [f"S{i}" for i in range(76)]
    return pd.DataFrame(X, columns=cols), pd.Series(m), cols


def _win(est, cols):
    return {"start": "a", "end": "b", "pool": cols, "corr": est["resid_corr"],
            "shrinkage": est["shrinkage"]}


def test_full_shrinkage_leaves_exact_zeros_and_no_verdict():
    R, m, cols = _full_shrinkage_case()
    est = single_index_shrinkage(R, m)
    assert est["shrinkage"] == 1.0
    off = ~np.eye(76, dtype=bool)
    # Before: the subtraction left |values| up to ~2e-16, which were then ranked.
    assert np.all(est["excess_corr"].to_numpy()[off] == 0.0)
    out = _measure(_win(est, cols), cols[:4], {s: "tech" for s in cols}, 1000, SEED)
    assert out["informative"] is False and out["percentile"] is None
    assert out["verdict"] is None and out["excess_corr"] == 0.0
    assert "nothing beyond the market" in out["note"]
    assert "cannot be judged" in together_sentence(out)


def test_near_full_shrinkage_ranks_like_any_other_shrinkage():
    """The beyond-index correlations are (1 - shrinkage) times the sample ones, so
    the ranking, and the percentile, do not depend on how close shrinkage is to 1."""
    rng = np.random.default_rng(4)
    m = rng.normal(0, .01, 200)
    g = rng.normal(0, .01, 200)
    X = np.column_stack([m + g + rng.normal(0, .01, 200) for _ in range(4)]
                        + [m + rng.normal(0, .012, 200) for _ in range(30)])
    cols = [f"G{i}" for i in range(4)] + [f"X{i}" for i in range(30)]
    est = single_index_shrinkage(pd.DataFrame(X, columns=cols), pd.Series(m))
    seg = {s: "a" for s in cols}
    outs = [_measure({**_win(est, cols), "shrinkage": s}, cols[:4], seg, 500, SEED)
            for s in (0.2, 0.99, 1 - 1e-7)]
    assert all(o["informative"] for o in outs)
    assert {o["percentile"] for o in outs} == {outs[0]["percentile"]}
    assert outs[0]["verdict"] == "together"


def test_ties_count_as_ties_and_all_ties_say_nothing():
    cols = [f"S{i}" for i in range(12)]
    a = np.full((12, 12), 0.3)
    np.fill_diagonal(a, 1.0)
    flat = pd.DataFrame(a, index=cols, columns=cols)
    seg = {s: "a" for s in cols}
    w = {"start": "a", "end": "b", "pool": cols, "corr": flat, "shrinkage": 0.5}
    out = _measure(w, cols[:3], seg, 200, SEED)
    assert out["informative"] is False and out["ties"] == 200 and out["percentile"] is None
    # Partial ties: half the pool shares the members' level, the rest is lower.
    a[6:, :] = a[:, 6:] = 0.1
    np.fill_diagonal(a, 1.0)
    part = pd.DataFrame(a, index=cols, columns=cols)
    out = _measure({**w, "corr": part}, cols[:3], seg, 400, SEED)
    assert out["informative"] and out["ties"] > 0 and out["below"] > 0
    assert out["below"] + out["ties"] + out["above"] == 400
    assert out["percentile"] == pytest.approx((out["below"] + out["ties"] / 2) / 400, abs=1e-3)
    assert out["below"] < 400  # so the text must not say "all"
    assert f"more than {out['below']:,} of 400" in together_sentence(out)
    assert f"equal to {out['ties']:,}" in together_sentence(out)


def _book(index_px=None, flat_member=False, T=300):
    rng = np.random.default_rng(3)
    cal = pd.bdate_range("2021-01-04", periods=T, tz="UTC") + pd.Timedelta(hours=22)
    m = rng.normal(0, 0.01, T)
    g = rng.normal(0, 0.008, T)
    px = {"SPY": 100 * np.cumprod(1 + m) if index_px is None else index_px(T)}
    seg = {}
    for i in range(5):
        px[f"G{i}"] = 100 * np.cumprod(1 + m + g + rng.normal(0, 0.012, T))
        seg[f"G{i}"] = "a"
    if flat_member:  # moves exactly with the index: nothing beyond it
        px["G5"] = 50 * np.cumprod(1 + 1.5 * m)
        seg["G5"] = "a"
    for i in range(20):
        px[f"X{i}"] = 100 * np.cumprod(1 + rng.uniform(.6, 1.6) * m + rng.normal(0, 0.014, T))
        seg[f"X{i}"] = "a"
    df = pd.DataFrame(px, index=cal)
    u = UniverseConfig(issuers=[Issuer(symbol=s, segment=v, role="core") for s, v in seg.items()],
                       benchmarks=["SPY"], sectors={"a": "A"})
    return PriceBook(df, "SPY", {c: "USD" for c in df}, None, None, 5), u


def test_index_without_variance_gives_no_verdict():
    book, u = _book(index_px=lambda T: np.full(T, 100.0))
    out = trading_together(book, u, {"th": [f"G{i}" for i in range(5)]}, 126, 0.9, 200, "SPY")
    now = out["th"]["now"]
    assert now["informative"] is False and now["percentile"] is None
    assert "did not move" in now["note"]
    with pytest.raises(ValueError):
        single_index_shrinkage(pd.DataFrame({"a": [.01, -.02, .03], "b": [0, .01, .02],
                                             "c": [.02, 0, -.01]}), pd.Series([0.0] * 3))


def test_company_the_index_explains_completely_is_left_out():
    book, u = _book(flat_member=True)
    members = [f"G{i}" for i in range(6)]
    out = trading_together(book, u, {"th": members}, 126, 0.9, 300, "SPY")["th"]["now"]
    assert out["left_out"] == ["G5"] and out["companies"] == 5
    assert out["informative"] and out["verdict"] == "together"
