import numpy as np
import pandas as pd

from themegraph.graph.candidates import detect, midrank_pct, write_ledger


def _track(tid, snaps, issuers_at, shares):
    """One track: k issuers at each snapshot sharing the topic's passages."""
    rows = []
    for t, k, sh in zip(snaps, issuers_at, shares, strict=True):
        for j in range(k):
            rows.append({"t": t, "track_id": tid, "symbol": f"S{j}",
                         "n_passages": max(1, int(round(sh * 100 / k))), "exposure": 0.2})
    return pd.DataFrame(rows)


SNAPS = list(pd.date_range("2016-01-31", periods=60, freq="ME", tz="UTC"))
FLAT = 36  # quiet history long enough to fill the new-high lookback
GROW_K = [3] * FLAT + [min(8, 3 + i // 3) for i in range(60 - FLAT)]
GROW_SH = [0.02] * FLAT + list(np.linspace(0.02, 0.30, 60 - FLAT))
TOTALS = pd.Series(100, index=SNAPS)


def test_growing_topic_flagged_sawtooth_not(cfg):
    saw = [0.10, 0.14, 0.18] * 20
    ex = pd.concat([_track(1, SNAPS, GROW_K, GROW_SH),
                    _track(2, SNAPS, [3, 4, 5] * 20, saw)])
    led = detect(ex, SNAPS, cfg.candidates, {}, {}, window_totals=TOTALS)
    assert set(led["track_id"]) == {1}
    assert led["t_first_flag"].iloc[0] > SNAPS[FLAT]


def test_no_flag_without_full_lookback_history(cfg):
    early = list(np.linspace(0.02, 0.30, 20)) + [0.30] * 40
    k = [min(8, 3 + i // 3) for i in range(20)] + [8] * 40
    led = detect(_track(1, SNAPS, k, early), SNAPS, cfg.candidates, {}, {},
                 window_totals=TOTALS)
    assert led.empty  # grew before 24 snapshots of history existed
    led = detect(_track(1, SNAPS, GROW_K, GROW_SH), SNAPS, cfg.candidates, {}, {},
                 window_totals=TOTALS, warmup_until=SNAPS[30])
    assert led.empty  # lookback would reach into the warm-up


def test_spread_required(cfg):
    led = detect(_track(1, SNAPS, [4] * 60, GROW_SH), SNAPS, cfg.candidates, {}, {},
                 window_totals=TOTALS)
    assert led.empty  # share grew but no new issuers joined


def test_split_fragment_judged_against_parent_history(cfg):
    parent = _track(1, SNAPS, [8] * 60, [0.40] * 60)
    # The child appears at FLAT, split from the parent, and grows to 0.30 < 0.40.
    child = _track(2, SNAPS[FLAT - 9:], GROW_K[FLAT - 9:], GROW_SH[FLAT - 9:])
    ex = pd.concat([parent, child])
    assert set(detect(ex, SNAPS, cfg.candidates, {}, {}, window_totals=TOTALS)["track_id"]) \
        == {2}
    assert detect(ex, SNAPS, cfg.candidates, {}, {}, window_totals=TOTALS,
                  parents={2: 1}).empty


def test_midrank_and_ledger_hash_stable(tmp_path):
    assert midrank_pct(np.array([1.0, 1.0]), 1.0) == 0.5
    led = pd.DataFrame({"candidate_id": ["c1"], "track_id": [1],
                        "t": [pd.Timestamp("2020-01-31", tz="UTC")],
                        "t_first_flag": [pd.Timestamp("2020-01-31", tz="UTC")],
                        "issuers": [["A", "B"]]})
    assert write_ledger(led, tmp_path / "a") == write_ledger(led, tmp_path / "b")
    write_ledger(pd.DataFrame(), tmp_path / "empty")


def test_recurring_topic_not_flagged_emerging_one_is(cfg):
    recurring = [0.30] * FLAT + list(np.linspace(0.10, 0.30, 60 - FLAT))  # old level
    ex = pd.concat([_track(1, SNAPS, GROW_K, recurring), _track(2, SNAPS, GROW_K, GROW_SH)])
    led = detect(ex, SNAPS, cfg.candidates, {}, {}, window_totals=TOTALS)
    assert set(led["track_id"]) == {2}


def _breadth(snaps, issuers_at, shares):
    rows = []
    for t, k, sh in zip(snaps, issuers_at, shares, strict=True):
        for j in range(k):
            rows.append({"t": t, "track_id": 1, "symbol": f"S{j}",
                         "n_passages": max(1, int(sh * 100 / k)), "exposure": 0.2})
    return pd.DataFrame(rows)


def test_generic_means_broad_before_growth_not_broad_at_flag(cfg):
    snaps, totals = SNAPS, TOTALS
    active = pd.Series(20, index=snaps)
    # Spreads from 2 to all 20 issuers within months (AI in 2023): emerging.
    k = [1] * FLAT + [min(20, 2 + 3 * i) for i in range(60 - FLAT)]
    sh = [0.0] * FLAT + [min(0.3, 0.02 + 0.02 * i) for i in range(60 - FLAT)]
    fast = _breadth(snaps, k, sh)
    fast = fast.loc[fast["t"] >= snaps[FLAT]]
    assert not detect(fast, snaps, cfg.candidates, {}, {}, window_totals=totals,
                      active_issuers=active).empty
    # Discussed by 15 of 20 issuers throughout, share drifting up: generic.
    always = _breadth(snaps, [15] * 60, GROW_SH)
    assert detect(always, snaps, cfg.candidates, {}, {}, window_totals=totals,
                  active_issuers=active).empty


def test_thin_coverage_does_not_make_detection_impossible(cfg):
    snaps = SNAPS
    k = [1] * FLAT + [min(4, 1 + i // 6) for i in range(60 - FLAT)]  # up to 4 of 5 issuers
    sh = [0.01] * FLAT + list(np.linspace(0.02, 0.3, 60 - FLAT))
    led = detect(_breadth(snaps, k, sh), snaps, cfg.candidates, {}, {},
                 window_totals=pd.Series(100, index=snaps),
                 active_issuers=pd.Series(5, index=snaps))
    assert not led.empty  # min_issuers=3 with a 50% cap of 5 would be unsatisfiable
