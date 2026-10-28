"""Q2 primary test: does a text-first candidate predict rising market affinity among
its issuers, relative to random issuer sets matched on segment mix?

Matching is on segment only. The design also named beta and momentum; with 4-12
issuers per segment, segment x beta x momentum cells are mostly empty, so those are
reported as balance diagnostics (see returns.momentum_gap_vs_null) rather than
matched on. Inference is descriptive: candidate episodes overlap in issuers and in
time, so they are grouped into dependence clusters before any permutation test.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from ..graph.candidates import midrank_pct
from ..market.hac import mean_pair_affinity

SESSIONS_PER_DAY = 252 / 365.25


def matched_null_sets(issuers: list[str], pool: list[str], segments: dict[str, str],
                      rng: np.random.Generator, draws: int
                      ) -> tuple[list[list[str]], dict]:
    """Random sets of distinct non-members with the members' segment mix.

    Each draw fills every segment slot from that segment's non-members where
    possible, and any remaining slots from other non-members, always without
    replacement within the draw, so a set never repeats a security or includes a
    member. If fewer non-members exist than members, matching is unsupported and no
    sets are returned. Diagnostics: `supported` and `same_segment_share` (the share
    of slots filled from the member's own segment)."""
    members = set(issuers)
    outsiders = sorted(s for s in pool if s not in members)
    k = len(issuers)
    if len(outsiders) < k or k == 0:
        return [], {"supported": False, "same_segment_share": np.nan}
    need: dict[str, int] = {}
    for s in issuers:
        need[segments.get(s, "?")] = need.get(segments.get(s, "?"), 0) + 1
    out, same = [], 0
    by_seg = {seg: [s for s in outsiders if segments.get(s, "?") == seg] for seg in need}
    for _ in range(draws):
        left = set(outsiders)
        pick = []
        for seg in sorted(need):
            cands = [s for s in by_seg[seg] if s in left]  # sorted, as outsiders are
            n = min(need[seg], len(cands))
            chosen = list(rng.choice(cands, n, replace=False)) if n else []
            pick += chosen
            left -= set(chosen)
            same += n
        rest = k - len(pick)
        if rest:
            chosen = list(rng.choice(sorted(left), rest, replace=False))
            pick += chosen
        out.append(pick)
    return out, {"supported": True, "same_segment_share": same / (k * draws)}


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
            sets, diag = matched_null_sets(members, pool, segments, rng, draws)
            null = np.array([mean_pair_affinity(a1, s) - mean_pair_affinity(a0, s)
                             for s in sets])
            rows.append({"candidate_id": r.candidate_id, "t": r.t, "h": h,
                         "issuers": members, "n": len(members), "delta": delta,
                         "null_median": float(np.median(null)) if len(null) else np.nan,
                         "null_pct": midrank_pct(null, delta) if len(null) else np.nan,
                         "null_supported": diag["supported"],
                         "null_same_segment_share": diag["same_segment_share"]})
    return pd.DataFrame(rows)


def dependence_groups(res: pd.DataFrame) -> pd.Series:
    """Cluster episodes (rows of one horizon) that share an issuer and whose
    measurement intervals [t, t + h] overlap; returns a group id per row."""
    parent = list(range(len(res)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    rows = list(res.itertuples())
    span = [(pd.Timestamp(r.t), pd.Timestamp(r.t) + pd.Timedelta(days=r.h / SESSIONS_PER_DAY),
             set(r.issuers)) for r in rows]
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = span[i], span[j]
            if a[0] <= b[1] and b[0] <= a[1] and a[2] & b[2]:
                parent[find(i)] = find(j)
    return pd.Series([find(i) for i in range(len(rows))], index=res.index)


def summarise_percentiles(res: pd.DataFrame, draws: int, seed: int,
                          column: str = "null_pct") -> dict:
    """Per horizon: the episode-mean null percentile (0.5 = no signal), and the
    same averaged within dependence groups first. The sign-flip permutation p-value
    flips whole groups, not episodes, because episodes sharing issuers over
    overlapping intervals are not independent. With few groups this is descriptive."""
    out = {}
    rng = np.random.default_rng(seed)
    for h, g in res.groupby("h"):
        g = g.loc[g[column].notna()]
        if g.empty:
            continue
        if "issuers" in g:
            grp = dependence_groups(g)
        else:  # no issuer lists: treat each episode as its own group (labelled below)
            grp = pd.Series(range(len(g)), index=g.index)
        x = g.groupby(grp)[column].mean().to_numpy() - 0.5
        flips = rng.choice([-1, 1], size=(draws, len(x)))
        p = float(np.mean((flips * x).mean(axis=1) >= x.mean()))
        out[int(h)] = {"n_episodes": int(len(g)), "n_groups": int(len(x)),
                       "mean_pct": float(g[column].mean()),
                       "group_mean_pct": float(x.mean() + 0.5),
                       "p_value_grouped": p,
                       "grouping": "issuer+interval overlap" if "issuers" in g else "none"}
    return out
