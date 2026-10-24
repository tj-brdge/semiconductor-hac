"""Outcome accounting: entry/exit conventions, constituent statuses, null baskets,
dependence groups, and snapshot availability."""

import datetime as dt

import numpy as np
import pandas as pd

from themegraph.config import Successor, load_pipeline
from themegraph.eval.comovement import (
    dependence_groups,
    matched_null_sets,
    summarise_percentiles,
)
from themegraph.eval.returns import holding_return
from themegraph.market.hac import market_snapshot

IDX = pd.bdate_range("2020-01-28", periods=10).tz_localize("UTC") + pd.Timedelta(hours=22)
T = pd.Timestamp("2020-01-31 23:59:59", tz="UTC")  # 4th session; entry is the 5th
END = IDX[-1]


def test_move_into_entry_close_is_excluded():
    px = pd.DataFrame({"X": [100, 100, 100, 100, 110, 110, 110, 110, 110, 110]}, index=IDX,
                      dtype=float)
    r = holding_return(px, ["X"], T, 3, END)
    assert r["status"] == "ok" and r["value"] == 0.0
    assert r["entry"] == IDX[4] and r["exit"] == IDX[7]


def test_equal_weight_buy_and_hold_simple_return():
    px = pd.DataFrame({"A": [1, 1, 1, 1, 100, 100, 150], "B": [1, 1, 1, 1, 50, 50, 25]},
                      index=IDX[:7], dtype=float)
    r = holding_return(px, ["A", "B"], T, 2, END)
    assert np.isclose(r["value"], (0.5 + -0.5) / 2)  # mean of simple returns, not logs


def test_constituent_statuses_and_coverage():
    base = [100.0] * 4 + [100, 101, 102, 103, 104, 105]
    px = pd.DataFrame({"OK": base, "DL": base[:6] + [np.nan] * 4,
                       "NEW": [np.nan] * 10, "GAP": base[:8] + [np.nan, 105]}, index=IDX)
    r = holding_return(px, ["OK", "DL", "GAP"], T, 4, END, {"DL": dt.date(2020, 2, 5)})
    assert r["statuses"] == {"OK": "complete", "DL": "delisted", "GAP": "stale_exit"}
    assert np.isclose(r["value"], np.mean([104 / 100 - 1, 101 / 100 - 1, 103 / 100 - 1]))
    r = holding_return(px, ["OK", "NEW"], T, 4, END)
    assert r["statuses"]["NEW"] == "not_listed" and r["status"] == "insufficient_coverage"
    assert np.isnan(r["value"])  # membership is fixed: a 50%-covered basket is not scored


def test_incomplete_horizon_is_not_truncated():
    px = pd.DataFrame({"X": np.arange(10.0) + 100}, index=IDX)
    assert holding_return(px, ["X"], T, 5, IDX[7])["status"] == "incomplete_horizon"


def test_null_sets_unique_exclude_members_and_report_support():
    segs = {"A1": "a", "A2": "a", "B1": "b", "B2": "b", "C1": "c", "C2": "c"}
    rng = np.random.default_rng(0)
    sets, diag = matched_null_sets(["A1", "B1", "B2"], list(segs), segs, rng, 200)
    assert diag["supported"] and all(len(set(s)) == 3 for s in sets)
    assert all(not {"A1", "B1", "B2"} & set(s) for s in sets)
    assert np.isclose(diag["same_segment_share"], 1 / 3)  # only A has a spare peer
    sets, diag = matched_null_sets(["A1", "A2", "B1", "B2"], list(segs), segs, rng, 10)
    assert sets == [] and not diag["supported"]  # two non-members for four slots


def test_dependence_groups_pool_overlapping_episodes():
    res = pd.DataFrame({
        "t": pd.to_datetime(["2020-01-31", "2020-03-31", "2023-01-31"], utc=True),
        "h": 252, "issuers": [["A", "B"], ["B", "C"], ["A", "B"]],
        "null_pct": [0.9, 0.8, 0.2]})
    g = dependence_groups(res)
    assert g.iloc[0] == g.iloc[1] and g.iloc[2] != g.iloc[0]
    s = summarise_percentiles(res, 200, 0)[252]
    assert s["n_episodes"] == 3 and s["n_groups"] == 2
    assert np.isclose(s["group_mean_pct"], (0.85 + 0.2) / 2)


