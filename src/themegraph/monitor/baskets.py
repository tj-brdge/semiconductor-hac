"""Equal-weight theme basket indexes (docs/MONITOR.md, "Basket method").

Prices: the store's closes are dividend-adjusted and converted to USD at ingest
(data/fmp.py: `historical-price-eod/dividend-adjusted`, `to_usd`), so every
series here is a USD total-return series, benchmarks included.

Method, per basket version:
- Entry: the first benchmark (US) session strictly after the version's
  `entry_after` moment (publication time for live versions, the month-end for
  reconstructed ones), at that session's close. A constituent whose venue did not
  trade that day is bought at its venue's next close within `stale_sessions`;
  until then its capital is held as cash. A constituent with no close in that
  time is "not bought": its capital stays as cash for the life of the version
  and is shown as such, never dropped from the basket.
- Equal starting capital per constituent, then buy and hold (no rebalancing).
  Share prices are never averaged.
- Each day a constituent is valued at its last close. Missing closes on days its
  venue traded (eval/returns.py: `venue_sessions`, verified closures only) count
  as stale sessions; the index keeps the last close and the day's coverage says
  how much of the basket's value had a fresh price.
- An acquired constituent with recorded terms becomes the successor's shares plus
  cash; without terms it is held at its last close as cash (flagged).
- A new version starts at its own entry close with the previous index level, so
  the theme index is chain-linked: later-discovered companies only count from
  the day they were added. Nothing is backfilled.

Benchmarks are bought and held from the first version's entry on the same basis.
Each index day carries `basis`: reconstructed (before the registry went live) or
live (observed after publication).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..eval.returns import venue_sessions

TRAILING = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}


class PriceBook:
    """Closes on the benchmark calendar plus each venue's open sessions."""

    def __init__(self, closes: pd.DataFrame, calendar_symbol: str, venues: dict[str, str],
                 delisted: dict | None = None, successors: dict | None = None,
                 stale_sessions: int = 5):
        closes = closes.sort_index()
        self.closes = closes
        self.cal = closes[calendar_symbol].dropna().index
        self.venues = venues
        self.sessions = venue_sessions(closes, venues, self.cal)
        self.delisted = {k: pd.Timestamp(v, tz="UTC") for k, v in (delisted or {}).items()}
        self.successors = successors or {}
        self.stale = stale_sessions
        self.last_date = self.cal[-1] if len(self.cal) else None
        # last close (carried) and the date it came from, on the calendar
        self.carry = closes.reindex(closes.index.union(self.cal)).ffill().reindex(self.cal)
        when = closes.notna().apply(lambda col: col.index.where(col))
        when = when.reindex(closes.index.union(self.cal)).ffill().reindex(self.cal)
        self.when = when

    def missing_sessions(self, sym: str) -> pd.Series:
        """Per calendar day: venue sessions since the constituent's last close."""
        vs = self.sessions.get(self.venues.get(sym, "USD"), self.cal)
        last = self.when[sym] if sym in self.when else pd.Series(pd.NaT, index=self.cal)
        v = pd.DatetimeIndex(vs).tz_convert(None).to_numpy()
        pos_v = np.searchsorted(v, self.cal.tz_convert(None).to_numpy(), side="right")
        lastv = pd.to_datetime(last, utc=True).dt.tz_convert(None).to_numpy()
        pos_l = np.searchsorted(v, lastv, side="right")
        out = np.where(pd.isna(lastv), 10**6, pos_v - pos_l)
        return pd.Series(out, index=self.cal)


