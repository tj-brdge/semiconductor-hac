"""Macro sensitivity of baskets and market clusters (docs/MONITOR.md, "Macro
sensitivity").

Question: when short rates, long rates, oil, inflation or the dollar moved, did
this group of stocks tend to move with them, beyond the market?

For each theme basket and each market cluster: the equal-weight return of its
current companies is regressed (ordinary least squares) on the market's return
and the change in each factor, all standardised, over spans of 30, 60, 90, 180
and 360 days (`SPANS`, `span_sensitivities`), from up to twelve years of months.
The factors are changes over the span, never levels. A coefficient of +0.4
means: over a span in which the factor rose by one of its usual moves for that
span, the group's return was 0.4 of its own usual moves higher, holding the
market and the other factors fixed.

Factors use only values known at the time: CPI for month M counts in month
M + release lag (ingest.build_factors). A link is called clear when its
coefficient is larger than in 95% of slid factor histories (see
`span_sensitivities`); on noise that flags 4.5% to 7% of links, rising with the
span. With five factors, five spans and many groups, such labels do appear by
chance; groups that share companies are not independent, so a chance link tends
to show up in several at once. Read these as a description of the past, not a
forecast. Nothing here feeds detection, baskets or any threshold.
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


# -- time frames -------------------------------------------------------------------------
# Declared 2026-11-16 at the owner's request: the same question over longer spans.
# Month counts stand for the day counts shown on the site (30 days = 1 month).
SPANS = [(1, "30 days"), (2, "60 days"), (3, "90 days"), (6, "180 days"), (12, "360 days")]
HISTORY_MONTHS = 144   # up to twelve years, so the 360-day span has about eleven
                       # non-overlapping years behind it
MIN_BLOCKS = 10        # a span needs this many non-overlapping stretches of history


def _roll(a: np.ndarray, h: int) -> np.ndarray:
    """Sums over every run of `h` consecutive rows."""
    c = np.vstack([np.zeros((1, a.shape[1])), np.cumsum(a, axis=0)])
    return c[h:] - c[:-h]


def _z(a: np.ndarray) -> np.ndarray:
    sd = a.std(axis=0)
    return (a - a.mean(axis=0)) / np.where(sd == 0, 1.0, sd)


def _span_betas(Y: np.ndarray, m: np.ndarray, F: np.ndarray, h: int) -> np.ndarray:
    """Standardised coefficients (factors x groups) of h-month returns on h-month
    factor changes, with the market's h-month return as a control."""
    X = np.column_stack([np.ones(len(Y) - h + 1), _z(_roll(m[:, None], h)), _z(_roll(F, h))])
    B, *_ = np.linalg.lstsq(X, _z(_roll(Y, h)), rcond=None)
    return B[2:]


def span_sensitivities(Y: pd.DataFrame, market: pd.Series, factors: pd.DataFrame,
                       h: int) -> dict[str, dict]:
    """For each column of `Y` (monthly log returns of a group): its h-month return
    against each factor's h-month change, beyond the market, using every month as
    a starting point.

    Neighbouring spans share months, so ordinary significance tests flag far too
    many links at long spans (about 15% on noise at 360 days, measured). Instead
    the factor history is slid against the returns by every offset of at least a
    year (wrapping round), which keeps each series' own behaviour and destroys
    any real link; a link is clear when its coefficient is larger than in 95% of
    those offsets. Groups are handled by the month their history starts. A span
    needs MIN_BLOCKS non-overlapping stretches; otherwise the group has no entry.
    """
    ok = (market.notna() & factors.notna().all(axis=1)).to_numpy()
    if not ok.any():
        return {}
    last = len(ok) - 1 - int(np.argmax(ok[::-1]))            # last complete month
    first = last - int(np.argmin(ok[: last + 1][::-1])) + 1 if not ok[: last + 1].all() else 0
    rows = slice(first, last + 1)                             # last unbroken run
    m, F = market.to_numpy()[rows], factors.to_numpy()[rows]
    Yv = Y.iloc[rows]
    start = Yv.notna().to_numpy().argmax(axis=0)
    out: dict[str, dict] = {}
    for s0 in np.unique(start):
        cols = [c for c, st in zip(Yv.columns, start, strict=True)
                if st == s0 and Yv[c].iloc[s0:].notna().all() and Yv[c].iloc[s0:].std() > 0]
        n = len(m) - s0
        if not cols or (n - h + 1) / h < MIN_BLOCKS:
            continue
        y, ms, Fs = Yv[cols].to_numpy()[s0:], m[s0:], F[s0:]
        obs = _span_betas(y, ms, Fs, h)
        gap = max(h, 12)
        null = np.stack([np.abs(_span_betas(y, ms, np.roll(Fs, k, axis=0), h))
                         for k in range(gap, n - gap + 1)])
        pval = (1 + (null >= np.abs(obs)).sum(axis=0)) / (1 + len(null))
        for gi, c in enumerate(cols):
            out[c] = {"months": int(n), **{
                name: {"beta": round(float(obs[fi, gi]), 3), "p": round(float(pval[fi, gi]), 3),
                       "clear": bool(pval[fi, gi] < 0.05)}
                for fi, name in enumerate(factors.columns)}}
    return out


