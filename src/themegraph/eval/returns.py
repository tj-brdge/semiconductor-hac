"""Secondary Q2 test: forward returns of candidate baskets.

Conventions (all explicit, all tested):
- Sessions are those of the horizon calendar: the benchmark's trading days (US),
  not the union of every exchange's dates. The snapshot at t sees closes up to and
  including t. The basket is bought at the close of the first calendar session after
  t (the entry session) and sold at the close h calendar sessions later (the exit
  session). The move into the entry close is excluded.
- Execution per security, by venue (exchange, proxied by currency): if the venue is
  open on the entry session the position is bought at that close; if the venue is
  closed that day (a scheduled closure: no security of that venue has a quote), it is
  bought at the venue's next session before exit. At exit, a closed venue sells at its
  last session on or before the exit session. A security without a quote on a day its
  venue traded is a genuinely missing quote, handled by the statuses below; prices
  are never forward-filled across missing quotes.
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
    converted_stale  as converted, with the successor's last close within
                   STALE_SESSIONS (its exit close missing)
    delisted       delisted before exit with no recorded terms: valued at the last
                   close, held as cash. An assumption (no delisting-return data),
                   counted separately (`n_delisted_assumed`) so it can be excluded.
    unvalued       none of the above (quotes stop with no delisting record, or the
                   successor's stock consideration cannot be priced)
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

from ..config import Successor, UniverseConfig
from ..graph.candidates import midrank_pct
from .comovement import matched_null_sets

MIN_COVERAGE = 0.8
STALE_SESSIONS = 5


def execution_context(closes: pd.DataFrame, universe: UniverseConfig,
                      calendar_symbol: str) -> dict:
    """Horizon calendar (the calendar symbol's sessions), venues (currency as exchange
    proxy; benchmarks trade in USD) and terms for acquired members."""
    if calendar_symbol not in closes or not closes[calendar_symbol].notna().any():
        raise ValueError(f"no closes for the calendar symbol {calendar_symbol!r}")
    venues = {b: "USD" for b in universe.benchmarks}
    venues.update({i.symbol: i.currency for i in universe.issuers})
    return {"calendar": closes[calendar_symbol].dropna().index, "venues": venues,
            "delisted": {i.symbol: i.delisted for i in universe.issuers if i.delisted},
            "successors": {i.symbol: i.successor for i in universe.issuers if i.successor}}


def venue_sessions(panel: pd.DataFrame, venues: dict[str, str] | None) -> dict[str, pd.Index]:
    """Dates each venue traded: any security of the venue has a close."""
    venues = venues or {}
    groups: dict[str, list[str]] = {}
    for c in panel.columns:
        groups.setdefault(venues.get(c, "USD"), []).append(c)
    return {v: panel.index[panel[cols].notna().any(axis=1).to_numpy()]
            for v, cols in groups.items()}


def holding_return(panel: pd.DataFrame, members: list[str], t: pd.Timestamp, h: int,
                   end: pd.Timestamp, delisted: dict[str, dt.date] | None = None,
                   successors: dict[str, Successor] | None = None,
                   calendar: pd.Index | None = None, venues: dict[str, str] | None = None,
                   sessions: dict[str, pd.Index] | None = None) -> dict:
    """Equal-weight buy-and-hold return of `members` from the first calendar session
    after t to the close h calendar sessions later. `panel` is closes (dates x
    symbols); `calendar` the horizon sessions (default: the panel's dates)."""
    delisted, successors = delisted or {}, successors or {}
    cal = pd.Index(calendar) if calendar is not None else panel.index
    sessions = sessions if sessions is not None else venue_sessions(panel, venues)
    venues = venues or {}
    after = np.flatnonzero(cal > t)
    out = {"entry": None, "exit": None, "value": np.nan, "worst_case": np.nan,
           "n_members": len(members), "n_used": 0, "n_bought": 0, "n_unvalued": 0,
           "n_delisted_assumed": 0, "statuses": {}}
    if not len(after):
        return {**out, "status": "incomplete_horizon"}
    e = after[0]
    x = e + h
    out["entry"] = cal[e]
    if x >= len(cal) or cal[x] > end:
        return {**out, "status": "incomplete_horizon"}
    entry, exit_ = cal[e], cal[x]
    out["exit"] = exit_
    rets, statuses = {}, {}
    for m in members:
        col = panel[m] if m in panel else pd.Series(np.nan, index=panel.index)
        vs = sessions.get(venues.get(m, "USD"), panel.index)
        # Entry: this session if the venue traded, else the venue's next session.
        d0 = entry if entry in vs else next((d for d in vs[vs > entry] if d < exit_), None)
        p0 = col.get(d0, np.nan) if d0 is not None else np.nan
        if not np.isfinite(p0):
            statuses[m] = "not_listed" if not col.loc[:entry].notna().any() else "no_entry"
            continue
        # Exit: this session if the venue traded, else its last session before it.
        d1 = exit_ if exit_ in vs else vs[(vs <= exit_) & (vs > d0)].max()
        p1 = col.get(d1, np.nan) if d1 is not None and not pd.isna(d1) else np.nan
        status = "complete"
        if not np.isfinite(p1):
            seen = col.loc[d0:exit_].dropna()
            d = delisted.get(m)
            succ = successors.get(m)
            if d is not None and pd.Timestamp(d, tz="UTC") <= exit_:
                if succ is not None:
                    status, p1 = _converted(panel, succ, exit_, sessions, venues, cal)
                else:
                    status, p1 = "delisted", seen.iat[-1]
            elif int(((cal > seen.index[-1]) & (cal <= exit_)).sum()) <= STALE_SESSIONS:
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


def _converted(panel, succ: Successor, exit_, sessions, venues, cal) -> tuple[str, float]:
    """Value at exit of what one acquired share became: `shares` of the successor plus
    `cash`. Cash-only terms need no quote. Stock terms need the successor's close on
    the exit session (or its venue's last session before it if closed), else a close
    within STALE_SESSIONS calendar sessions (stale_successor); otherwise the position
    is unvalued. The acquired company's own last close is never used for known terms."""
    if not succ.shares:
        return "converted", succ.cash
    col = panel[succ.symbol] if succ.symbol in panel else pd.Series(np.nan, index=panel.index)
    vs = sessions.get(venues.get(succ.symbol, "USD"), panel.index)
    d = exit_ if exit_ in vs else vs[vs <= exit_].max()
    px = col.get(d, np.nan) if not pd.isna(d) else np.nan
    if np.isfinite(px):
        return "converted", succ.shares * px + succ.cash
    seen = col.loc[:exit_].dropna()
    if len(seen) and int(((cal > seen.index[-1]) & (cal <= exit_)).sum()) <= STALE_SESSIONS:
        return "converted_stale", succ.shares * seen.iat[-1] + succ.cash
    return "unvalued", np.nan


def trailing_log_return(panel: pd.DataFrame, t: pd.Timestamp, lookback: int,
                        calendar: pd.Index | None = None) -> pd.Series:
    """Log return over the last `lookback` calendar sessions to t, each end taken at
    the security's last close within STALE_SESSIONS sessions (venue closures do not
    make a security ineligible); NaN where either end has no recent close."""
    cal = pd.Index(calendar) if calendar is not None else panel.index
    win = cal[cal <= t][-(lookback + 1):]
    if len(win) < lookback + 1:
        return pd.Series(np.nan, index=panel.columns)
    hist = panel.loc[(panel.index <= win[-1])]
    tol = cal[cal <= t][-(lookback + 1 + STALE_SESSIONS):]

    def last_close(at, lo):
        part = hist.loc[(hist.index <= at) & (hist.index >= lo)]
        return part.ffill().iloc[-1] if len(part) else pd.Series(np.nan, index=panel.columns)
    start = last_close(win[0], tol[0])
    stop = last_close(win[-1], win[-1 - STALE_SESSIONS] if len(win) > STALE_SESSIONS else win[0])
    return np.log(stop / start)


def returns_test(first_flags: pd.DataFrame, panel: pd.DataFrame, core: set[str],
                 benchmark: str, horizons: list[int], segments: dict[str, str],
                 draws: int, seed: int, end: pd.Timestamp,
                 delisted: dict[str, dt.date] | None = None,
                 momentum_lookback: int = 126,
                 successors: dict[str, Successor] | None = None,
                 calendar: pd.Index | None = None,
                 venues: dict[str, str] | None = None) -> pd.DataFrame:
    """One row per (candidate, horizon). Baskets use the candidate's core members
    as of the flag. Nulls are segment-matched baskets of non-members; the momentum
    comparison is the same number of core names with the highest trailing return."""
    rows = []
    sessions = venue_sessions(panel, venues)
    kw = {"calendar": calendar, "venues": venues, "sessions": sessions}
    for r in first_flags.itertuples():
        members = sorted(s for s in r.issuers if s in core)
        if not members:
            continue
        mom_now = trailing_log_return(panel[[c for c in panel if c in core]], r.t,
                                      momentum_lookback, calendar)
        pool = mom_now.dropna().index.tolist()  # eligible: full trailing history
        mom_names = mom_now.dropna().nlargest(len(members)).index.tolist()
        for h in horizons:
            b = holding_return(panel, members, r.t, h, end, delisted, successors, **kw)
            row = {"candidate_id": r.candidate_id, "t": r.t, "h": h,
                   "n_members": len(members), "n_used": b["n_used"],
                   "n_unvalued": b["n_unvalued"], "worst_case": b["worst_case"],
                   "n_delisted_assumed": b["n_delisted_assumed"],
                   "status": b["status"], "entry": b["entry"], "exit": b["exit"],
                   "constituents": json.dumps(b["statuses"], sort_keys=True),
                   "basket": b["value"]}
            bench = holding_return(panel, [benchmark], r.t, h, end, **kw)
            mom = holding_return(panel, mom_names, r.t, h, end, delisted, successors, **kw)
            rng = np.random.default_rng([seed, h, int(pd.Timestamp(r.t).value // 10**9)])
            sets, diag = matched_null_sets(members, pool, segments, rng, draws)
            null = np.array([holding_return(panel, s, r.t, h, end, delisted, successors,
                                            **kw)["value"]
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