def test_snapshot_ignores_prices_published_after_it():
    cfg = load_pipeline().market
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2019-06-03", "2020-02-14").tz_localize("UTC") + pd.Timedelta(hours=22)
    rets = pd.DataFrame(rng.normal(0, 0.01, (len(idx), 6)), index=idx,
                        columns=["SPY", "A", "B", "C", "D", "E"])
    known = pd.DataFrame({c: idx for c in rets.columns}, index=idx)
    t = pd.Timestamp("2020-01-31 23:59:59", tz="UTC")
    late = (idx > pd.Timestamp("2020-01-27", tz="UTC")) & (idx <= t)
    known.loc[late] = pd.Timestamp("2020-02-05", tz="UTC")  # published after the snapshot
    syms = ["A", "B", "C", "D", "E"]
    full = market_snapshot(rets, t, 63, cfg, syms, ["SPY"], known=known)
    asof = market_snapshot(rets.loc[~late], t, 63, cfg, syms, ["SPY"])
    pd.testing.assert_frame_equal(full["affinity"], asof["affinity"])


def test_correlation_survives_staggered_gaps():
    cfg = load_pipeline().market
    rng = np.random.default_rng(4)
    idx = pd.bdate_range("2019-01-01", periods=80).tz_localize("UTC")
    rets = pd.DataFrame(rng.normal(0, 0.01, (80, 5)), index=idx, columns=list("SABCD"))
    for i, c in enumerate("ABCD"):  # every row misses one column; each column 75% present
        rets.iloc[i::4, rets.columns.get_loc(c)] = np.nan
    assert rets[list("ABCD")].dropna().empty  # the old row-dropping had nothing left
    snap = market_snapshot(rets, idx[-1], 60, cfg.model_copy(
        update={"min_observations_share": 0.7}), list("ABCD"), ["S"])
    assert snap["affinity"] is not None


def _flat_panel(n=30, members=("A", "B", "C", "D", "E", "ACQ")):
    idx = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC") + pd.Timedelta(hours=22)
    return pd.DataFrame({m: 100.0 for m in members}, index=idx)


def test_missing_exit_price_makes_basket_unvalued_not_ok():
    """Four of five members +10%, one with no exit price: not reported as +10%."""
    panel = _flat_panel()
    t = panel.index[0]
    panel.iloc[-5:, :4] = 110.0
    panel.loc[panel.index[10]:, "E"] = np.nan  # quotes stop, no delisting record
    b = holding_return(panel, ["A", "B", "C", "D", "E"], t, 25, panel.index[-1])
    assert b["status"] == "unvalued_position" and np.isnan(b["value"])
    assert b["statuses"]["E"] == "unvalued" and b["n_unvalued"] == 1
    assert np.isclose(b["worst_case"], (4 * 0.10 - 1.0) / 5)  # lower bound only


def test_acquired_member_converts_into_acquirer_shares():
    panel = _flat_panel()
    t = panel.index[0]
    panel.loc[panel.index[10]:, "E"] = np.nan
    panel.loc[panel.index[10]:, "ACQ"] = 150.0
    terms = {"E": Successor(symbol="ACQ", shares=0.5, cash=20.0, source="test")}
    b = holding_return(panel, ["A", "E"], t, 25, panel.index[-1],
                       {"E": panel.index[10].date()}, terms)
    assert b["statuses"]["E"] == "converted" and b["status"] == "ok"
    assert np.isclose(b["value"], (0.0 + (0.5 * 150 + 20) / 100 - 1) / 2)
    # Without recorded terms: last close held as cash, flagged as an assumption.
    b = holding_return(panel, ["A", "E"], t, 25, panel.index[-1], {"E": panel.index[10].date()})
    assert b["statuses"]["E"] == "delisted" and b["n_delisted_assumed"] == 1