UNITS = {"us2y": "pp", "us10y": "pp", "wti": "%", "cpi": "%", "dxy": "%"}


def factor_moves(F: pd.DataFrame) -> dict:
    """How much each factor itself changed over each span, to the latest complete
    month: yields in percentage points, oil, the dollar and CPI in percent."""
    done = F.dropna(how="any")
    out = {}
    for k in F.columns:
        row = {}
        for h, _ in SPANS:
            if len(done) >= h:
                v = float(done[k].iloc[-h:].sum())
                row[str(h)] = round(v if UNITS[k] == "pp" else float(np.expm1(v)) * 100, 2)
        out[k] = {"unit": UNITS[k], "moves": row}
    return out


def macro_table(book, factors: pd.DataFrame | None, clusters: list[dict],
                baskets: dict[str, list[str]], index: str) -> dict:
    """Sensitivities of every theme basket and market cluster over each span in
    SPANS, from up to HISTORY_MONTHS complete months."""
    if factors is None or factors.empty:
        return {"available": False,
                "note": "No macro series stored yet (rates, oil, CPI, dollar): they are "
                        "downloaded with prices on the next refresh."}
    r = monthly_returns(book.carry, book.last_date).tail(HISTORY_MONTHS)
    if index not in r or r[index].notna().sum() < MIN_BLOCKS:
        return {"available": False, "note": "Not enough complete months of prices."}
    F = factors.copy()
    F.index = pd.DatetimeIndex(F.index).to_period("M")
    F = F.loc[~F.index.duplicated(keep="last")].reindex(r.index)
    F = F[[c for c in FACTORS if c in F.columns and F[c].notna().sum() >= MIN_BLOCKS]]
    if not F.shape[1]:
        return {"available": False, "note": "No macro series cover the price history."}
    groups = {f"c:{c['id']}": c["members"] for c in clusters}
    groups.update({f"t:{tid}": b for tid, b in baskets.items()})
    cols = {g: [s for s in mem if s in r.columns] for g, mem in groups.items()}
    Y = pd.DataFrame({g: np.log1p(r[c].mean(axis=1, skipna=True)) for g, c in cols.items() if c},
                     index=r.index)
    per: dict[str, dict] = {g: {} for g in Y.columns}
    for h, _ in SPANS:
        for g, res in span_sensitivities(Y, np.log1p(r[index]), F, h).items():
            per[g][str(h)] = res
    per = {g: v for g, v in per.items() if v}
    done = F.dropna(how="any")
    return {"available": True, "index": index, "history_months": HISTORY_MONTHS,
            "start": str(r.index[0]), "end": str(done.index[-1]) if len(done) else None,
            "spans": [{"key": str(h), "label": lab, "months": h} for h, lab in SPANS],
            "factors": [{"key": k, "label": FACTORS[k]} for k in F.columns],
            "missing": [FACTORS[k] for k in FACTORS if k not in F.columns],
            "factor_moves": factor_moves(F),
            "clusters": {g[2:]: v for g, v in per.items() if g.startswith("c:")},
            "themes": {g[2:]: v for g, v in per.items() if g.startswith("t:")}}


# -- companies against the factors, and clusters of them -----------------------------------
# Declared 2026-11-16, before any result on live data; nothing here feeds
# detection or baskets.
ALPHA = 0.05          # a link, or a company's factors taken together, is clear at this level
MIN_CORR = 0.7        # companies join a cluster when their factor-driven return paths
                      # correlate this much on average
# Months of history each span uses. Every company in a span has the full window,
# so the chance comparison can treat them together.
SPAN_WINDOW = {1: 72, 2: 72, 3: 72, 6: 96, 12: 144}
PHRASE = {"us2y": "short-term rates rise", "us10y": "long-term rates rise",
          "wti": "oil rises", "cpi": "inflation rises", "dxy": "the dollar rises"}


