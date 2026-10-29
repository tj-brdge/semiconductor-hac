"""Do a theme's stocks trade together? (docs/MONITOR.md, "Trading together")

For the last `window_sessions` closes, and the same span just before, on the
benchmark calendar:
- simple daily returns, linked geometrically: each company's last close is carried
  over days without one, so a move across a gap lands on its next close and the
  window's compounded return equals the price change;
- the covariance of all monitored companies with enough closes, shrunk toward a
  single-index model on the index (Ledoit & Wolf 2003, market/hac.py);
- each pair's correlation beyond what the index explains.

A theme's measure is the average beyond-index correlation among the companies in
its current basket, compared with random baskets of the same size and the same
segment mix drawn from the other companies (eval/comovement.matched_null_sets). The
headline is that percentile: with a single-index target every beyond-index
correlation is the sample value times (1 - shrinkage), so the level shrinks heavily
in short windows while the comparison does not change. Baskets are ranked on the
sample beyond-index correlation (`resid_corr`), which orders them exactly as the
shrunk values do whenever shrinkage is below 1, without their rounding error.

No verdict is given when the comparison carries no information:
- full shrinkage: the estimator puts all co-movement on the index, so every
  beyond-index correlation is zero and there is nothing to rank;
- the index did not move in the window (no single-index model);
- every random basket ties with the theme's basket.
Those measures have `informative: false`, `percentile: null` and a `note`.
Companies the index explains completely (no residual variance) are left out of the
window, like companies with too few closes.

This describes prices only. It never decides which themes are published.
"""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from ..eval.comovement import matched_null_sets
from ..market.hac import single_index_shrinkage
from .baskets import PriceBook

FULL_SHRINKAGE = 1 - 1e-9  # at or above: no beyond-index co-movement left to compare
TIE = 1e-9                 # beyond-index correlations closer than this are ties


def _window(book: PriceBook, end: int, n: int, symbols: list[str], index: str,
            min_share: float) -> dict | None:
    if end - n < 0 or index not in book.carry:
        return None
    days = book.cal[end - n:end + 1]
    span = {"start": str(days[0].date()), "end": str(days[-1].date())}
    pool = []
    for s in symbols:
        if s not in book.carry or s == index:
            continue
        carried = book.carry[s].reindex(days)
        if carried.isna().iloc[0]:
            continue  # not trading at the window's start
        own = (book.when[s].reindex(days) == pd.Series(days, index=days)).iloc[1:]
        if own.mean() >= min_share:
            pool.append(s)
    if len(pool) < 3:
        return None
    px = book.carry[pool + [index]].reindex(days)
    R = px.pct_change().iloc[1:]
    try:
        est = single_index_shrinkage(R[pool], R[index])
    except ValueError:
        return {**span, "note": f"{index} did not move in this window, so there is no "
                                "market model to measure against"}
    flat = set(est["degenerate"])
    pool = [s for s in pool if s not in flat]
    if len(pool) < 3:
        return None
    return {**span, "pool": pool, "flat": sorted(flat),
            "corr": est["resid_corr"].loc[pool, pool], "shrinkage": est["shrinkage"]}


def _avgs(corr: pd.DataFrame, sets: list[list[str]]) -> np.ndarray:
    """Average pairwise value for many equal-sized sets at once."""
    pos = {s: i for i, s in enumerate(corr.index)}
    A = corr.to_numpy()
    idx = np.array([[pos[s] for s in x] for x in sets])
    iu = np.triu_indices(idx.shape[1], 1)
    return A[idx[:, iu[0]], idx[:, iu[1]]].mean(axis=1)


def _verdict(pct: float) -> str:
    """Wording bands for the page (presentation only; nothing is selected on them)."""
    return "together" if pct >= 0.9 else "less" if pct <= 0.1 else "usual"


def _measure(w: dict | None, members: list[str], segments: dict[str, str], draws: int,
             seed: int) -> dict | None:
    if w is None:
        return None
    base = {"start": w["start"], "end": w["end"], "informative": False, "percentile": None,
            "verdict": None}
    if "pool" not in w:
        return {**base, "companies": 0, "note": w["note"]}
    m = [s for s in members if s in w["pool"]]
    left = sorted(set(members) - set(m))
    if len(m) < 2:
        return {**base, "companies": len(m), "left_out": left,
                "note": "fewer than two of the basket's companies have enough prices"}
    shrink = w["shrinkage"]
    base |= {"companies": len(m), "left_out": left, "shrinkage": round(shrink, 3)}
    if shrink >= FULL_SHRINKAGE:
        return {**base, "excess_corr": 0.0,
                "note": "over this window the market model explains all of the co-movement "
                        "among monitored companies, so there is nothing beyond the market "
                        "to compare"}
    now = float(_avgs(w["corr"], [m])[0])
    sets, info = matched_null_sets(m, w["pool"], segments, np.random.default_rng(seed), draws)
    if not sets:
        return {**base, "excess_corr": round((1 - shrink) * now, 4),
                "note": "too few other monitored companies to compare with"}
    null = _avgs(w["corr"], sets)
    below = int((null < now - TIE).sum())
    ties = int((np.abs(null - now) <= TIE).sum())
    n = len(sets)
    same = float(info["same_segment_share"])
    out = {**base, "excess_corr": round((1 - shrink) * now, 4), "random_baskets": n,
           "below": below, "ties": ties, "above": n - below - ties,
           "same_segment_share": round(same, 3), "segment_matched": same == 1.0}
    if ties == n:
        return {**out, "note": "every random basket had the same value, so the comparison "
                               "says nothing"}
    pct = (below + 0.5 * ties) / n
    return {**out, "informative": True, "percentile": round(pct, 3), "verdict": _verdict(pct)}


def trading_together(book: PriceBook, universe, baskets: dict[str, list[str]],
                     window_sessions: int, min_share: float, draws: int,
                     index: str) -> dict[str, dict]:
    """theme id -> {"now": measure, "before": measure} for each basket."""
    symbols = [i.symbol for i in universe.issuers]
    segments = {i.symbol: i.segment for i in universe.issuers}
    last = len(book.cal) - 1
    now = _window(book, last, window_sessions, symbols, index, min_share)
    before = _window(book, last - window_sessions, window_sessions, symbols, index, min_share)
    out = {}
    for tid, members in baskets.items():
        seed = zlib.crc32(tid.encode())  # fixed per theme: reruns give the same answer
        out[tid] = {"window_sessions": window_sessions, "index": index,
                    "now": _measure(now, members, segments, draws, seed),
                    "before": _measure(before, members, segments, draws, seed)}
    return out
