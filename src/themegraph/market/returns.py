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
