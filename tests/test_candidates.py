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


def test_recurring_topic_not_flagged_emerging_one_is(cfg):
    snaps = list(pd.date_range("2018-01-31", periods=48, freq="ME", tz="UTC"))
    recurring = [0.30] * 20 + list(np.linspace(0.10, 0.30, 28))  # back to an old level
    emerging = [0.02] * 20 + list(np.linspace(0.02, 0.30, 28))
    ex = _exposure({1: recurring, 2: emerging}, snaps)
    led = detect(ex, snaps, cfg.candidates, {}, {}, window_totals=pd.Series(100, index=snaps))
    assert set(led["track_id"]) == {2}


def _breadth(snaps, issuers_at, shares):
    rows = []
    for t, k, sh in zip(snaps, issuers_at, shares, strict=True):
        for j in range(k):
            rows.append({"t": t, "track_id": 1, "symbol": f"S{j}",
                         "n_passages": max(1, int(sh * 100 / k)), "exposure": 0.2})
    return pd.DataFrame(rows)


def test_generic_means_broad_before_growth_not_broad_at_flag(cfg):
    snaps = list(pd.date_range("2018-01-31", periods=48, freq="ME", tz="UTC"))
    active = pd.Series(20, index=snaps)
    totals = pd.Series(100, index=snaps)
    # Spreads from 2 to all 20 issuers within three months (AI in 2023): emerging.
    k = [1] * 20 + [min(20, 2 + 6 * i) for i in range(28)]
    sh = [0.0] * 20 + [min(0.3, 0.02 + 0.03 * i) for i in range(28)]
    fast = _breadth(snaps, k, sh).query("n_passages > 0")
    fast = fast.loc[fast["t"] >= snaps[20]]
    assert not detect(fast, snaps, cfg.candidates, {}, {}, window_totals=totals,
                      active_issuers=active).empty
    # Discussed by 15 of 20 issuers throughout, share drifting up: generic.
    always = _breadth(snaps, [15] * 48, [0.02] * 20 + list(np.linspace(0.02, 0.3, 28)))
    assert detect(always, snaps, cfg.candidates, {}, {}, window_totals=totals,
                  active_issuers=active).empty


def test_thin_coverage_does_not_make_detection_impossible(cfg):
    snaps = list(pd.date_range("2018-01-31", periods=48, freq="ME", tz="UTC"))
    k = [1] * 20 + [min(4, 1 + i // 6) for i in range(28)]  # up to 4 of 5 issuers
    sh = [0.01] * 20 + list(np.linspace(0.02, 0.3, 28))
    led = detect(_breadth(snaps, k, sh), snaps, cfg.candidates, {}, {},
                 window_totals=pd.Series(100, index=snaps),
                 active_issuers=pd.Series(5, index=snaps))
    assert not led.empty  # min_issuers=3 with a 50% cap of 5 would be unsatisfiable