def _groups(C: np.ndarray, min_corr: float) -> list[np.ndarray]:
    """Average-linkage clusters from a correlation matrix; pairs and larger only."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    if len(C) < 2:
        return []
    D = np.clip(1.0 - C, 0.0, 2.0)
    np.fill_diagonal(D, 0.0)
    lab = fcluster(linkage(squareform(D, checks=False), method="average"),
                   t=1.0 - min_corr, criterion="distance")
    return [g for g in (np.flatnonzero(lab == x) for x in np.unique(lab)) if len(g) > 1]


def _name(mean: dict[str, float], members: list[str], taken: set[str]) -> str:
    ranked = sorted(mean.items(), key=lambda kv: -abs(kv[1]))
    top = [ranked[0]] + [kv for kv in ranked[1:2] if abs(kv[1]) >= 0.75 * abs(ranked[0][1])]
    name = " and ".join(f"{'rises' if b > 0 else 'falls'} when {PHRASE[k]}" for k, b in top)
    name = name[0].upper() + name[1:]
    if name in taken:
        name = f"{name} ({', '.join(members[:2])})"
    taken.add(name)
    return name


def _gap_note(r: pd.DataFrame, F: pd.DataFrame, index: str, pos: int) -> str:
    """What is missing in the month that breaks the run of complete months."""
    if pos < 0:
        return " The stored history starts there."
    gone = [FACTORS[c] for c in F.columns if pd.isna(F[c].iloc[pos])]
    if pd.isna(r[index].iloc[pos]):
        gone.append(f"{index} prices")
    return f" The run is broken at {r.index[pos]}: no value for {', '.join(gone)}." if gone else ""


def _company_span(r: pd.DataFrame, F: pd.DataFrame, sector: dict[str, str], index: str,
                  h: int, alpha: float, min_corr: float) -> dict:
    W = SPAN_WINDOW[h]
    ok = (r[index].notna() & F.notna().all(axis=1)).to_numpy()
    run = 0
    for v in ok[::-1]:                       # the latest unbroken run of complete months
        if not v:
            if run:
                break
            continue
        run += 1
    end = len(ok) - int(np.argmax(ok[::-1]))
    if run < W:
        return {"available": False,
                "note": f"Needs {W} complete months of prices and factors; {run} stored."
                        + _gap_note(r, F, index, end - run - 1)}
    rows = slice(end - W, end)
    rw, Fw = r.iloc[rows], F.iloc[rows].to_numpy()
    syms = [s for s in sector if s in rw.columns and rw[s].notna().all() and rw[s].std() > 0]
    if len(syms) < 3:
        return {"available": False, "note": "Too few companies with the full history."}
    Y, m = np.log1p(rw[syms].to_numpy()), np.log1p(rw[index].to_numpy())
    obs = _span_betas(Y, m, Fw, h)                            # factors x companies
    gap = max(h, 12)
    shifts = range(gap, W - gap + 1)
    null = np.stack([_span_betas(Y, m, np.roll(Fw, k, axis=0), h) for k in shifts])

    def strength(B, Fmat):
        """Share of a company's (standardised) return the factors account for."""
        C = np.corrcoef(_z(_roll(Fmat, h)).T) if Fmat.shape[1] > 1 else np.ones((1, 1))
        return np.einsum("kn,kl,ln->n", B, C, B), C
    q, C = strength(obs, Fw)
    qn = np.stack([strength(null[i], np.roll(Fw, k, axis=0))[0] for i, k in enumerate(shifts)])
    p_link = (1 + (np.abs(null) >= np.abs(obs)).sum(axis=0)) / (1 + len(null))
    p_joint = (1 + (qn >= q).sum(axis=0)) / (1 + len(qn))
    sens = np.flatnonzero(p_joint < alpha)
    # Chance comparison: in each slid history, how many companies would have passed
    # their own bar? Sliding keeps how companies move with each other.
    counts = (qn > np.quantile(qn, 1 - alpha, axis=0)).sum(axis=1)

    keys = list(F.columns)
    clusters, taken, member_of = [], set(), {}
    if len(sens) > 1:
        B = obs[:, sens]
        cov = B.T @ C @ B
        d = np.sqrt(np.clip(np.diag(cov), 1e-18, None))
        Cs = np.clip(cov / np.outer(d, d), -1.0, 1.0)
        for g in sorted(_groups(Cs, min_corr), key=lambda g: -len(g)):
            members = [syms[sens[k]] for k in g]
            mean = {k: round(float(B[j, g].mean()), 3) for j, k in enumerate(keys)}
            secs: dict[str, int] = {}
            for sy in members:
                secs[sector[sy]] = secs.get(sector[sy], 0) + 1
            sub = Cs[np.ix_(g, g)]
            c = {"id": len(clusters) + 1, "name": _name(mean, members, taken),
                 "members": members, "size": len(members), "betas": mean,
                 "avg_corr": round(float(sub[np.triu_indices(len(g), 1)].mean()), 3),
                 "sectors": dict(sorted(secs.items(), key=lambda kv: -kv[1]))}
            clusters.append(c)
            member_of.update(dict.fromkeys(members, c["id"]))
    companies = []
    for n, sy in enumerate(syms):
        links = {k: {"beta": round(float(obs[j, n]), 3), "clear": bool(p_link[j, n] < alpha)}
                 for j, k in enumerate(keys)}
        companies.append({"symbol": sy, "sector": sector[sy], "links": links,
                          "n_clear": sum(v["clear"] for v in links.values()),
                          "joint_p": round(float(p_joint[n]), 3),
                          "sensitive": bool(p_joint[n] < alpha),
                          "cluster": member_of.get(sy)})
    companies.sort(key=lambda c: (-c["sensitive"], -c["n_clear"], c["joint_p"], c["symbol"]))
    return {"available": True, "months": W, "start": str(rw.index[0]), "end": str(rw.index[-1]),
            "n_companies": len(syms), "left_out": len(sector) - len(syms),
            "sensitive": int(len(sens)),
            "multi": sum(c["n_clear"] >= 2 for c in companies),
            "null": {"mean": round(float(counts.mean()), 1),
                     "p95": float(np.quantile(counts, 0.95)), "offsets": len(counts)},
            "beyond_chance": bool(len(sens) > np.quantile(counts, 0.95)),
            "companies": companies, "clusters": clusters}


