"""Outcome accounting: entry/exit conventions, constituent statuses, null baskets,
dependence groups, and snapshot availability."""

import datetime as dt
import json

import numpy as np
import pandas as pd

from themegraph.config import Successor, load_pipeline
from themegraph.eval.comovement import (
    dependence_groups,
    matched_null_sets,
    summarise_percentiles,
)
from themegraph.eval.returns import MAX_CLOSURE_SESSIONS, holding_return, venue_sessions
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


# -- horizon calendar and venues ---------------------------------------------------

def _sessions(start, end, closed=()):
    d = pd.bdate_range(start, end)
    d = d[~d.strftime("%Y-%m-%d").isin(closed)]
    return d.tz_localize("UTC") + pd.Timedelta(hours=22)


US = _sessions("2019-08-26", "2019-10-31", closed=("2019-09-02",))          # Labor Day
KR = _sessions("2019-08-26", "2019-10-31", closed=("2019-09-12", "2019-09-13"))  # Chuseok
ALL = US.union(KR)
VENUES = {"A": "USD", "B": "USD", "C": "USD", "SPY": "USD", "K": "KRW", "K2": "KRW"}


def _mixed(prices=None):
    panel = pd.DataFrame(index=ALL)
    for s in ("A", "B", "C", "SPY"):
        panel[s] = pd.Series(np.linspace(100, 120, len(US)), index=US)
    for s in ("K", "K2"):
        panel[s] = pd.Series(np.linspace(50, 60, len(KR)), index=KR)
    return panel


def test_us_basket_enters_on_the_next_us_session_not_a_foreign_only_day():
    panel = _mixed()
    t = pd.Timestamp("2019-08-31 23:59:59", tz="UTC")
    old = holding_return(panel, ["A", "B", "C"], t, 5, ALL[-1])  # union calendar
    assert old["status"] == "insufficient_coverage"  # the reported failure
    b = holding_return(panel, ["A", "B", "C"], t, 5, ALL[-1], calendar=US, venues=VENUES)
    assert b["entry"].date() == dt.date(2019, 9, 3) and b["status"] == "ok"
    assert b["exit"] == US[US.get_loc(b["entry"]) + 5]  # h counts US sessions only
    assert np.isclose(b["value"], panel.at[b["exit"], "A"] / panel.at[b["entry"], "A"] - 1)


def test_foreign_member_closed_at_entry_or_exit_uses_its_own_sessions():
    panel = _mixed()
    t = pd.Timestamp("2019-09-11 23:59:59", tz="UTC")  # entry 09-12: Korea closed
    b = holding_return(panel, ["A", "K"], t, 1, ALL[-1], calendar=US, venues=VENUES)
    assert b["entry"].date() == dt.date(2019, 9, 12) and b["exit"].date() == dt.date(2019, 9, 13)
    assert b["statuses"]["K"] == "no_entry"  # no Korean session inside a 1-session hold
    b = holding_return(panel, ["A", "K"], t, 3, ALL[-1], calendar=US, venues=VENUES)
    k0 = panel.at[KR[KR > b["entry"]][0], "K"]  # bought at the next Korean session (09-16)
    assert b["statuses"]["K"] == "complete" and b["status"] == "ok"
    assert np.isclose(b["value"], np.mean([
        panel.at[b["exit"], "A"] / panel.at[b["entry"], "A"] - 1,
        panel.at[b["exit"], "K"] / k0 - 1]))
    # Exit on a Korean holiday: sold at Korea's last session before it.
    t = pd.Timestamp("2019-09-05 23:59:59", tz="UTC")
    b = holding_return(panel, ["K"], t, 5, ALL[-1], calendar=US, venues=VENUES)
    assert b["exit"].date() == dt.date(2019, 9, 13) and b["statuses"]["K"] == "complete"
    last = KR[KR <= b["exit"]][-1]
    assert np.isclose(b["value"], panel.at[last, "K"] / panel.at[b["entry"], "K"] - 1)


