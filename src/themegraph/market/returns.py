"""Daily log returns and benchmark residualisation, computed inside each window only."""

from __future__ import annotations

import numpy as np
import pandas as pd


def price_panel(prices: pd.DataFrame) -> pd.DataFrame:
    """Long (symbol, date, close) -> wide date x symbol of USD closes."""
    return prices.pivot_table(index="date", columns="symbol", values="close").sort_index()


def log_returns(panel: pd.DataFrame) -> pd.DataFrame:
    """Returns on sessions where both closes exist; a missing close stays missing."""
    return np.log(panel).diff().iloc[1:]


def calendar_returns(panel: pd.DataFrame, known: pd.DataFrame,
                     calendar: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Log returns on one trading calendar, with when each became known.

    Each stock's return runs between its own consecutive closes, so a day its market
    is shut (a US holiday on which Asian markets trade, or the reverse) does not
    erase its returns. Returns are then summed onto the sessions of `calendar` (the
    benchmark): a foreign stock's moves on non-calendar days land on the next
    calendar session. A return is known when the later of its two closes is.
    Without the calendar symbol, the union of all trading days is used."""
    cal = panel[calendar].dropna().index if calendar in panel else panel.index
    rets, kn = {}, {}
    for c in panel.columns:
        s = panel[c].dropna()
        s = s[s > 0]
        if len(s) < 2:
            continue
        r = np.log(s).diff().iloc[1:]
        k = known[c].reindex(s.index)
        k = pd.concat([k, k.shift(1)], axis=1).max(axis=1).iloc[1:]
        pos = cal.searchsorted(r.index)
        ok = pos < len(cal)
        bucket = cal[pos[ok]]
        rets[c] = r[ok].groupby(bucket).sum(min_count=1)
        kn[c] = k[ok].groupby(bucket).max()
    R = pd.DataFrame(rets).reindex(cal[1:])
    K = pd.DataFrame(kn).reindex(index=R.index, columns=R.columns)
    return R, K


def window_residuals(rets: pd.DataFrame, benchmarks: list[str]) -> pd.DataFrame:
    """OLS residuals of every non-benchmark column on the benchmark returns,
    estimated within this window only (no look-ahead from later data)."""
    bench = [b for b in benchmarks if b in rets.columns]
    names = [c for c in rets.columns if c not in bench]
    if not bench:
        return rets[names]
    X = rets[bench]
    out = {}
    for c in names:
        frame = pd.concat([rets[c], X], axis=1).dropna()
        if len(frame) < 10:
            out[c] = pd.Series(np.nan, index=rets.index)
            continue
        A = np.column_stack([np.ones(len(frame)), frame[bench].to_numpy()])
        coef, *_ = np.linalg.lstsq(A, frame[c].to_numpy(), rcond=None)
        resid = frame[c] - A @ coef
        out[c] = resid.reindex(rets.index)
    return pd.DataFrame(out)