def company_macro(book, factors: pd.DataFrame | None, universe, index: str,
                  alpha: float = ALPHA, min_corr: float = MIN_CORR) -> dict:
    """Each company against the factors, at each span, and clusters of companies.

    1. Per company and span: its return over the span on the market's and on each
       factor's change over the span. A link to one factor is clear when its
       coefficient beats 95% of slid factor histories (as `span_sensitivities`).
       `n_clear` counts a company's clear links: two or more means it has traded
       against several factors at once (each holding the others fixed).
    2. A company is macro-sensitive when the factors together account for more of
       its return than in 95% of slid histories. Only these are clustered: the
       direction of a sensitivity that is not there is noise.
    3. Clusters: average linkage on how alike the companies' factor-driven return
       paths are, cut at `min_corr`; named after the strongest average sensitivity.
    4. Chance comparison: the number of companies that would pass in each slid
       history. `beyond_chance` says whether the real count beats 95% of them.
    """
    if factors is None or factors.empty:
        return {"available": False, "note": "No macro series stored yet."}
    r = monthly_returns(book.carry, book.last_date).tail(HISTORY_MONTHS)
    F = factors.copy()
    F.index = pd.DatetimeIndex(F.index).to_period("M")
    F = F.loc[~F.index.duplicated(keep="last")].reindex(r.index)
    F = F[[c for c in FACTORS if c in F.columns and F[c].notna().sum() >= MIN_BLOCKS]]
    if index not in r or not F.shape[1]:
        return {"available": False, "note": "Not enough complete months of prices."}
    sector = {i.symbol: universe.sectors.get(i.segment, i.segment) for i in universe.issuers}
    spans = {str(h): _company_span(r, F, sector, index, h, alpha, min_corr) for h, _ in SPANS}
    if not any(v["available"] for v in spans.values()):
        return {"available": False, "note": next(iter(spans.values()))["note"]}
    return {"available": True, "index": index, "alpha": alpha, "min_corr": min_corr,
            "factors": [{"key": k, "label": FACTORS[k]} for k in F.columns],
            "spans": [{"key": str(h), "label": lab} for h, lab in SPANS], "by_span": spans}


def fixture_factors(prices: pd.DataFrame) -> pd.DataFrame:
    """Synthetic monthly factor changes for demonstration data: seeded noise, so
    the demonstration shows the layout and (rightly) almost no clear link."""
    d = pd.to_datetime(prices["date"])
    idx = pd.period_range(d.min().to_period("M"), d.max().to_period("M"), freq="M")
    rng = np.random.default_rng(zlib.crc32(b"fixture-macro"))
    scale = {"us2y": 0.15, "us10y": 0.15, "wti": 0.08, "cpi": 0.003, "dxy": 0.02}
    return pd.DataFrame(rng.standard_normal((len(idx), len(FACTORS))),
                        index=idx.to_timestamp(how="end").normalize(),
                        columns=list(FACTORS)) * pd.Series(scale)
