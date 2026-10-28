"""Market layer: which stocks trade together. Shrinkage correlations and HAC with
cophenetic affinity (no fixed k).

Estimators (config `market.estimator`, DESIGN.md "Market layer v2"):
- single_index (v2): simple daily returns, linked geometrically onto the benchmark
  calendar; the covariance matrix is shrunk toward a single-index model on the
  index (Ledoit & Wolf 2003, "Improved estimation of the covariance matrix of stock
  returns"); the correlation used is what the shrunk covariance leaves beyond the
  index, so stocks do not look connected just because they all move with the market.
- residual_lw (v1): log returns, OLS residuals on the configured benchmarks,
  Ledoit-Wolf shrinkage toward the identity.
"""

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


def single_index_shrinkage(R: pd.DataFrame, m: pd.Series) -> dict:
    """Ledoit-Wolf (2003) covariance shrunk toward a single-index model.

    R: T x N simple returns (no gaps), m: the index's simple returns on the same
    days. Target F: off-diagonal beta_i * beta_j * var(m), diagonal the sample
    variances. Shrinkage intensity: the paper's consistent estimator of the optimal
    weight, clipped to [0, 1]. Differences from the paper's code: the index is the
    configured benchmark rather than the equal-weighted mean of the sample, and
    moments use 1/T as there.

    Returns the shrunk covariance `sigma`, `beta`, `var_index`, `shrinkage`, and
    `excess_corr`: the correlation of the part of each return the index does not
    explain, taken from the shrunk covariance. Off the diagonal that is
    (1 - shrinkage) * (sample covariance - beta_i * beta_j * var(m)), i.e. the
    sample's co-movement beyond the index, shrunk toward none."""
    X = R.to_numpy(dtype=float)
    x = X - X.mean(axis=0)
    xm = m.to_numpy(dtype=float)
    xm = xm - xm.mean()
    t, n = x.shape
    sample = x.T @ x / t
    covm = x.T @ xm / t
    varm = float(xm @ xm / t)
    prior = np.outer(covm, covm) / varm
    np.fill_diagonal(prior, np.diag(sample))
    c = float(np.linalg.norm(sample - prior, "fro") ** 2)
    y = x ** 2
    p = float((y.T @ y).sum() / t - (sample ** 2).sum())
    rdiag = float((y ** 2).sum() / t - (np.diag(sample) ** 2).sum())
    z = x * xm[:, None]
    v1 = y.T @ z / t - covm[:, None] * sample
    roff1 = float((v1 * covm[None, :]).sum() / varm - (np.diag(v1) * covm).sum() / varm)
    v3 = z.T @ z / t - varm * sample
    roff3 = float((v3 * np.outer(covm, covm)).sum() / varm ** 2
                  - (np.diag(v3) * covm ** 2).sum() / varm ** 2)
    r = rdiag + 2 * roff1 - roff3
    shrink = float(np.clip((p - r) / c / t, 0.0, 1.0)) if c > 0 else 1.0
    sigma = shrink * prior + (1 - shrink) * sample
    beta = covm / varm
    resid = sigma - varm * np.outer(beta, beta)
    d = np.sqrt(np.clip(np.diag(resid), 1e-18, None))
    excess = np.clip(resid / np.outer(d, d), -1, 1)
    np.fill_diagonal(excess, 1.0)
    cols = R.columns
    return {"sigma": pd.DataFrame(sigma, index=cols, columns=cols),
            "beta": pd.Series(beta, index=cols), "var_index": varm,
            "shrinkage": shrink,
            "excess_corr": pd.DataFrame(excess, index=cols, columns=cols)}


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
    empty = {"t": t, "window": window, "symbols": [], "affinity": None, "corr": None,
             "linkage": None}
    if getattr(cfg, "estimator", "residual_lw") == "single_index":
        # `rets` are log returns summed onto the calendar, so expm1 gives the simple
        # return compounded over each stock's own sessions in that calendar step:
        # the geometric link. A gap still left after linking (no close at all
        # in the step) counts as no move, as in v1.
        idx = cfg.index
        eligible = [s for s in eligible if s != idx]
        if idx not in hist.columns or len(eligible) < 3 or len(hist) < window:
            return empty
        h = np.expm1(hist[eligible + [idx]])
        h = h.loc[h[idx].notna()]
        est = single_index_shrinkage(h[eligible].fillna(0.0), h[idx])
        corr = est["excess_corr"]
        aff, Z = hac_affinity(corr, cfg.linkage)
        return {"t": t, "window": window, "symbols": list(aff.index), "affinity": aff,
                "corr": corr, "linkage": Z, "shrinkage": est["shrinkage"]}
    bench = [b for b in residualize_against if b in hist.columns]
    resid = window_residuals(hist[eligible + bench], bench)
    if resid.shape[1] < 3 or len(hist) < window:
        return empty
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
