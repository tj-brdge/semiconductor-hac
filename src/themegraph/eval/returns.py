"""Secondary Q2 test: forward excess returns of candidate baskets versus SOXX,
segment-matched random baskets, and a same-size momentum basket."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..graph.candidates import midrank_pct
from .comovement import matched_null_sets


def forward_return(rets: pd.DataFrame, cols: list[str], t: pd.Timestamp, h: int) -> float:
    """Equal-weight log return over the h sessions after t (entry at the next close)."""
    fut = rets.loc[rets.index > t, [c for c in cols if c in rets.columns]].head(h)
    if len(fut) < h or fut.empty:
        return float("nan")
    per_name = fut.sum(min_count=int(h * 0.9))
    return float(per_name.mean()) if per_name.notna().any() else float("nan")


def returns_test(first_flags: pd.DataFrame, rets: pd.DataFrame, core: set[str],
                 benchmark: str, horizons: list[int], segments: dict[str, str],
                 draws: int, seed: int, momentum_lookback: int = 126) -> pd.DataFrame:
    rows = []
    for r in first_flags.itertuples():
        members = [s for s in r.issuers if s in core and s in rets.columns]
        if not members:
            continue
        hist = rets.loc[rets.index <= r.t].tail(momentum_lookback)
        pool = [c for c in hist.columns if c in core and hist[c].notna().mean() > 0.9]
        for h in horizons:
            basket = forward_return(rets, members, r.t, h)
            bench = forward_return(rets, [benchmark], r.t, h)
            mom = hist[pool].sum().nlargest(len(members)).index.tolist()
            rng = np.random.default_rng([seed, h, int(pd.Timestamp(r.t).value // 10**9)])
            null = [forward_return(rets, s, r.t, h)
                    for s in matched_null_sets(members, pool, segments, rng, draws)]
            null = np.array(null, dtype=float)
            rows.append({
                "candidate_id": r.candidate_id, "t": r.t, "h": h, "n": len(members),
                "basket": basket, "excess_vs_benchmark": basket - bench,
                "excess_vs_momentum": basket - forward_return(rets, mom, r.t, h),
                "null_pct": midrank_pct(null, basket),
            })
    return pd.DataFrame(rows)
