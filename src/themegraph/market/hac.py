"""Market layer: shrinkage correlations and HAC with cophenetic affinity (no fixed k)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import cophenet, linkage
from scipy.spatial.distance import squareform
from sklearn.covariance import LedoitWolf

from ..config import Market
from .returns import window_residuals


def shrunk_correlation(resid: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf correlation of residual returns. Columns already passed the
    min_observations_share filter; their remaining gaps are set to 0 (no residual
    move) instead of dropping every row with any gap, which with staggered gaps can
    leave no rows at all."""
    X = resid.fillna(0.0)
    z = (X - X.mean()) / X.std(ddof=0).replace(0, np.nan)
    z = z.dropna(axis=1)
    cov = LedoitWolf().fit(z.to_numpy()).covariance_
    d = np.sqrt(np.diag(cov))
    corr = np.clip(cov / np.outer(d, d), -1, 1)
    np.fill_diagonal(corr, 1.0)
    return pd.DataFrame(corr, index=z.columns, columns=z.columns)


def correlation_distance(corr: pd.DataFrame) -> np.ndarray:
    D = np.sqrt(np.clip(2 * (1 - corr.to_numpy()), 0, None))
    np.fill_diagonal(D, 0.0)
    return D


def hac_affinity(corr: pd.DataFrame, method: str = "average") -> tuple[pd.DataFrame, np.ndarray]:
    """Cophenetic similarity in [0, 1]: 1 - merge height / max height."""
    D = correlation_distance(corr)
    Z = linkage(squareform(D, checks=False), method=method)
    coph = squareform(cophenet(Z))
    top = coph.max() or 1.0
    aff = 1 - coph / top
    np.fill_diagonal(aff, 1.0)
    return pd.DataFrame(aff, index=corr.index, columns=corr.columns), Z


def market_snapshot(rets: pd.DataFrame, t: pd.Timestamp, window: int, cfg: Market,
                    symbols: list[str], residualize_against: list[str],
                    known: pd.DataFrame | None = None) -> dict:
    """Affinity among `symbols` from the `window` sessions ending at or before t.
    `known` (same shape as rets) gives when each return became available; returns
    not yet available at t are treated as missing, so a snapshot never sees a price
    published after it, whatever its trading date."""
    hist = rets.loc[rets.index <= t]
    if known is not None:
        k = known.reindex(index=hist.index, columns=hist.columns)
        hist = hist.where(k.isna() | (k <= t)).dropna(how="all")
    hist = hist.tail(window)
    cols = [s for s in symbols if s in hist.columns]
    present = hist[cols].notna().mean()
    eligible = present.index[present >= cfg.min_observations_share].tolist()
    bench = [b for b in residualize_against if b in hist.columns]
    resid = window_residuals(hist[eligible + bench], bench)
    if resid.shape[1] < 3 or len(hist) < window:
        return {"t": t, "window": window, "symbols": [], "affinity": None, "corr": None,
                "linkage": None}
    corr = shrunk_correlation(resid)
    aff, Z = hac_affinity(corr, cfg.linkage)
    return {"t": t, "window": window, "symbols": list(aff.index), "affinity": aff,
            "corr": corr, "linkage": Z}


def mean_pair_affinity(aff: pd.DataFrame | None, members: list[str]) -> float:
    if aff is None:
        return float("nan")
    m = [s for s in members if s in aff.index]
    if len(m) < 2:
        return float("nan")
    sub = aff.loc[m, m].to_numpy()
    iu = np.triu_indices(len(m), 1)
    return float(sub[iu].mean())