def _segment(book: PriceBook, members: list[str], entry: pd.Timestamp,
             end: pd.Timestamp | None) -> dict:
    """Value path of one buy-and-hold version (capital 1, split equally)."""
    cal = book.cal[(book.cal >= entry) & ((book.cal <= end) if end is not None else True)]
    n = len(members)
    value = pd.DataFrame(0.0, index=cal, columns=members)
    fresh = pd.DataFrame(False, index=cal, columns=members)
    status, info = {}, {}
    for s in members:
        px = book.closes[s].dropna() if s in book.closes else pd.Series(dtype=float)
        later = px.loc[px.index >= entry]
        miss_at_entry = book.missing_sessions(s) if s in book.closes else None
        if not len(later) or (miss_at_entry is not None and len(cal) and
                              (later.index[0] > cal[min(book.stale, len(cal) - 1)])):
            status[s] = "not_bought"
            value[s] = 1.0 / n
            info[s] = {"entry_date": None, "entry_price": None}
            continue
        d0, p0 = later.index[0], float(later.iloc[0])
        units = (1.0 / n) / p0
        carry = book.carry[s].reindex(cal)
        v = units * carry
        v[cal < d0] = 1.0 / n  # cash until bought
        miss = book.missing_sessions(s).reindex(cal)
        fr = (miss <= 0) | (cal < d0)
        d = book.delisted.get(s)
        st = "held"
        if d is not None and d <= cal[-1]:
            succ = book.successors.get(s)
            after = cal >= d
            if succ is not None:
                sp = book.carry[succ.symbol].reindex(cal) if succ.symbol in book.carry \
                    else pd.Series(np.nan, index=cal)
                v[after] = units * (succ.shares * sp[after] + succ.cash)
                fr[after] = sp[after].notna()
                st = "converted"
            else:
                fr[after] = True  # held as cash at the last close
                st = "delisted_cash"
        value[s] = v.fillna(1.0 / n)
        fresh[s] = fr & v.notna()
        status[s] = st
        info[s] = {"entry_date": str(d0.date()), "entry_price": p0}
    total = value.sum(axis=1)
    cov = (value.where(fresh, 0.0).sum(axis=1) / total).where(total > 0)
    def missing_now(s):
        if status.get(s) == "converted":
            succ = book.successors[s].symbol
            return int(book.missing_sessions(succ).iloc[-1]) if succ in book.closes else None
        if status.get(s) == "delisted_cash":
            return 0  # held as cash: nothing left to price
        return int(book.missing_sessions(s).iloc[-1]) if s in book.closes else None
    return {"cal": cal, "value": value, "total": total, "coverage": cov,
            "status": status, "info": info, "missing": {s: missing_now(s) for s in members}}


def entry_session(book: PriceBook, after: str) -> pd.Timestamp | None:
    t = pd.Timestamp(after)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    later = book.cal[book.cal > t]
    return later[0] if len(later) else None


