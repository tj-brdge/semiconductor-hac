"""Macro-sensitivity layer: rolling ridge exposures of residual monthly returns to
macro factor changes, using only factor values available at each snapshot."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from ..config import Macro


def monthly_returns(daily_rets: pd.DataFrame) -> pd.DataFrame:
    return daily_rets.resample("ME").sum(min_count=1)


def macro_exposures(monthly_rets: pd.DataFrame, factors: pd.DataFrame, benchmark: str,
                    cfg: Macro, t: pd.Timestamp) -> pd.DataFrame:
    """Standardised ridge coefficients (issuer x factor) at snapshot t.

    `factors` is indexed by month end and must already respect availability (a
    factor value appears only in months when it was known). Rows with any missing
    factor are dropped, never filled.
    """
    r = monthly_rets.loc[monthly_rets.index <= t].tail(cfg.window_months)
    f = factors.reindex(r.index)
    rows = {}
    for sym in r.columns:
        if sym == benchmark:
            continue
        frame = pd.concat([r[sym].rename("y"), r.get(benchmark).rename("mkt"), f], axis=1).dropna()
        if len(frame) < cfg.min_months:
            continue
        X = frame.drop(columns="y")
        Xs = (X - X.mean()) / X.std(ddof=0).replace(0, np.nan)
        Xs = Xs.fillna(0.0)
        y = (frame["y"] - frame["y"].mean()) / (frame["y"].std(ddof=0) or 1.0)
        coef = Ridge(alpha=cfg.ridge_alpha).fit(Xs.to_numpy(), y.to_numpy()).coef_
        rows[sym] = pd.Series(coef, index=X.columns).drop("mkt")
    return pd.DataFrame(rows).T


def exposure_similarity(expo: pd.DataFrame) -> pd.DataFrame:
    if expo.empty:
        return expo
    v = expo.to_numpy()
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    u = v / n
    sim = (u @ u.T + 1) / 2  # map cosine [-1, 1] -> [0, 1]
    return pd.DataFrame(sim, index=expo.index, columns=expo.index)


def build_factors(daily: dict[str, pd.Series], kinds: dict[str, str],
                  cpi_monthly: pd.Series | None, lag_months: int) -> pd.DataFrame:
    """Month-end factor table.

    daily: name -> daily series (levels) indexed by date.
    kinds: name -> "diff" (yields: change in level) or "logret" (prices).
    cpi_monthly: CPI level indexed by reference month start. The growth for
    reference month M is placed in month M + lag_months, i.e. the month in which it
    was first known. Missing months stay missing.
    """
    cols = {}
    for name, s in daily.items():
        m = s.sort_index().resample("ME").last()
        cols[name] = m.diff() if kinds.get(name, "diff") == "diff" else np.log(m).diff()
    if cpi_monthly is not None:
        s = cpi_monthly.sort_index()
        idx = s.index.to_period("M")
        s = pd.Series(s.to_numpy(), index=idx)
        full = s.reindex(pd.period_range(idx.min(), idx.max(), freq="M"))
        growth = np.log(full).diff()  # a gap makes both adjacent growths missing
        growth.index = (growth.index + lag_months).to_timestamp(how="end").normalize()
        cols["cpi"] = growth
    return pd.DataFrame(cols).sort_index()
