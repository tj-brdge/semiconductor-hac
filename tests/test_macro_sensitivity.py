"""Macro sensitivity of baskets and clusters (monitor/macro.py)."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from themegraph.monitor import refresh as M
from themegraph.monitor.macro import FACTORS, macro_table, monthly_returns, sensitivities

IDX = pd.period_range("2018-01", periods=36, freq="M")


def _data(seed=0):
    rng = np.random.default_rng(seed)
    F = pd.DataFrame(rng.standard_normal((36, 5)), index=IDX, columns=list(FACTORS))
    mkt = pd.Series(rng.standard_normal(36), index=IDX)
    return rng, F, mkt


def test_a_real_link_is_found_and_noise_is_not():
    rng, F, mkt = _data()
    y = 0.5 * mkt - 0.8 * F["us10y"] + 0.3 * pd.Series(rng.standard_normal(36), index=IDX)
    out = sensitivities(y, mkt, F, 30)
    assert out["months"] == 36 and "mkt" not in out
    assert out["us10y"]["clear"] and out["us10y"]["beta"] < -0.5
    assert not out["wti"]["clear"] and not out["cpi"]["clear"]


def test_false_alarm_rate_is_about_one_in_twenty():
    hits = total = 0
    for seed in range(300):
        rng, F, mkt = _data(seed)
        y = 0.6 * mkt + pd.Series(rng.standard_normal(36), index=IDX)
        out = sensitivities(y, mkt, F, 30)
        hits += sum(out[k]["clear"] for k in FACTORS)
        total += len(FACTORS)
    assert 0.02 < hits / total < 0.09


def test_too_few_months_and_missing_factors():
    rng, F, mkt = _data()
    y = pd.Series(rng.standard_normal(36), index=IDX)
    assert sensitivities(y.tail(20), mkt, F, 30) is None       # too short: no numbers
    F2 = F.assign(dxy=np.nan)                                   # a factor never downloaded
    out = sensitivities(y, mkt, F2, 30)
    assert "dxy" not in out and out["months"] == 36
    F3 = F.copy()
    F3.iloc[:4, 0] = np.nan                                     # months without a value drop out
    assert sensitivities(y, mkt, F3, 30)["months"] == 32


def test_the_unfinished_month_is_left_out():
    days = pd.bdate_range("2021-01-01", "2021-03-17", tz="UTC")
    carry = pd.DataFrame({"A": np.linspace(100, 120, len(days))}, index=days)
    r = monthly_returns(carry, days[-1])
    assert str(r.index[-1]) == "2021-02"
    full = pd.bdate_range("2021-01-01", "2021-03-31", tz="UTC")
    carry = pd.DataFrame({"A": np.linspace(100, 120, len(full))}, index=full)
    assert str(monthly_returns(carry, full[-1]).index[-1]) == "2021-03"


def test_no_series_stored_is_said_not_guessed():
    out = macro_table(None, None, [], {}, "SPY")
    assert out["available"] is False and "downloaded with prices" in out["note"]


def test_refresh_publishes_sensitivities_for_clusters_and_themes(tmp_path):
    st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                   as_of=dt.date(2021, 6, 30), log=lambda *a: None)
    assert st["ok"], st["errors"]
    data = tmp_path / "site" / "data"
    rel = data / json.loads((data / "latest.json").read_text())["path"]
    macro = json.loads((rel / "index.json").read_text())["macro"]
    assert macro["available"] and macro["end"] == "2021-06" and "themes" not in macro
    index = json.loads((rel / "index.json").read_text())
    assert index["company_names"] == {}        # the synthetic universe has no names
    assert [f["key"] for f in macro["factors"]] == list(FACTORS)
    assert [s["label"] for s in macro["spans"]] == ["30 days", "60 days", "90 days",
                                                    "180 days", "360 days"]
    assert macro["clusters"]
    assert set(macro["factor_moves"]) == set(FACTORS)
    assert macro["factor_moves"]["us10y"]["unit"] == "pp"
    doc = json.loads(next((rel / "themes").glob("*.json")).read_text())
    g = doc["macro"]
    assert "1" in g and "12" not in g          # six years of fixture months: no 360-day span
    assert set(g["1"]) == {"months", *FACTORS}
    assert all(set(g["1"][k]) == {"beta", "p", "clear"} for k in FACTORS)


# -- time frames -------------------------------------------------------------------------

def _long(seed=3, months=144):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2014-01", periods=months, freq="M")
    F = pd.DataFrame(rng.standard_normal((months, 5)), index=idx, columns=list(FACTORS))
    mkt = pd.Series(rng.standard_normal(months), index=idx)
    return rng, idx, F, mkt


def test_a_link_is_found_at_every_time_frame_and_noise_is_not():
    from themegraph.monitor.macro import SPANS, span_sensitivities
    rng, idx, F, mkt = _long()
    Y = pd.DataFrame({"linked": 0.6 * mkt - 0.7 * F["us10y"] + 0.7 * rng.standard_normal(144),
                      "noise": 0.6 * mkt + rng.standard_normal(144)}, index=idx)
    for h, _ in SPANS:
        out = span_sensitivities(Y, mkt, F, h)
        assert out["linked"]["us10y"]["clear"] and out["linked"]["us10y"]["beta"] < -0.25, h
        assert out["linked"]["months"] == 144
    flagged = sum(span_sensitivities(Y, mkt, F, h)["noise"][k]["clear"]
                  for h, _ in SPANS for k in FACTORS)
    assert flagged <= 4                          # 25 tries on noise


def test_long_spans_do_not_over_flag_noise():
    """Overlapping spans make ordinary tests flag about 15% of noise at 360 days
    (measured 2026-11-16); the slid-history comparison keeps it near 5%."""
    from themegraph.monitor.macro import span_sensitivities
    hits = total = 0
    for seed in range(12):
        rng, idx, F, mkt = _long(seed)
        Y = pd.DataFrame({f"g{i}": 0.6 * mkt + rng.standard_normal(144) for i in range(10)},
                         index=idx)
        for g in span_sensitivities(Y, mkt, F, 12).values():
            hits += sum(g[k]["clear"] for k in FACTORS)
            total += len(FACTORS)
    assert total == 600 and hits / total < 0.11


def test_a_span_needs_enough_history_and_young_groups_use_their_own():
    from themegraph.monitor.macro import span_sensitivities
    rng, idx, F, mkt = _long()
    young = pd.Series(rng.standard_normal(144), index=idx)
    young.iloc[:90] = np.nan                     # 54 months of history
    Y = pd.DataFrame({"old": rng.standard_normal(144), "young": young}, index=idx)
    one, twelve = span_sensitivities(Y, mkt, F, 1), span_sensitivities(Y, mkt, F, 12)
    assert one["young"]["months"] == 54 and one["old"]["months"] == 144
    assert "old" in twelve and "young" not in twelve    # 54 months: no ten 12-month spans


def test_factor_moves_are_changes_in_plain_units():
    from themegraph.monitor.macro import factor_moves
    idx = pd.period_range("2020-01", periods=12, freq="M")
    F = pd.DataFrame({"us10y": 0.1, "wti": np.log(1.02), "cpi": np.log(1.003)}, index=idx)
    mv = factor_moves(F)
    assert mv["us10y"] == {"unit": "pp", "moves": {"1": 0.1, "2": 0.2, "3": 0.3, "6": 0.6,
                                                    "12": 1.2}}
    assert mv["wti"]["moves"]["1"] == 2.0 and mv["wti"]["moves"]["2"] == 4.04
    assert mv["cpi"]["moves"]["12"] == round((1.003 ** 12 - 1) * 100, 2)


# -- clusters by macro sensitivity -----------------------------------------------------

def _book(seed, planted=True, n=40, months=38):
    from types import SimpleNamespace as NS

    from themegraph.monitor.macro import FACTORS as FK
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2018-01", periods=months, freq="M")
    F = pd.DataFrame(rng.standard_normal((months, 5)),
                     index=idx.to_timestamp(how="end").normalize(), columns=list(FK))
    mkt = rng.standard_normal(months) * 0.04
    rets = {"SPY": mkt}
    for i in range(n):
        e = rng.standard_normal(months) * 0.05
        if planted and i < 8:
            e = e - 0.06 * F["us10y"].to_numpy()      # hurt by rising long rates
        elif planted and i < 14:
            e = e + 0.06 * F["wti"].to_numpy()        # helped by rising oil
        elif planted and i < 18:                      # both at once
            e = e - 0.05 * F["us10y"].to_numpy() + 0.05 * F["wti"].to_numpy()
        rets[f"S{i:02d}"] = mkt + e
    # One close per month (its last business day), a starting row before the first.
    ends = pd.DatetimeIndex([pd.offsets.BDay().rollback(p.to_timestamp(how="end").normalize())
                             for p in idx]).tz_localize("UTC")
    start = ends[0] - pd.offsets.BMonthEnd(1)
    lvl = (1 + pd.DataFrame(rets)).cumprod() * 100
    carry = pd.concat([pd.DataFrame(100.0, index=[start], columns=lvl.columns),
                       lvl.set_index(ends)])
    u = NS(issuers=[NS(symbol=f"S{i:02d}", segment="a" if i < 20 else "b") for i in range(n)],
           sectors={"a": "Sector A", "b": "Sector B"})
    return NS(carry=carry, last_date=ends[-1]), F, u


def test_each_company_is_measured_against_the_factors_then_clustered():
    from themegraph.monitor.macro import company_macro
    book, F, u = _book(1, months=80)
    out = company_macro(book, F, u, "SPY")
    assert out["available"] and [s["label"] for s in out["spans"]][0] == "30 days"
    one = out["by_span"]["1"]
    assert one["months"] == 72 and one["n_companies"] == 40
    assert one["beyond_chance"] and one["sensitive"] > one["null"]["p95"]
    by = {c["symbol"]: c for c in one["companies"]}
    rates = [f"S{i:02d}" for i in range(8)]          # planted: hurt by rising long rates
    both = [f"S{i:02d}" for i in range(14, 18)]      # planted: long rates and oil together
    assert sum(by[s]["links"]["us10y"]["clear"] and by[s]["links"]["us10y"]["beta"] < 0
               for s in rates) >= 6
    # Companies that trade against two factors at once are counted as such.
    assert sum(by[s]["n_clear"] >= 2 for s in both) >= 3 and one["multi"] >= 3
    names = {c["name"]: set(c["members"]) for c in one["clusters"]}
    assert len(names["Falls when long-term rates rise"] & set(rates)) >= 5
    assert any("oil rises" in n for n in names)
    # Sensitive companies come first in the table; every company is listed.
    assert one["companies"][0]["sensitive"] and len(one["companies"]) == 40
    # 80 months stored: the 180- and 360-day spans say what they need.
    assert out["by_span"]["12"]["available"] is False
    assert "144 complete months" in out["by_span"]["12"]["note"]


def test_noise_is_reported_as_not_beyond_chance():
    from themegraph.monitor.macro import company_macro
    flagged = 0
    for seed in range(6):
        book, F, u = _book(100 + seed, planted=False, months=80)
        one = company_macro(book, F, u, "SPY")["by_span"]["1"]
        flagged += one["beyond_chance"]
        assert one["sensitive"] <= 8            # about 5% of 40 by chance
    assert flagged <= 1


def test_company_macro_needs_series_and_history():
    from themegraph.monitor.macro import company_macro
    book, F, u = _book(1)                       # 38 months only
    assert company_macro(book, None, u, "SPY")["available"] is False
    out = company_macro(book, F, u, "SPY")
    assert out["available"] is False and "72 complete months" in out["note"]


def test_a_month_with_no_cpi_figure_counts_as_nothing_published():
    """The October 2025 CPI was never published. Before: that left two months of
    the factor missing, and the monitor reported 9 complete months (Feb to Oct
    2026) although years of data were stored."""
    from themegraph.market.macro import build_factors
    idx = pd.date_range("2025-06-01", periods=8, freq="MS")
    level = pd.Series([100.0, 100.2, 100.5, 100.8, np.nan, 101.4, 101.6, 101.9], index=idx)
    level = level.dropna()                       # October has no row at all
    strict = build_factors({}, {}, level, 2)["cpi"]
    assert strict.isna().sum() == 3              # the first month and both sides of the gap
    filled = build_factors({}, {}, level, 2, fill_unpublished=3)["cpi"]
    by = {str(k)[:7]: v for k, v in filled.items()}
    assert by["2025-12"] == 0.0                  # October's slot: nothing was published
    assert by["2026-01"] == pytest.approx(np.log(101.4 / 100.8))   # all of it, on arrival
    assert filled.isna().sum() == 1
    # A longer silence than the limit stays missing.
    long = level.drop(idx[[5, 6]])
    assert build_factors({}, {}, long, 2, fill_unpublished=1)["cpi"].isna().sum() > 1


def test_a_short_run_says_which_factor_broke_it():
    from themegraph.monitor.macro import company_macro
    book, F, u = _book(1, months=80)
    F.iloc[60, F.columns.get_loc("cpi")] = np.nan
    out = company_macro(book, F, u, "SPY")
    assert out["available"] is False
    assert "broken at" in out["note"] and "Inflation (CPI" in out["note"]
