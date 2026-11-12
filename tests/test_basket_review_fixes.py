"""Defects found in the 2026-11-12 review of the basket and registry code."""

from types import SimpleNamespace

import numpy as np
import pandas as pd

from themegraph.monitor.baskets import PriceBook, entry_session, theme_performance
from themegraph.monitor.config import load_monitor
from themegraph.monitor.registry import Registry

M = load_monitor()


def _book(cols, T=120, late=None):
    cal = pd.bdate_range("2024-01-02", periods=T, tz="UTC") + pd.Timedelta(hours=22)
    rng = np.random.default_rng(1)
    df = pd.DataFrame({c: 100 * np.cumprod(1 + rng.normal(0, 0.01, T)) for c in cols},
                      index=cal)
    for c in late or []:
        df.loc[df.index[:40], c] = np.nan
    return PriceBook(df, "SPY", {c: "USD" for c in cols}, None, None, 5)


def _theme(members, entry_after="2024-01-02 23:59:59+00:00"):
    return {"published_at": "2024-06-30T00:00:00+00:00", "versions": [{
        "version": 1, "entry_after": entry_after, "created_at": "2024-06-30T00:00:00+00:00",
        "constituents": [{"symbol": s} for s in members]}]}


def test_company_without_any_prices_is_not_bought_not_a_crash():
    perf = theme_performance(_theme(["A", "B", "ZZZ"]), _book(["SPY", "A", "B"]), ["SPY"], None)
    assert perf["status"] == "ok"  # before: TypeError, and no release at all
    z = next(c for c in perf["constituents"] if c["symbol"] == "ZZZ")
    assert z["holding"] == "not_bought" and z["return_since_entry"] == 0


def test_entry_is_never_a_close_that_may_already_have_happened():
    book = _book(["SPY", "A"])
    day = book.cal[10]
    # Decided at 21:00 UTC on a session day: New York has closed, the stamp says 22:00.
    got = entry_session(book, str(day.normalize() + pd.Timedelta(hours=21)))
    assert got == book.cal[11]  # before: the same day's close
    # A month-end decision (23:59:59) still buys at the next session, as before.
    assert entry_session(book, str(day.normalize() + pd.Timedelta(hours=23, minutes=59))) \
        == book.cal[11]


def test_benchmark_that_starts_later_still_has_returns():
    book = _book(["SPY", "A", "B", "IGV"], late=["IGV"])
    perf = theme_performance(_theme(["A", "B"]), book, ["SPY", "IGV"], None)
    r = perf["returns"]["3m"]
    assert r["IGV"] is not None and r["excess_vs_IGV"] is not None  # before: None
    assert perf["series"][5]["IGV"] is None and perf["returns"]["full_period"]["IGV"] is None
    assert isinstance(perf["participation_3m"]["positive"], int)  # before: numpy -> "0"


def test_missing_evidence_month_does_not_cool_a_theme(tmp_path):
    from test_registry_rules import SNAPS, _cov
    from test_registry_rules import _theme as reg_theme
    reg = Registry(tmp_path)
    full = {s: 100 for s in "ABCD"}
    theme = {s: 10 for s in "ABC"}
    th = reg_theme(reg, [full] * 5 + [{"A": 100, "D": 100}], [theme] * 5 + [None])
    th["measures"][-1].update(observed=False, share=None)
    reg._status(th, SNAPS[5], SNAPS[:6], M, _cov())
    assert th["status"] == "stable"  # before: cooling, on a month with no evidence


def test_catch_up_back_to_the_held_basket_adds_no_version(tmp_path):
    from test_registry_rules import SNAPS, _cov
    reg = Registry(tmp_path)
    now = "2021-12-31T23:59:59+00:00"
    th = {"id": "th-1", "published": True, "published_at": "2021-01-01T00:00:00+00:00",
          "pending": None, "versions": [{
              "version": 1, "created_at": "2021-01-01T00:00:00+00:00",
              "entry_after": "2021-01-01T00:00:00+00:00",
              "constituents": [{"symbol": s} for s in "ABC"]}]}
    for t, members in zip(SNAPS, ["ABCD", "ABCD", "ABC", "ABC"], strict=False):
        reg._version(th, SimpleNamespace(exposure={s: 0.2 for s in members}), t, now, M, {},
                     _cov())
    # Before: a second version equal to the first, which rebalanced the held basket.
    assert len(th["versions"]) == 1 and th["pending"] is None
