"""Q2 primary test: does a text-first candidate predict rising market affinity among
its issuers, relative to random issuer sets matched on segment mix?"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from ..graph.candidates import midrank_pct
from ..market.hac import mean_pair_affinity


def matched_null_sets(issuers: list[str], pool: list[str], segments: dict[str, str],
                      rng: np.random.Generator, draws: int) -> list[list[str]]:
    """Random sets with the same segment mix as `issuers`, drawn from non-members
    (falling back to the whole pool only when a segment has too few non-members)."""
    members = set(issuers)
    outsiders = [s for s in pool if s not in members]
    by_seg: dict[str, list[str]] = {}
    for s in outsiders:
        by_seg.setdefault(segments.get(s, "?"), []).append(s)
    need: dict[str, int] = {}
    for s in issuers:
        need[segments.get(s, "?")] = need.get(segments.get(s, "?"), 0) + 1
    out = []
    for _ in range(draws):
        pick = []
        for seg, k in need.items():
            cands = by_seg.get(seg, [])
            if len(cands) < k:
                cands = outsiders if len(outsiders) >= k else pool
            pick += list(rng.choice(cands, k, replace=False))
        out.append(pick)
    return out


def comovement_test(first_flags: pd.DataFrame, affinity_at: Callable, horizons: list[int],
                    segments: dict[str, str], draws: int, seed: int) -> pd.DataFrame:
    """first_flags: one row per candidate (candidate_id, t, issuers).
    affinity_at(t, h) -> affinity matrix h sessions after t (None if unavailable)."""
    rows = []
    for r in first_flags.itertuples():
        a0 = affinity_at(r.t, 0)
        if a0 is None:
            continue
        for h in horizons:
            a1 = affinity_at(r.t, h)
            if a1 is None:
                continue
            members = [s for s in r.issuers if s in a0.index and s in a1.index]
            if len(members) < 2:
                continue
            pool = [s for s in a0.index if s in a1.index]
            delta = mean_pair_affinity(a1, members) - mean_pair_affinity(a0, members)
            rng = np.random.default_rng([seed, h, int(pd.Timestamp(r.t).value // 10**9)])
            null = np.array([mean_pair_affinity(a1, s) - mean_pair_affinity(a0, s)
                             for s in matched_null_sets(members, pool, segments, rng, draws)])
            rows.append({"candidate_id": r.candidate_id, "t": r.t, "h": h,
                         "n": len(members), "delta": delta,
                         "null_median": float(np.median(null)),
                         "null_pct": midrank_pct(null, delta)})
    return pd.DataFrame(rows)


def summarise_percentiles(res: pd.DataFrame, draws: int, seed: int) -> dict:
    """Mean null percentile per horizon (0.5 under no signal) with a sign-flip
    permutation p-value for the mean exceeding 0.5."""
    out = {}
    rng = np.random.default_rng(seed)
    for h, g in res.groupby("h"):
        x = g["null_pct"].to_numpy() - 0.5
        obs = x.mean()
        flips = rng.choice([-1, 1], size=(draws, len(x)))
        p = float(np.mean((flips * x).mean(axis=1) >= obs))
        out[int(h)] = {"n": int(len(x)), "mean_pct": float(obs + 0.5), "p_value": p}
    return out
