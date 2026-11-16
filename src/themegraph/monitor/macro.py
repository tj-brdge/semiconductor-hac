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


# -- clusters by macro sensitivity -----------------------------------------------------
# Declared 2026-11-16, before any result on live data. Text and returns thresholds
# elsewhere are untouched; nothing here feeds detection or baskets.
ALPHA = 0.05          # a stock counts as macro-sensitive when the five factors together
                      # explain its returns beyond the market at this level (F-test)
MIN_CORR = 0.7        # stocks join a cluster when their macro-driven return paths
                      # correlate this much on average
DRAWS = 200           # shuffles of the factor months, for the chance comparison
PHRASE = {"us2y": "short-term rates rise", "us10y": "long-term rates rise",
          "wti": "oil rises", "cpi": "inflation rises", "dxy": "the dollar rises"}


def _fit(Y: np.ndarray, m: np.ndarray, Fz: np.ndarray, rss0: np.ndarray):
    """Per stock (columns of Y): factor coefficients beyond the market, and the
    p-value of the factors jointly (F-test against market only)."""
    from scipy.stats import f as fdist
    X = np.column_stack([np.ones(len(m)), m, Fz])
    B, *_ = np.linalg.lstsq(X, Y, rcond=None)
    rss = ((Y - X @ B) ** 2).sum(axis=0)
    k, dof = Fz.shape[1], len(m) - X.shape[1]
    stat = ((rss0 - rss) / k) / np.clip(rss / dof, 1e-18, None)
    return B[2:], fdist.sf(stat, k, dof)


