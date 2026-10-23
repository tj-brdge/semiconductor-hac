import numpy as np
import pandas as pd

from themegraph.config import Availability
from themegraph.data.fmp import normalise_monthly_economic, to_usd
from themegraph.data.pit import as_of, macro_available_at, snapshot_ts
from themegraph.market.macro import build_factors


def test_snapshot_sees_same_day_prices_only_after_close():
    df = pd.DataFrame({"available_at": [pd.Timestamp("2024-01-31 22:00", tz="UTC"),
                                        pd.Timestamp("2024-02-01 22:00", tz="UTC")]})
    assert len(as_of(df, "2024-01-31")) == 1


def test_cpi_available_two_months_later():
    s = pd.Series(pd.to_datetime(["2025-01-01"]))
    assert macro_available_at(s, Availability())[0] == pd.Timestamp("2025-03-01", tz="UTC")


def test_cpi_gap_stays_missing_not_zero():
    rows = [{"date": "2025-08-01", "value": 100.0}, {"date": "2025-09-01", "value": 101.0},
            {"date": "2025-11-01", "value": 102.0}, {"date": "2025-12-01", "value": 103.0}]
    m = normalise_monthly_economic(rows, Availability())
    f = build_factors({}, {}, m.set_index("date")["value"], lag_months=2)["cpi"]
    # Sept growth placed in Nov; Oct missing -> growth for Oct and Nov missing (Dec, Jan).
    assert np.isclose(f.loc["2025-11-30"], np.log(101 / 100))
    assert np.isnan(f.loc["2025-12-31"]) and np.isnan(f.loc["2026-01-31"])
    assert np.isclose(f.loc["2026-02-28"], np.log(103 / 102))


def test_fx_conversion_uses_recent_rate_only():
    prices = pd.DataFrame({"date": pd.to_datetime(["2024-01-10", "2024-03-01"]),
                           "close": [1300.0, 1300.0]})
    fx = pd.DataFrame({"date": pd.to_datetime(["2024-01-09"]), "close": [1300.0]})
    out = to_usd(prices, fx)
    assert len(out) == 1 and out["close"].iloc[0] == 1.0


def test_snapshot_ts_end_of_day():
    assert snapshot_ts("2024-05-31") == pd.Timestamp("2024-05-31 23:59:59", tz="UTC")
