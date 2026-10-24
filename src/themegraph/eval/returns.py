"""Secondary Q2 test: forward returns of candidate baskets.

Conventions (all explicit, all tested):
- The snapshot at t sees closes up to and including t. The basket is bought at the
  close of the first session after t (the entry session) and sold at the close h
  sessions later (the exit session). The move into the entry close is excluded.
- The statistic is the return of an investable equal-weight buy-and-hold basket:
  the mean of constituents' simple returns P_exit / P_entry - 1, no rebalancing.
  (A mean of log returns is not the return of any holdable portfolio and is biased
  low for volatile baskets.) Excess returns are simple-return differences.
- Basket membership is fixed at the flag. Members with no entry close are not bought
  (status not_listed: no price history yet; no_entry: a gap on the entry session);
  the basket is equal-weight over the members actually bought. If fewer than
  MIN_COVERAGE of members can be bought, the basket is insufficient_coverage.
- Every bought position must be valued at exit, otherwise the basket has no return:
    complete       closes at entry and exit
    stale_exit     no close on the exit session; last close within STALE_SESSIONS
    converted      acquired before exit with recorded terms (universe `successor`):
                   the position becomes the acquirer's shares plus cash at the
                   delisting, valued at the acquirer's exit close (cash held flat)
    delisted       delisted before exit with no recorded terms: valued at the last
                   close, held as cash. An assumption (no delisting-return data),
                   counted separately (`n_delisted_assumed`) so it can be excluded.
    unvalued       none of the above (quotes stop with no delisting record)
  A basket with any unvalued position gets status unvalued_position and no return:
  dropping the position would report a different, survivorship-biased basket as if
  it were fully observed. `worst_case` gives the return with unvalued positions at
  -100% (a lower bound), for reference only.
- A horizon whose exit session lies beyond the evaluated period's data is reported
  as incomplete_horizon, never truncated.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from ..config import Successor
from ..graph.candidates import midrank_pct
from .comovement import matched_null_sets

MIN_COVERAGE = 0.8
STALE_SESSIONS = 5


def holding_return(panel: pd.DataFrame, members: list[str], t: pd.Timestamp, h: int,
                   end: pd.Timestamp, delisted: dict[str, dt.date] | None = None,
                   successors: dict[str, Successor] | None = None) -> dict:
    """Equal-weight buy-and-hold return of `members` from the first close after t
    to the close h sessions later. `panel` is closes (sessions x symbols)."""
    delisted, successors = delisted or {}, successors or {}
    idx = panel.index
    after = np.flatnonzero(idx > t)
    out = {"entry": None, "exit": None, "value": np.nan, "worst_case": np.nan,
           "n_members": len(members), "n_used": 0, "n_bought": 0, "n_unvalued": 0,
           "n_delisted_assumed": 0, "statuses": {}}
    if not len(after):
        return {**out, "status": "incomplete_horizon"}
    e = after[0]
    x = e + h
    out["entry"] = idx[e]
    if x >= len(idx) or idx[x] > end:
        return {**out, "status": "incomplete_horizon"}
    out["exit"] = idx[x]
    rets, statuses = {}, {}
    for m in members:
        col = panel[m] if m in panel else pd.Series(np.nan, index=idx)
        p0 = col.iat[e]
        if not np.isfinite(p0):
            statuses[m] = "not_listed" if not col.iloc[:e].notna().any() else "no_entry"
            continue
        p1 = col.iat[x]
        status = "complete"
        if not np.isfinite(p1):
            seen = col.iloc[e:x + 1].dropna()
            d = delisted.get(m)
            succ = successors.get(m)
            if d is not None and pd.Timestamp(d, tz="UTC") <= idx[x]:
                px = panel[succ.symbol].iat[x] if succ is not None and succ.symbol in panel \
                    else np.nan
                if succ is not None and np.isfinite(px):
                    status, p1 = "converted", succ.shares * px + succ.cash
                else:
                    status, p1 = "delisted", seen.iat[-1]
            elif x - idx.get_loc(seen.index[-1]) <= STALE_SESSIONS:
                status, p1 = "stale_exit", seen.iat[-1]
            else:
                status, p1 = "unvalued", np.nan
        statuses[m] = status
        rets[m] = p1 / p0 - 1.0
    out["statuses"] = statuses
    out["n_bought"] = len(rets)
    valued = [r for r in rets.values() if np.isfinite(r)]
    out["n_used"] = len(valued)
    out["n_unvalued"] = len(rets) - len(valued)
    out["n_delisted_assumed"] = sum(s == "delisted" for s in statuses.values())
    if not members or len(rets) < MIN_COVERAGE * len(members):
        return {**out, "status": "insufficient_coverage"}
    if out["n_unvalued"]:
        worst = [r if np.isfinite(r) else -1.0 for r in rets.values()]
        return {**out, "worst_case": float(np.mean(worst)), "status": "unvalued_position"}
    return {**out, "value": float(np.mean(valued)), "status": "ok"}


def trailing_log_return(panel: pd.DataFrame, t: pd.Timestamp, lookback: int) -> pd.Series:
    hist = panel.loc[panel.index <= t].tail(lookback + 1)
    full = hist.notna().all()
    lr = np.log(hist.iloc[-1] / hist.iloc[0])
    return lr.where(full)


def returns_test(first_flags: pd.DataFrame, panel: pd.DataFrame, core: set[str],
                 benchmark: str, horizons: list[int], segments: dict[str, str],
                 draws: int, seed: int, end: pd.Timestamp,
                 delisted: dict[str, dt.date] | None = None,
                 momentum_lookback: int = 126,
                 successors: dict[str, Successor] | None = None) -> pd.DataFrame:
    """One row per (candidate, horizon). Baskets use the candidate's core members
    as of the flag. Nulls are segment-matched baskets of non-members; the momentum
    comparison is the same number of core names with the highest trailing return."""
    rows = []
    for r in first_flags.itertuples():
        members = sorted(s for s in r.issuers if s in core)
        if not members:
            continue
        mom_now = trailing_log_return(panel[[c for c in panel if c in core]], r.t,
                                      momentum_lookback)
        pool = mom_now.dropna().index.tolist()  # eligible: full trailing history
        mom_names = mom_now.dropna().nlargest(len(members)).index.tolist()
        for h in horizons:
            b = holding_return(panel, members, r.t, h, end, delisted, successors)
            row = {"candidate_id": r.candidate_id, "t": r.t, "h": h,
                   "n_members": len(members), "n_used": b["n_used"],
                   "n_unvalued": b["n_unvalued"], "worst_case": b["worst_case"],
                   "n_delisted_assumed": b["n_delisted_assumed"],
                   "status": b["status"], "entry": b["entry"], "exit": b["exit"],
                   "constituents": json.dumps(b["statuses"], sort_keys=True),
                   "basket": b["value"]}
            bench = holding_return(panel, [benchmark], r.t, h, end)
            mom = holding_return(panel, mom_names, r.t, h, end, delisted, successors)
            rng = np.random.default_rng([seed, h, int(pd.Timestamp(r.t).value // 10**9)])
            sets, diag = matched_null_sets(members, pool, segments, rng, draws)
            null = np.array([holding_return(panel, s, r.t, h, end, delisted, successors)["value"]
                             for s in sets], dtype=float)
            null_mom = [mom_now[s].mean() for s in sets]
            row.update({
                "benchmark": bench["value"],
                "excess_vs_benchmark": b["value"] - bench["value"],
                "excess_vs_momentum": b["value"] - mom["value"],
                "null_pct": midrank_pct(null, b["value"]) if b["status"] == "ok" else np.nan,
                "null_draws": int(np.isfinite(null).sum()),
                "null_supported": diag["supported"],
                "null_same_segment_share": diag["same_segment_share"],
                # Balance diagnostic: positive = members had stronger trailing
                # momentum than their matched nulls (matching is on segment only).
                "momentum_gap_vs_null": float(mom_now.reindex(members).mean()
                                              - np.nanmean(null_mom)) if sets else np.nan,
            })
            rows.append(row)
    return pd.DataFrame(rows)
