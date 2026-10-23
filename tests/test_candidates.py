import numpy as np
import pandas as pd

from themegraph.graph.candidates import detect, midrank_pct, write_ledger


def _exposure(shares: dict[int, list[float]], snaps, issuers=("A", "B", "C")):
    rows = []
    for tid, series in shares.items():
        for t, sh in zip(snaps, series, strict=True):
            for s in issuers:
                rows.append({"t": t, "track_id": tid, "symbol": s,
                             "n_passages": int(round(sh * 100)), "exposure": 0.2})
    return pd.DataFrame(rows)


def test_growing_topic_flagged_sawtooth_not(cfg):
    snaps = list(pd.date_range("2020-01-31", periods=18, freq="ME", tz="UTC"))
    grow = list(np.linspace(0.02, 0.30, 18))
    saw = [0.10, 0.14, 0.18] * 6
    ex = _exposure({1: grow, 2: saw}, snaps)
    totals = pd.Series(100, index=snaps)
    led = detect(ex, snaps, cfg.candidates, {}, {}, window_totals=totals)
    assert set(led["track_id"]) == {1}
    assert led["t_first_flag"].iloc[0] == snaps[8]


def test_warmup_suppresses_early_flags(cfg):
    snaps = list(pd.date_range("2020-01-31", periods=18, freq="ME", tz="UTC"))
    ex = _exposure({1: list(np.linspace(0.02, 0.30, 18))}, snaps)
    totals = pd.Series(100, index=snaps)
    led = detect(ex, snaps, cfg.candidates, {}, {}, window_totals=totals,
                 warmup_until=snaps[5])
    assert led["t"].min() > snaps[8]


def test_midrank_and_ledger_hash_stable(tmp_path):
    assert midrank_pct(np.array([1.0, 1.0]), 1.0) == 0.5
    led = pd.DataFrame({"candidate_id": ["c1"], "track_id": [1],
                        "t": [pd.Timestamp("2020-01-31", tz="UTC")],
                        "t_first_flag": [pd.Timestamp("2020-01-31", tz="UTC")],
                        "issuers": [["A", "B"]]})
    assert write_ledger(led, tmp_path / "a") == write_ledger(led, tmp_path / "b")
    write_ledger(pd.DataFrame(), tmp_path / "empty")


def test_recurring_and_generic_topics_not_flagged(cfg):
    snaps = list(pd.date_range("2018-01-31", periods=48, freq="ME", tz="UTC"))
    # recurring: high, dips, then grows back to its old level
    recurring = [0.30] * 20 + list(np.linspace(0.10, 0.30, 28))
    emerging = [0.02] * 20 + list(np.linspace(0.02, 0.30, 28))
    ex = _exposure({1: recurring, 2: emerging}, snaps)
    totals = pd.Series(100, index=snaps)
    led = detect(ex, snaps, cfg.candidates, {}, {}, window_totals=totals)
    assert set(led["track_id"]) == {2}
    # the same emerging topic, but discussed by every issuer with text: generic
    led = detect(ex, snaps, cfg.candidates, {}, {}, window_totals=totals,
                 active_issuers=pd.Series(4, index=snaps))
    assert led.empty
