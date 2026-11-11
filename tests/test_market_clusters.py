"""Market clusters: the stock-return HAC cut where shuffled data stop producing
links that strong (monitor/market_clusters.py)."""

import json

import numpy as np
import pandas as pd

from themegraph.config import Issuer, UniverseConfig
from themegraph.monitor.baskets import PriceBook
from themegraph.monitor.market_clusters import market_clusters


def _book(T=200, seed=7):
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range("2021-01-04", periods=T, tz="UTC") + pd.Timedelta(hours=22)
    m = rng.normal(0, 0.01, T)
    fa, fb = rng.normal(0, 0.012, T), rng.normal(0, 0.012, T)
    px, seg = {"SPY": 100 * np.cumprod(1 + m)}, {}
    for i in range(5):
        px[f"A{i}"] = 100 * np.cumprod(1 + m + fa + rng.normal(0, 0.01, T))
        px[f"B{i}"] = 100 * np.cumprod(1 + 1.3 * m + fb + rng.normal(0, 0.01, T))
        seg[f"A{i}"], seg[f"B{i}"] = "a", "b"
    for i in range(20):  # the market only: nothing beyond it
        px[f"X{i}"] = 100 * np.cumprod(1 + rng.uniform(.5, 1.5) * m + rng.normal(0, 0.015, T))
        seg[f"X{i}"] = "x"
    df = pd.DataFrame(px, index=cal)
    u = UniverseConfig(issuers=[Issuer(symbol=s, segment=v, role="core") for s, v in seg.items()],
                       benchmarks=["SPY"], sectors={"a": "A", "b": "B", "x": "X"})
    return PriceBook(df, "SPY", {c: "USD" for c in df}, None, None, 5), u


def test_groups_that_trade_together_are_found_and_the_rest_left_out():
    book, u = _book()
    out = market_clusters(book, u, {"th-a": ["A0", "A1", "X3"], "th-x": ["X1", "X2"]},
                          126, 0.9, "SPY")
    assert out["available"] and out["companies"] == 30
    got = sorted(sorted(c["members"]) for c in out["clusters"])
    assert got == [[f"A{i}" for i in range(5)], [f"B{i}" for i in range(5)]]
    a = next(c for c in out["clusters"] if "A0" in c["members"])
    assert a["themes"] == [{"id": "th-a", "shared": 2}] and a["sectors"] == {"A": 5}
    assert a["avg_corr"] > out["cut"]["corr"] > 0
    assert 0 < out["cut"]["corr"] < 0.5
    # Every merge inside a cluster is above the cut; the tree covers every company.
    assert len(out["merges"]) == out["companies"] - 1
    assert sum(lf["cluster"] is None for lf in out["leaves"]) == 20
    json.dumps(out, allow_nan=False)  # strict JSON for the release
    assert market_clusters(book, u, {}, 126, 0.9, "SPY") == {**out, "clusters": [
        {**c, "themes": []} for c in out["clusters"]]}  # deterministic


def test_no_co_movement_gives_no_clusters():
    rng = np.random.default_rng(3)
    T = 200
    cal = pd.bdate_range("2021-01-04", periods=T, tz="UTC") + pd.Timedelta(hours=22)
    m = rng.normal(0, 0.01, T)
    px = {"SPY": 100 * np.cumprod(1 + m)}
    px |= {f"X{i}": 100 * np.cumprod(1 + m + rng.normal(0, 0.015, T)) for i in range(25)}
    u = UniverseConfig(issuers=[Issuer(symbol=f"X{i}", segment="x", role="core")
                                for i in range(25)], benchmarks=["SPY"], sectors={"x": "X"})
    book = PriceBook(pd.DataFrame(px, index=cal), "SPY", {c: "USD" for c in px}, None, None, 5)
    out = market_clusters(book, u, {}, 126, 0.9, "SPY")
    assert out["available"] and out["clusters"] == []


def test_not_enough_history_says_so():
    book, u = _book(T=60)
    out = market_clusters(book, u, {}, 126, 0.9, "SPY")
    assert out == {"available": False, "index": "SPY", "window_sessions": 126,
                   "note": "not enough price history"}
