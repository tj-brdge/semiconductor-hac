"""Macro sensitivity of baskets and clusters (monitor/macro.py)."""

import datetime as dt
import json

import numpy as np
import pandas as pd

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
    out = macro_table(None, None, [], {}, 36, 30, "SPY")
    assert out["available"] is False and "downloaded with prices" in out["note"]


def test_refresh_publishes_sensitivities_for_clusters_and_themes(tmp_path):
    st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                   as_of=dt.date(2021, 6, 30), log=lambda *a: None)
    assert st["ok"], st["errors"]
    data = tmp_path / "site" / "data"
    rel = data / json.loads((data / "latest.json").read_text())["path"]
    macro = json.loads((rel / "index.json").read_text())["macro"]
    assert macro["available"] and macro["end"] == "2021-06" and "themes" not in macro
    assert [f["key"] for f in macro["factors"]] == list(FACTORS)
    assert macro["clusters"]
    doc = json.loads(next((rel / "themes").glob("*.json")).read_text())
    g = doc["macro"]
    assert g["months"] == 36 and set(g) == {"months", *FACTORS}
    assert all(set(g[k]) == {"beta", "t", "clear"} for k in FACTORS)


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


def test_stocks_are_clustered_by_how_they_trade_against_the_factors():
    from themegraph.monitor.macro import macro_clusters
    book, F, u = _book(1)
    out = macro_clusters(book, F, u, 36, 30, "SPY")
    assert out["available"] and out["months"] == 36 and out["companies"] == 40
    assert out["beyond_chance"] and out["sensitive"] > out["null"]["sensitive_p95"]
    by = {c["name"]: set(c["members"]) for c in out["clusters"]}
    rates = by["Falls when long-term rates rise"]
    oil = by["Rises when oil rises"]
    assert len(rates & {f"S{i:02d}" for i in range(8)}) >= 6
    assert len(oil & {f"S{i:02d}" for i in range(8, 14)}) >= 4
    assert not rates & oil
    c = next(c for c in out["clusters"] if c["name"] == "Falls when long-term rates rise")
    assert c["betas"]["us10y"] < -0.3 and c["avg_corr"] >= 0.7


def test_noise_is_reported_as_not_beyond_chance():
    from themegraph.monitor.macro import macro_clusters
    flagged = 0
    for seed in range(8):
        book, F, u = _book(100 + seed, planted=False)
        out = macro_clusters(book, F, u, 36, 30, "SPY", draws=100)
        flagged += out["beyond_chance"]
        assert out["sensitive"] <= 8            # about 5% of 40 by chance
    assert flagged <= 2


def test_macro_clusters_need_series_and_history():
    from themegraph.monitor.macro import macro_clusters
    book, F, u = _book(1)
    assert macro_clusters(book, None, u, 36, 30, "SPY")["available"] is False
    assert macro_clusters(book, F.tail(10), u, 36, 30, "SPY")["available"] is False
