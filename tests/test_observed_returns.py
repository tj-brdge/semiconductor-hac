"""Statistics use observed returns: a carried price is a valuation convention (a
holding without a fresh close keeps its last value), never a 0.0 return
(monitor/baskets.py: `PriceBook.observed`; monitor/macro.py: `monthly_returns`)."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from themegraph.monitor.baskets import PriceBook, _participation, theme_performance
from themegraph.monitor.macro import company_macro, fixture_factors, macro_table, monthly_returns

CAL = pd.bdate_range("2012-01-02", "2021-12-31", tz="UTC") + pd.Timedelta(hours=22)
LAST_QUOTE = pd.Timestamp("2019-03-15 22:00", tz="UTC")     # GONE stops here, unrecorded
ALIVE = ["A", "B", "C", "D", "E"]
STALE = 5


def _day(s: str) -> pd.Timestamp:
    return pd.Timestamp(f"{s} 22:00", tz="UTC")


# Six US sessions without any Korean quote, ending on the month's last session.
HOLIDAY = (_day("2017-09-22"), _day("2017-09-29"))


def _panel() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    mkt = rng.normal(0.0003, 0.01, len(CAL))
    px = pd.DataFrame(index=CAL)
    px["SPY"] = 100 * np.exp(np.cumsum(mkt))
    for s in ALIVE + ["GONE", "K1", "K2", "LONE"]:
        px[s] = 50 * np.exp(np.cumsum(mkt + rng.normal(0, 0.012, len(CAL))))
    px.loc[px.index > LAST_QUOTE, "GONE"] = np.nan
    gap = (px.index >= HOLIDAY[0]) & (px.index <= HOLIDAY[1])
    assert gap.sum() == 6
    px.loc[gap, ["K1", "K2"]] = np.nan      # the whole venue: a holiday week
    px.loc[gap, "LONE"] = np.nan            # one US security's own gap: not a holiday
    return px


@pytest.fixture(scope="module")
def book() -> PriceBook:
    px = _panel()
    venues = {c: "KRW" if c.startswith("K") else "USD" for c in px}
    return PriceBook(px, "SPY", venues, None, None, STALE)


@pytest.fixture(scope="module")
def factors() -> pd.DataFrame:
    return fixture_factors(pd.DataFrame({"date": CAL.tz_convert(None).normalize()}))


def _universe(symbols):
    return SimpleNamespace(issuers=[SimpleNamespace(symbol=s, segment="seg") for s in symbols],
                           sectors={"seg": "Sector"})


def test_months_without_quotes_have_no_return_instead_of_zero(book):
    r = monthly_returns(book.observed, book.last_date)
    old = monthly_returns(book.carry, book.last_date)       # what statistics used to read
    after = r.index >= pd.Period("2019-03")                 # 29 March is 10 sessions on
    assert (old.loc[after, "GONE"].iloc[1:] == 0.0).all()   # the bug: flat carried price
    assert r.loc[after, "GONE"].isna().all()
    before = r.index < pd.Period("2019-03")
    pd.testing.assert_series_equal(r.loc[before, "GONE"], old.loc[before, "GONE"])
    for s in ["SPY", *ALIVE]:                                # nothing else moves
        pd.testing.assert_series_equal(r[s], old[s])


def test_company_table_leaves_out_a_company_for_windows_it_does_not_cover(book, factors):
    out = company_macro(book, factors, _universe([*ALIVE, "GONE"]), "SPY")
    assert out["available"]
    span = out["by_span"]["1"]                               # 72 months to 2021-12
    assert span["available"] and (span["start"], span["end"]) == ("2016-01", "2021-12")
    assert {c["symbol"] for c in span["companies"]} == set(ALIVE)
    assert span["n_companies"] == 5 and span["left_out"] == 1
    assert span["left_out_unobserved"] == 1


def test_group_return_ignores_a_member_after_its_last_close_and_counts_it(book, factors):
    r = monthly_returns(book.observed, book.last_date)
    grp = r[["A", "B", "GONE"]].mean(axis=1, skipna=True)
    late, early = pd.Period("2020-06"), pd.Period("2018-06")
    assert grp[late] == pytest.approx((r.loc[late, "A"] + r.loc[late, "B"]) / 2)
    assert grp[early] == pytest.approx(r.loc[early, ["A", "B", "GONE"]].sum() / 3)
    out = macro_table(book, factors, [{"id": 1, "members": ["A", "B", "GONE"]}],
                      {"th-1": ["A", "B", "GONE"], "th-2": ["C", "D"], "th-dead": ["GONE"]},
                      "SPY")
    assert out["available"]
    want = {"members": 3, "used": 2, "left_out": 1}
    assert out["members"]["themes"]["th-1"] == want
    assert out["members"]["clusters"]["1"] == want
    assert out["members"]["themes"]["th-2"] == {"members": 2, "used": 2, "left_out": 0}
    # A group with nobody quoted has no return, so it has no sensitivities at all
    # (it used to get betas fitted mostly on zeros).
    assert "th-dead" not in out["themes"] and "th-1" in out["themes"]


def test_participation_does_not_count_an_unquoted_holding_as_eligible(book):
    p = _participation(book, ["A", "B", "GONE"], ["SPY"])
    a, z = book.cal[-64], book.cal[-1]
    ups = sum(book.closes[s][z] > book.closes[s][a] for s in ("A", "B"))
    assert p["eligible"] == 2 and p["unobserved"] == 1 and p["positive"] == ups
    assert _participation(book, ["A", "B"], ["SPY"])["unobserved"] == 0


def test_valuation_still_carries_the_last_close(book):
    """The basket index, weights and returns come from carried closes, exactly as
    before: derived here from the raw closes, without the book's panels."""
    after = "2018-01-02"
    theme = {"versions": [{"version": 1, "entry_after": after, "constituents": [
        {"symbol": s, "exposure": 1.0, "role": "core"} for s in ("A", "GONE")]}]}
    perf = theme_performance(theme, book, ["SPY"], None)
    px = book.closes
    days = px.index[px.index.normalize() > pd.Timestamp(after, tz="UTC")]
    a, g = px.loc[days, "A"], px.loc[days, "GONE"].ffill()   # the last close is kept
    value = 0.5 * a / a.iloc[0] + 0.5 * g / g.iloc[0]
    got = pd.DataFrame(perf["series"])
    assert list(got["date"]) == [str(d.date()) for d in days]
    assert list(got["theme"]) == [round(float(v), 4) for v in 100.0 * value]
    assert perf["returns"]["full_period"]["theme"] == float(value.iloc[-1] / value.iloc[0] - 1)
    c = {x["symbol"]: x for x in perf["constituents"]}
    kept = float(px["GONE"].dropna().iloc[-1] / g.iloc[0])
    assert c["GONE"]["return_since_entry"] == round(kept - 1, 6)
    assert c["GONE"]["weight_now"] == round(0.5 * kept / float(value.iloc[-1]), 6)
    assert c["A"]["weight_now"] == round(1 - 0.5 * kept / float(value.iloc[-1]), 6)
    assert c["GONE"]["price_status"] == "stale" and c["GONE"]["holding"] == "held"
    assert perf["coverage_latest"] == round(c["A"]["weight_now"], 4)
    # The trailing 3-month index return is flat for GONE's half, as valuation says...
    k = 63
    assert perf["returns"]["3m"]["theme"] == float(value.iloc[-1] / value.iloc[-1 - k] - 1)
    # ...while the statistic beside it no longer calls that a return.
    assert perf["participation_3m"]["eligible"] == 1


def test_a_holiday_week_is_not_a_missing_month(book):
    r = monthly_returns(book.observed, book.last_date)
    month = pd.Period("2017-09")
    assert book.missing_sessions("K1").loc[HOLIDAY[1]] == 0     # closure: not a session
    for s in ("K1", "K2"):
        assert r[s].iloc[1:].notna().all()             # the first month has no base
        want = book.closes[s].loc[:HOLIDAY[0]].dropna().iloc[-1] / \
            book.closes[s].loc[:_day("2017-08-31")].iloc[-1] - 1
        assert r.loc[month, s] == pytest.approx(want)
    # The same six sessions without a quote for one security alone are a gap in its
    # data: no September return, and no October return from a stale base.
    assert book.missing_sessions("LONE").loc[HOLIDAY[1]] == 6
    assert r["LONE"].iloc[1:].isna().sum() == 2 and r.loc[[month, month + 1], "LONE"].isna().all()