def test_missing_quote_on_an_open_venue_day_is_not_filled():
    panel = _mixed()
    t = pd.Timestamp("2019-08-31 23:59:59", tz="UTC")
    panel.loc[US[US > t][0], "C"] = np.nan  # US open, C has no quote at entry
    b = holding_return(panel, ["A", "B", "C"], t, 5, ALL[-1], calendar=US, venues=VENUES)
    assert b["statuses"]["C"] == "no_entry"


def test_successor_terms_are_honoured_when_quotes_are_missing():
    panel = _mixed()
    t = pd.Timestamp("2019-08-31 23:59:59", tz="UTC")
    gone = US[US > t][3]
    panel.loc[gone:, "B"] = np.nan  # B acquired
    stock = {"B": Successor(symbol="C", shares=1.0, source="t")}
    exit_ = US[US.get_loc(US[US > t][0]) + 10]
    b = holding_return(panel, ["A", "B"], t, 10, ALL[-1], {"B": gone.date()}, stock,
                       calendar=US, venues=VENUES)
    assert b["statuses"]["B"] == "converted"
    panel.loc[exit_ - pd.Timedelta(days=2):, "C"] = np.nan  # successor exit quote missing
    b = holding_return(panel, ["A", "B"], t, 10, ALL[-1], {"B": gone.date()}, stock,
                       calendar=US, venues=VENUES)
    assert b["statuses"]["B"] == "converted_stale" and b["status"] == "ok"
    panel.loc[:, "C"] = np.nan  # no successor price at all: not the old last close
    b = holding_return(panel, ["A", "B"], t, 10, ALL[-1], {"B": gone.date()}, stock,
                       calendar=US, venues=VENUES)
    assert b["statuses"]["B"] == "unvalued" and b["status"] == "unvalued_position"
    cash = {"B": Successor(symbol="C", cash=130.0, source="t")}  # cash-only: no quote needed
    b = holding_return(panel, ["A", "B"], t, 10, ALL[-1], {"B": gone.date()}, cash,
                       calendar=US, venues=VENUES)
    assert b["statuses"]["B"] == "converted" and b["status"] == "ok"
    b = holding_return(panel, ["A", "B"], t, 10, ALL[-1], {"B": gone.date()},
                       calendar=US, venues=VENUES)  # unknown terms stay an assumption
    assert b["statuses"]["B"] == "delisted" and b["n_delisted_assumed"] == 1


def test_forward_outcomes_use_the_horizon_calendar(tmp_path):
    from themegraph import forward as Fw
    (tmp_path / Fw.PREDICTIONS).write_text(json.dumps(
        {"prediction_id": "p1", "snapshot": "2019-08-31 23:59:59+00:00",
         "issuers": ["A", "B", "C"]}) + "\n")
    out = Fw.evaluate_outcomes(tmp_path, _mixed(), "SPY", [5], ALL[-1],
                               calendar=US, venues=VENUES)
    assert out[0]["status"] == "ok" and out[0]["entry"].startswith("2019-09-03")


def test_momentum_ignores_other_venues_closures():
    from themegraph.eval.returns import trailing_log_return
    mom = trailing_log_return(_mixed(), ALL[-1], 30, US)
    assert mom[["A", "K"]].notna().all()


# -- verified closures versus missing data ----------------------------------------

D20 = pd.bdate_range("2020-01-01", periods=20, tz="UTC") + pd.Timedelta(hours=22)
V2 = {"SPY": "USD", "K1": "KRW", "K2": "KRW", "T": "TWD", "S": "KRW"}


def _venue_panel(k1, k2=None):
    p = pd.DataFrame(index=D20)
    p["SPY"] = np.linspace(100, 120, 20)
    p["K1"] = k1
    p["K2"] = k1 if k2 is None else k2
    return p