def _groups(Fz: np.ndarray, betas: np.ndarray, min_corr: float) -> tuple[list, np.ndarray]:
    """Average-linkage clusters of the stocks' macro-driven return paths."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    if betas.shape[1] < 2:
        return [], np.zeros((betas.shape[1],) * 2)
    C = np.corrcoef((Fz @ betas).T)
    D = np.clip(1.0 - C, 0.0, 2.0)
    np.fill_diagonal(D, 0.0)
    lab = fcluster(linkage(squareform(D, checks=False), method="average"),
                   t=1.0 - min_corr, criterion="distance")
    return [g for g in (np.flatnonzero(lab == x) for x in np.unique(lab)) if len(g) > 1], C


def _name(mean: dict[str, float], members: list[str], taken: set[str]) -> str:
    ranked = sorted(mean.items(), key=lambda kv: -abs(kv[1]))
    top = [ranked[0]] + [kv for kv in ranked[1:2] if abs(kv[1]) >= 0.75 * abs(ranked[0][1])]
    name = " and ".join(f"{'rises' if b > 0 else 'falls'} when {PHRASE[k]}" for k, b in top)
    name = name[0].upper() + name[1:]
    if name in taken:
        name = f"{name} ({', '.join(members[:2])})"
    taken.add(name)
    return name


def macro_clusters(book, factors: pd.DataFrame | None, universe, window_months: int,
                   min_months: int, index: str, alpha: float = ALPHA,
                   min_corr: float = MIN_CORR, draws: int = DRAWS) -> dict:
    """Stocks grouped by how they have traded against the factors.

    1. Per stock: monthly return regressed on the market and the factor changes.
       Stocks whose factors are jointly significant (F-test, `alpha`) are
       macro-sensitive; the rest are left out, because the direction of a
       sensitivity that is not there is noise.
    2. Each sensitive stock's macro-driven return path (factor changes times its
       coefficients) is clustered by average linkage, cut at `min_corr`.
    3. Chance comparison: the factor months are shuffled `draws` times (which
       breaks any link to returns but keeps how stocks move with each other), and
       the same steps are repeated. `beyond_chance` says whether more stocks are
       sensitive than in 95% of shuffles.
    """
    if factors is None or factors.empty:
        return {"available": False, "note": "No macro series stored yet."}
    r = monthly_returns(book.carry, book.last_date).tail(window_months)
    F = factors.copy()
    F.index = pd.DatetimeIndex(F.index).to_period("M")
    F = F.loc[~F.index.duplicated(keep="last")].reindex(r.index)
    F = F[[c for c in FACTORS if c in F.columns and F[c].notna().sum() >= min_months]]
    if index not in r or not F.shape[1]:
        return {"available": False, "note": "Not enough complete months of prices."}
    ok = r[index].notna() & F.notna().all(axis=1)
    r, F = r.loc[ok], F.loc[ok]
    if len(r) < max(min_months, F.shape[1] + 4):
        return {"available": False, "note": "Not enough complete months of prices."}
    sector = {i.symbol: universe.sectors.get(i.segment, i.segment) for i in universe.issuers}
    syms = [s for s in sector if s in r.columns and r[s].notna().all() and r[s].std() > 0]
    if len(syms) < 3:
        return {"available": False, "note": "Not enough companies with a full history."}

    def z(a):
        a = np.asarray(a, dtype=float)
        return (a - a.mean(axis=0)) / a.std(axis=0)
    Y, m, Fz = z(r[syms]), z(r[index]), z(F)
    X0 = np.column_stack([np.ones(len(m)), m])
    B0, *_ = np.linalg.lstsq(X0, Y, rcond=None)
    rss0 = ((Y - X0 @ B0) ** 2).sum(axis=0)
    betas, p = _fit(Y, m, Fz, rss0)
    sens = np.flatnonzero(p < alpha)
    groups, C = _groups(Fz, betas[:, sens], min_corr)

    rng = np.random.default_rng(zlib.crc32(b"macro-clusters"))
    null_s, null_c = [], []
    for _ in range(draws):
        Fp = Fz[rng.permutation(len(Fz))]
        bp, pp = _fit(Y, m, Fp, rss0)
        sp = np.flatnonzero(pp < alpha)
        null_s.append(len(sp))
        null_c.append(sum(len(g) for g in _groups(Fp, bp[:, sp], min_corr)[0]))
    s95, c95 = float(np.quantile(null_s, 0.95)), float(np.quantile(null_c, 0.95))

    keys = list(F.columns)
    clusters, taken = [], set()
    for g in sorted(groups, key=lambda g: -len(g)):
        members = [syms[sens[k]] for k in g]
        mean = {k: round(float(betas[j, sens[g]].mean()), 3) for j, k in enumerate(keys)}
        secs: dict[str, int] = {}
        for sy in members:
            secs[sector[sy]] = secs.get(sector[sy], 0) + 1
        sub = C[np.ix_(g, g)]
        clusters.append({"id": len(clusters) + 1, "name": _name(mean, members, taken),
                         "members": members, "size": len(members), "betas": mean,
                         "avg_corr": round(float(sub[np.triu_indices(len(g), 1)].mean()), 3),
                         "sectors": dict(sorted(secs.items(), key=lambda kv: -kv[1]))})
    clustered = {s for c in clusters for s in c["members"]}
    return {"available": True, "index": index, "months": int(len(r)),
            "start": str(r.index[0]), "end": str(r.index[-1]),
            "factors": [{"key": k, "label": FACTORS[k]} for k in keys],
            "companies": len(syms), "left_out": len(sector) - len(syms),
            "alpha": alpha, "min_corr": min_corr, "draws": draws,
            "sensitive": int(len(sens)), "clustered": len(clustered),
            "null": {"sensitive_p95": s95, "sensitive_mean": round(float(np.mean(null_s)), 1),
                     "clustered_p95": c95},
            "beyond_chance": bool(len(sens) > s95),
            "clusters": clusters,
            "unclustered": [syms[k] for k in sens if syms[k] not in clustered]}


def fixture_factors(prices: pd.DataFrame) -> pd.DataFrame:
    """Synthetic monthly factor changes for demonstration data: seeded noise, so
    the demonstration shows the layout and (rightly) almost no clear link."""
    d = pd.to_datetime(prices["date"])
    idx = pd.period_range(d.min().to_period("M"), d.max().to_period("M"), freq="M")
    rng = np.random.default_rng(zlib.crc32(b"fixture-macro"))
    return pd.DataFrame(rng.standard_normal((len(idx), len(FACTORS))),
                        index=idx.to_timestamp(how="end").normalize(), columns=list(FACTORS))
