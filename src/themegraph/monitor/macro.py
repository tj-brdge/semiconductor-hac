"""Macro sensitivity of baskets and market clusters (docs/MONITOR.md, "Macro
sensitivity").

Question: in months when short rates, long rates, oil, inflation or the dollar
moved, did this group of stocks tend to move with them, beyond the market?

For each theme basket and each market cluster: the equal-weight monthly return of
its current companies over the last `window_months` complete months is regressed
(ordinary least squares) on the market's return and the month's change in each
factor, all standardised. A coefficient of +0.4 means: in a month when the factor
rose by one of its usual monthly moves, the group's return was 0.4 of its own
usual monthly moves higher, holding the market and the other factors fixed.

Factors use only values known at the time: CPI for month M counts in month
M + release lag (ingest.build_factors). A link is called clear when |t| >= 2.
With five factors and many groups, about one in twenty such labels appears by
chance; groups that share companies are not independent, so a chance link tends
to show up in several at once; and three years of months is a short sample: read
these as a description of the recent past, not a forecast. Nothing here feeds
detection, baskets or
any threshold.
"""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

FACTORS = {
    "us2y": "Short-term rates (2-year Treasury yield)",
    "us10y": "Long-term rates (10-year Treasury yield)",
    "wti": "Oil (WTI crude)",
    "cpi": "Inflation (CPI, when published)",
    "dxy": "US dollar (dollar index)",
}
T_CLEAR = 2.0


def monthly_returns(carry: pd.DataFrame, last_date) -> pd.DataFrame:
    """Simple monthly returns of carried closes, complete months only."""
    px = carry.copy()
    px.index = pd.DatetimeIndex(px.index).tz_convert(None)
    m = px.resample("ME").last()
    r = m.pct_change(fill_method=None)
    r.index = r.index.to_period("M")
    last = pd.Timestamp(last_date).tz_convert(None) if pd.Timestamp(last_date).tzinfo \
        else pd.Timestamp(last_date)
    if last.normalize() < (last + pd.offsets.BMonthEnd(0)).normalize():
        r = r.loc[r.index < last.to_period("M")]  # the current month is not over
    return r


def sensitivities(y: pd.Series, market: pd.Series, factors: pd.DataFrame,
                  min_months: int) -> dict | None:
    """Standardised OLS coefficient and t-statistic of `y` on each factor, with the
    market as a control. None when there are too few complete months."""
    F = factors.loc[:, [c for c in factors.columns if factors[c].notna().sum() >= min_months]]
    frame = pd.concat([y.rename("y"), market.rename("mkt"), F], axis=1).dropna()
    k = frame.shape[1] - 1
    if len(frame) < max(min_months, k + 3) or not F.shape[1]:
        return None
    sd = frame.std(ddof=0)
    if (sd == 0).any():
        return None
    Z = (frame - frame.mean()) / sd
    X = np.column_stack([np.ones(len(Z)), Z.drop(columns="y").to_numpy()])
    yv = Z["y"].to_numpy()
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ beta
    dof = len(yv) - X.shape[1]
    cov = (resid @ resid / dof) * np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.clip(np.diag(cov), 1e-18, None))
    out = {"months": int(len(frame))}
    for j, name in enumerate(Z.drop(columns="y").columns, 1):
        if name == "mkt":
            continue
        t = float(beta[j] / se[j])
        out[name] = {"beta": round(float(beta[j]), 3), "t": round(t, 2),
                     "clear": bool(abs(t) >= T_CLEAR)}
    return out


def macro_table(book, factors: pd.DataFrame | None, clusters: list[dict],
                baskets: dict[str, list[str]], window_months: int, min_months: int,
                index: str) -> dict:
    if factors is None or factors.empty:
        return {"available": False,
                "note": "No macro series stored yet (rates, oil, CPI, dollar): they are "
                        "downloaded with prices on the next refresh."}
    r = monthly_returns(book.carry, book.last_date).tail(window_months)
    if index not in r or r[index].notna().sum() < min_months:
        return {"available": False, "note": "Not enough complete months of prices."}
    F = factors.copy()
    F.index = pd.DatetimeIndex(F.index).to_period("M")
    F = F.loc[~F.index.duplicated(keep="last")].reindex(r.index)
    F = F[[c for c in FACTORS if c in F.columns]]

    def group(members):
        cols = [s for s in members if s in r.columns]
        if not cols:
            return None
        return sensitivities(r[cols].mean(axis=1, skipna=True), r[index], F, min_months)

    used = [c for c in F.columns if F[c].notna().sum() >= min_months]
    return {"available": True, "window_months": window_months, "min_months": min_months,
            "t_clear": T_CLEAR, "index": index,
            "start": str(r.index[0]), "end": str(r.index[-1]),
            "factors": [{"key": k, "label": FACTORS[k]} for k in used],
            "missing": [FACTORS[k] for k in FACTORS if k not in used],
            "clusters": {str(c["id"]): g for c in clusters
                         if (g := group(c["members"])) is not None},
            "themes": {tid: g for tid, b in baskets.items() if (g := group(b)) is not None}}


def fixture_factors(prices: pd.DataFrame) -> pd.DataFrame:
    """Synthetic monthly factor changes for demonstration data: seeded noise, so
    the demonstration shows the layout and (rightly) almost no clear link."""
    d = pd.to_datetime(prices["date"])
    idx = pd.period_range(d.min().to_period("M"), d.max().to_period("M"), freq="M")
    rng = np.random.default_rng(zlib.crc32(b"fixture-macro"))
    return pd.DataFrame(rng.standard_normal((len(idx), len(FACTORS))),
                        index=idx.to_timestamp(how="end").normalize(), columns=list(FACTORS))
