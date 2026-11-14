"""Which stocks trade together right now: the market HAC cut into clusters
(docs/MONITOR.md, "Market clusters").

The research market layer uses hierarchical agglomerative clustering (HAC) only as
a similarity score (cophenetic affinity) and never cuts the tree. This page cuts
it, for reading, over the same window and estimator as "Trading together":

- correlations: each pair's correlation beyond the index (single-index model, the
  last `window_sessions` closes, companies with enough of their own closes);
- tree: average linkage on distance 1 - correlation. Shrinkage multiplies every
  off-diagonal correlation by the same (1 - shrinkage), which changes distances
  by an affine map, so average linkage builds the same tree from shrunk or unshrunk
  correlations; the unshrunk ones are used and the shrunk level is reported;
- cut, without choosing a number of clusters: the residuals of each company are
  shuffled in time independently (`draws` times), which keeps each company's
  returns but removes any co-movement, and the highest pairwise correlation in each
  shuffle is recorded. The cut is that maximum's (1 - alpha) quantile: a cluster
  only joins companies, and groups of companies, whose average correlation beats
  the strongest pair that random data produce in 95% of shuffles (familywise
  across all pairs). Companies joined above the cut are not in a cluster.

Prices only, a description of the latest window; it changes nothing in the
registry and selects no theme.
"""

from __future__ import annotations

import zlib

import numpy as np
from scipy.cluster.hierarchy import dendrogram, fcluster, linkage
from scipy.spatial.distance import squareform

from .baskets import PriceBook
from .comovement import _window

MAX_LINKS = 600


def _residuals(R, m) -> np.ndarray:
    x = R.to_numpy(dtype=float)
    x = x - x.mean(axis=0)
    xm = m.to_numpy(dtype=float)
    xm = xm - xm.mean()
    return x - xm[:, None] * (x.T @ xm / (xm @ xm))


def _max_corr(E: np.ndarray) -> float:
    C = np.corrcoef(E, rowvar=False)
    np.fill_diagonal(C, -np.inf)
    return float(C.max())


def market_clusters(book: PriceBook, universe, baskets: dict[str, list[str]],
                    window_sessions: int, min_share: float, index: str,
                    draws: int = 200, alpha: float = 0.05) -> dict:
    symbols = [i.symbol for i in universe.issuers]
    sector = {i.symbol: universe.sectors.get(i.segment, i.segment) for i in universe.issuers}
    w = _window(book, len(book.cal) - 1, window_sessions, symbols, index, min_share)
    if w is None or "pool" not in w:
        return {"available": False, "index": index, "window_sessions": window_sessions,
                "note": (w or {}).get("note") or "not enough price history"}
    pool, C = w["pool"], w["corr"].to_numpy()
    D = np.clip(1.0 - C, 0.0, 2.0)
    np.fill_diagonal(D, 0.0)
    Z = linkage(squareform(D, checks=False), method="average")
    rng = np.random.default_rng(zlib.crc32(b"market-clusters"))
    E = _residuals(w["returns"], w["index_returns"])
    null = np.array([_max_corr(rng.permuted(E, axis=0)) for _ in range(draws)])
    level = float(np.quantile(null, 1 - alpha))
    labels = fcluster(Z, t=1.0 - level, criterion="distance")
    order = dendrogram(Z, no_plot=True)["leaves"]
    pos = {k: i for i, k in enumerate(order)}
    groups = [np.flatnonzero(labels == lab) for lab in np.unique(labels)]
    groups = sorted([g for g in groups if len(g) > 1], key=lambda g: min(pos[k] for k in g))
    scale = 1.0 - w["shrinkage"]
    clusters = []
    for n, g in enumerate(groups, 1):
        members = [pool[k] for k in sorted(g, key=lambda k: pos[k])]
        sub = C[np.ix_(g, g)]
        avg = float(sub[np.triu_indices(len(g), 1)].mean())
        secs: dict[str, int] = {}
        for s in members:
            secs[sector.get(s, "?")] = secs.get(sector.get(s, "?"), 0) + 1
        themes = sorted(([tid, len(set(b) & set(members))] for tid, b in baskets.items()
                         if len(set(b) & set(members)) >= 2), key=lambda x: -x[1])
        clusters.append({"id": n, "members": members, "size": len(members),
                         "avg_corr": round(avg, 4), "avg_corr_shrunk": round(scale * avg, 4),
                         "sectors": dict(sorted(secs.items(), key=lambda kv: -kv[1])),
                         "themes": [{"id": t, "shared": k} for t, k in themes]})
    member_of = {s: c["id"] for c in clusters for s in c["members"]}
    # Pairs whose own correlation beats the cut (the network page's price links),
    # strongest first; the cut is familywise, so these are few by construction.
    iu = np.triu_indices(len(pool), 1)
    strong = np.flatnonzero(C[iu] >= level)
    strong = strong[np.argsort(-C[iu][strong], kind="stable")][:MAX_LINKS]
    links = [[pool[iu[0][k]], pool[iu[1][k]], round(float(C[iu][k]), 4)] for k in strong]
    return {"available": True, "index": index, "window_sessions": window_sessions,
            "start": w["start"], "end": w["end"], "shrinkage": round(w["shrinkage"], 3),
            "cut": {"corr": round(level, 4), "alpha": alpha, "draws": draws,
                    "rule": "familywise maximum of shuffled residual correlations"},
            "companies": len(pool), "left_out": w.get("flat", []),
            "leaves": [{"symbol": pool[k], "sector": sector.get(pool[k]),
                        "cluster": member_of.get(pool[k])} for k in order],
            # merges: [left, right, corr at which they join, size]; ids < n are
            # companies (index into `pool` order), n + i is merge i, as in scipy.
            "pool": pool, "links": links,
            "merges": [[int(a), int(b), round(1.0 - float(h), 4), int(c)] for a, b, h, c in Z],
            "clusters": clusters}