def test_venue_wide_outage_is_not_a_closure():
    # The reported case: both Korean quotes stop after day 3; SPY trades on.
    p = _venue_panel([100, 100, 110] + [np.nan] * 17)
    b = holding_return(p, ["K1", "K2"], D20[0], 15, D20[-1], calendar=D20, venues=V2)
    assert b["status"] == "unvalued_position"  # was ok, +10% on a 14-session-old price
    assert set(b["statuses"].values()) == {"unvalued"}
    # Within the stale tolerance the last close is used, and labelled stale.
    b = holding_return(p, ["K1", "K2"], D20[0], 5, D20[-1], calendar=D20, venues=V2)
    assert set(b["statuses"].values()) == {"stale_exit"} and np.isclose(b["value"], 0.1)


def test_long_bracketed_gap_is_missing_data_but_a_short_one_is_a_closure():
    prices = np.linspace(100, 119, 20)
    long_gap = prices.copy()
    long_gap[3:3 + MAX_CLOSURE_SESSIONS + 1] = np.nan  # resumes, but too long
    p = _venue_panel(long_gap)
    b = holding_return(p, ["K1", "K2"], D20[0], 9, D20[-1], calendar=D20, venues=V2)
    assert set(b["statuses"].values()) == {"unvalued"}
    short = prices.copy()
    short[8:11] = np.nan  # three-session holiday, both securities, venue reopens
    p = _venue_panel(short)
    b = holding_return(p, ["K1", "K2"], D20[0], 9, D20[-1], calendar=D20, venues=V2)
    assert b["exit"] == D20[10] and set(b["statuses"].values()) == {"complete"}
    assert np.isclose(b["value"], short[7] / short[1] - 1)  # sold at the last session


def test_one_securitys_gap_does_not_close_the_venue():
    k1 = np.linspace(100, 119, 20)
    k1[8:11] = np.nan
    p = _venue_panel(k1, np.linspace(50, 60, 20))  # K2 quoted throughout
    b = holding_return(p, ["K1"], D20[0], 9, D20[-1], calendar=D20, venues=V2)
    assert b["statuses"]["K1"] == "stale_exit"


def test_single_security_venue_never_verifies_a_closure():
    t = np.linspace(100, 119, 20)
    t[8:11] = np.nan
    p = _venue_panel(np.linspace(100, 119, 20))
    p["T"] = t
    b = holding_return(p, ["T"], D20[0], 9, D20[-1], calendar=D20, venues=V2)
    assert b["statuses"]["T"] == "stale_exit"  # treated as missing, within tolerance
    s = venue_sessions(p, V2, D20)
    assert s["TWD"].equals(D20) and not s["KRW"].difference(D20).size


def test_successor_in_a_venue_outage_is_unvalued_not_its_last_close():
    p = _venue_panel(np.linspace(100, 119, 20))
    p["S"] = [80.0] * 4 + [np.nan] * 16  # the successor trades on the KRW venue
    p.loc[D20[4]:, ["K1", "K2"]] = np.nan  # the whole KRW venue stops: outage
    p["A"] = np.linspace(10, 12, 20)
    v = {**V2, "A": "USD"}
    stock = {"A": Successor(symbol="S", shares=1.0, source="t")}
    gone = D20[6].date()
    p.loc[D20[6]:, "A"] = np.nan
    b = holding_return(p, ["A"], D20[0], 15, D20[-1], {"A": gone}, stock,
                       calendar=D20, venues=v)
    assert b["statuses"]["A"] == "unvalued" and b["status"] == "unvalued_position"
    cash = {"A": Successor(symbol="S", cash=13.0, source="t")}
    b = holding_return(p, ["A"], D20[0], 15, D20[-1], {"A": gone}, cash,
                       calendar=D20, venues=v)
    assert b["statuses"]["A"] == "converted"
    assert np.isclose(b["value"], 13.0 / p.at[D20[1], "A"] - 1)