def theme_performance(theme: dict, book: PriceBook, benchmarks: list[str],
                      go_live: str | None) -> dict:
    """Chain-linked index, benchmarks, returns, contributions and coverage."""
    versions = theme["versions"]
    entries = [entry_session(book, v["entry_after"]) for v in versions]
    if not versions or entries[0] is None:
        return {"status": "awaiting_entry",
                "note": "no benchmark session after publication yet"}
    levels, cov, segs = [], [], []
    level = 100.0
    for i, v in enumerate(versions):
        e = entries[i]
        if e is None:
            break
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        seg = _segment(book, [c["symbol"] for c in v["constituents"]], e, nxt)
        idx = level * seg["total"] / seg["total"].iloc[0]
        if nxt is not None:
            idx, c = idx.loc[idx.index < nxt], seg["coverage"].loc[seg["coverage"].index < nxt]
            level = float(level * seg["total"].loc[nxt] / seg["total"].iloc[0]) \
                if nxt in seg["total"].index else float(idx.iloc[-1])
        else:
            c = seg["coverage"]
        levels.append(idx)
        cov.append(c)
        segs.append((v, e, seg))
    series = pd.concat(levels)
    coverage = pd.concat(cov)
    first = entries[0]
    bench = {}
    for b in benchmarks:
        if b in book.carry:
            px = book.carry[b].loc[series.index]
            bench[b] = 100.0 * px / book.carry[b].loc[first]
    live_from = pd.Timestamp(go_live) if go_live else None
    if live_from is not None and live_from.tzinfo is None:
        live_from = live_from.tz_localize("UTC")
    rows = []
    for d, val in series.items():
        r = {"date": str(d.date()), "theme": round(float(val), 4),
             "coverage": round(float(coverage.get(d, np.nan)), 4)
             if pd.notna(coverage.get(d, np.nan)) else None,
             "basis": "live" if live_from is not None and d > live_from else "reconstructed"}
        for b, s in bench.items():
            r[b] = round(float(s.loc[d]), 4) if pd.notna(s.loc[d]) else None
        rows.append(r)

    def ret(s: pd.Series, k: int | None = None):
        s = s.dropna()
        if k is None:
            return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) > 1 else None
        return float(s.iloc[-1] / s.iloc[-1 - k] - 1) if len(s) > k else None

    returns = {"since_publication": {"theme": ret(series),
                                     **{b: ret(s) for b, s in bench.items()}}}
    for name, k in TRAILING.items():
        returns[name] = {"theme": ret(series, k), **{b: ret(s, k) for b, s in bench.items()}}
    for v in returns.values():
        for b in bench:
            v[f"excess_vs_{b}"] = (v["theme"] - v[b]) if v["theme"] is not None \
                and v.get(b) is not None else None
    v, e, seg = segs[-1]
    val = seg["value"].iloc[-1]
    tot = float(seg["total"].iloc[-1])
    n = len(v["constituents"])
    constituents = []
    for c in v["constituents"]:
        s = c["symbol"]
        r = float(val[s] * n - 1)  # value of a 1/n stake relative to its cost
        miss = seg["missing"][s]
        state = seg["status"][s]
        price = ("not_bought" if state == "not_bought" else
                 "fresh" if miss is not None and miss <= 0 else
                 "lagging" if miss is not None and miss <= book.stale else "stale")
        constituents.append({**c, "start_weight": round(1 / n, 6),
                             "weight_now": round(float(val[s] / tot), 6),
                             "return_since_entry": round(r, 6),
                             "contribution": round(r / n, 6), "holding": state,
                             "price_status": price, "sessions_since_close": miss,
                             **seg["info"][s]})
    part = _participation(book, [c["symbol"] for c in v["constituents"]], benchmarks)
    latest = coverage.iloc[-1] if len(coverage) else None
    return {"status": "ok", "method": "equal_weight_buy_and_hold_chain_linked",
            "price_basis": "USD, dividend-adjusted (total return)",
            "first_entry": str(first.date()), "current_version": v["version"],
            "current_entry": str(e.date()), "series": rows, "returns": returns,
            "constituents": constituents, "participation_3m": part,
            "coverage_latest": round(float(latest), 4) if latest is not None else None,
            "last_price_date": str(series.index[-1].date())}


def _participation(book: PriceBook, members: list[str], benchmarks: list[str]) -> dict:
    k = TRAILING["3m"]
    if len(book.cal) <= k:
        return {"eligible": 0}
    a, b = book.cal[-1 - k], book.cal[-1]
    r = {}
    for s in members:
        if s in book.carry and pd.notna(book.carry[s].get(a)) and pd.notna(book.carry[s].get(b)):
            r[s] = book.carry[s][b] / book.carry[s][a] - 1
    spy = benchmarks[0] if benchmarks else None
    rb = (book.carry[spy][b] / book.carry[spy][a] - 1) if spy in book.carry else None
    return {"eligible": len(r), "positive": sum(x > 0 for x in r.values()),
            f"beat_{spy}": sum(x > rb for x in r.values()) if rb is not None else None}


def market_history(perf: dict, snapshots: list[str]) -> list[dict]:
    """3-month return and excess vs the first benchmark at each month-end, from the
    index itself (for 'gaining / losing market strength')."""
    if perf.get("status") != "ok":
        return []
    s = pd.DataFrame(perf["series"]).set_index("date")
    s.index = pd.to_datetime(s.index)
    b = [c for c in s.columns if c not in ("theme", "coverage", "basis")]
    out = []
    for t in snapshots:
        d = pd.Timestamp(t).tz_localize(None).normalize()
        hist = s.loc[s.index <= d]
        if len(hist) <= TRAILING["3m"]:
            continue
        a = hist.iloc[-1 - TRAILING["3m"]]
        z = hist.iloc[-1]
        row = {"t": t, "ret_3m": float(z["theme"] / a["theme"] - 1)}
        if b and pd.notna(z[b[0]]) and pd.notna(a[b[0]]):
            row[f"excess_3m_vs_{b[0]}"] = row["ret_3m"] - float(z[b[0]] / a[b[0]] - 1)
        out.append(row)
    return out
