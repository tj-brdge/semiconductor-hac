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
