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
in short windows while the comparison does not change.

This describes prices only. It never decides which themes are published.
"""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from ..eval.comovement import matched_null_sets
from ..graph.candidates import midrank_pct
from ..market.hac import single_index_shrinkage
from .baskets import PriceBook


def _window(book: PriceBook, end: int, n: int, symbols: list[str], index: str,
            min_share: float) -> dict | None:
    if end - n < 0 or index not in book.carry:
        return None
    days = book.cal[end - n:end + 1]
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
    est = single_index_shrinkage(R[pool], R[index])
    return {"start": str(days[0].date()), "end": str(days[-1].date()), "pool": pool,
            "corr": est["excess_corr"], "shrinkage": est["shrinkage"]}


def _avg(corr: pd.DataFrame, members: list[str]) -> float:
    sub = corr.loc[members, members].to_numpy()
    return float(sub[np.triu_indices(len(members), 1)].mean())


def _avgs(corr: pd.DataFrame, sets: list[list[str]]) -> np.ndarray:
    """Average pairwise value for many equal-sized sets at once."""
    pos = {s: i for i, s in enumerate(corr.index)}
    A = corr.to_numpy()
    idx = np.array([[pos[s] for s in x] for x in sets])
    iu = np.triu_indices(idx.shape[1], 1)
    return A[idx[:, iu[0]], idx[:, iu[1]]].mean(axis=1)


def _measure(w: dict | None, members: list[str], segments: dict[str, str], draws: int,
             seed: int) -> dict | None:
    if w is None:
        return None
    m = [s for s in members if s in w["pool"]]
    if len(m) < 2:
        return {"start": w["start"], "end": w["end"], "companies": len(m),
                "note": "fewer than two of the basket's companies have enough prices"}
    now = _avg(w["corr"], m)
    sets, info = matched_null_sets(m, w["pool"], segments, np.random.default_rng(seed), draws)
    pct = midrank_pct(_avgs(w["corr"], sets), now) if sets else None
    return {"start": w["start"], "end": w["end"], "companies": len(m),
            "left_out": sorted(set(members) - set(m)),
            "excess_corr": round(now, 4), "percentile": None if pct is None else round(pct, 3),
            "same_segment_share": None if not sets else round(info["same_segment_share"], 3),
            "shrinkage": round(w["shrinkage"], 3), "random_baskets": len(sets)}


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
