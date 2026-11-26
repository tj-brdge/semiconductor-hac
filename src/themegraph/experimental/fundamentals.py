"""First-reported fundamentals from the SEC "Financial Statement Data Sets" (experiment).

Pure functions over the SUB (one row per filing) and NUM (one row per number) tables
of a quarterly data-set zip; field names follow https://www.sec.gov/files/aqfs.pdf.
Nothing here is used by the monitor (see the package docstring).

Rules:

- Entity-wide facts only: rows with a co-registrant or a segment are dropped, and so
  are company-specific tags (NUM.version is then the accession number).
- A fact is keyed on (company, metric, period end date, duration in quarters, unit).
  Fiscal-year labels (fy, fp) are never read. Dates are rounded to the nearest month
  end, as the SEC does for `ddate` and `period`, so a 52/53-week year keeps one key.
- The filing with the earliest acceptance time gives the first-reported value, and
  that time is when the value became available. A later filing with a different value
  is a dated revision; it never replaces the first value.
- Acceptance times are US/Eastern wall-clock and are converted to UTC exactly as
  `themegraph.data.edgar.accepted_utc` does. No filing lag is added here.
- A missing value stays missing. Nothing is filled, carried forward or set to zero.
"""

from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path

import pandas as pd

# Ordered candidate tags per metric: the first one present in a filing is used.
# us-gaap names first, then the IFRS name (20-F filers). The names follow the task
# brief and the published taxonomies; they are NOT yet verified against real data
# (`tag_usage` reports what the data actually contains).
CANDIDATES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "Revenue",
    ),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
                        "CostOfSales"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"),
    "inventory": ("InventoryNet", "Inventories"),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    ),
}
METRICS = ("revenue", "gross_profit", "operating_income", "inventory", "capex")
STOCK_METRICS = ("inventory",)  # point-in-time (qtrs == 0); every other metric is a flow
TAXONOMIES = ("us-gaap", "ifrs")
# Tags worth reading to see what a company uses instead of a candidate.
RELATED = re.compile(r"Revenue|CostOf|GrossProfit|OperatingIncome|FromOperatingActivities"
                     r"|Inventor|PaymentsToAcquire|PurchaseOfProperty")

KEY = ["cik", "metric", "period_end", "qtrs", "uom"]
QUARTERLY_FORMS = ("10-K", "10-Q", "10-KT", "10-QT")
ANNUAL_ONLY_FORMS = ("20-F", "40-F")


# ----------------------------------------------------------------- dates

def accepted_utc(values: pd.Series) -> pd.Series:
    """Eastern wall-clock acceptance time to UTC; same rule as data/edgar.py (a time in
    the repeated hour when clocks go back is ambiguous and becomes NaT)."""
    naive = pd.to_datetime(values.astype(str).str.replace("Z", "", regex=False),
                           errors="coerce")
    return naive.dt.tz_localize("America/New_York", ambiguous="NaT",
                                nonexistent="shift_forward").dt.tz_convert("UTC")


def month_end(dates: pd.Series) -> pd.Series:
    """Nearest month end (the SEC's rounding for `ddate` and `period`); idempotent."""
    d = pd.to_datetime(dates.astype(str).str.strip(), format="mixed", errors="coerce")
    return (d - pd.Timedelta(days=15)) + pd.offsets.MonthEnd(0)


# ----------------------------------------------------------------- reading

def _member(zf: zipfile.ZipFile, name: str) -> str:
    for n in zf.namelist():
        if n.rsplit("/", 1)[-1].lower() == name:
            return n
    raise FileNotFoundError(f"{name} not found in {zf.filename}")


def read_dataset(zip_path: Path, ciks: set[int] | None = None,
                 chunksize: int = 500_000) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Raw SUB and NUM rows (all strings) for `ciks` from one quarterly zip. NUM is
    read in chunks and cut to the companies' filings and to candidate or related
    standard-taxonomy tag names, so a 3-million-row file fits in memory."""
    opts = dict(sep="\t", dtype=str, quoting=csv.QUOTE_NONE, keep_default_na=False,
                encoding="utf-8", encoding_errors="replace")
    wanted = {t for tags in CANDIDATES.values() for t in tags}
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(_member(zf, "sub.txt")) as fh:
            sub = pd.read_csv(fh, **opts)
        if ciks is not None:
            sub = sub.loc[pd.to_numeric(sub["cik"], errors="coerce").isin(ciks)]
        adsh = set(sub["adsh"])
        parts = []
        with zf.open(_member(zf, "num.txt")) as fh:
            for chunk in pd.read_csv(fh, chunksize=chunksize, **opts):
                keep = chunk["adsh"].isin(adsh) & (
                    chunk["tag"].isin(wanted) | chunk["tag"].str.contains(RELATED))
                parts.append(chunk.loc[keep])
    num = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return sub.reset_index(drop=True), num


def prepare_sub(sub: pd.DataFrame) -> pd.DataFrame:
    """One typed row per filing. `available_at` is the acceptance time in UTC; where
    that is missing or ambiguous it falls back to 05:00 UTC on the day after the filing
    date (the end of that day in New York, the later of the two possible offsets)."""
    out = sub.drop_duplicates("adsh").copy()
    out["cik"] = pd.to_numeric(out["cik"], errors="coerce").astype("Int64")
    out = out.dropna(subset=["cik"])
    out["cik"] = out["cik"].astype(int)
    out["form"] = out["form"].astype(str).str.strip().str.upper()
    out["filing_period"] = month_end(out["period"])
    out["filed"] = pd.to_datetime(out["filed"].astype(str).str.strip(), format="mixed",
                                  errors="coerce")
    out["accepted_at"] = accepted_utc(out["accepted"])
    fallback = (out["filed"] + pd.Timedelta(days=1, hours=5)).dt.tz_localize("UTC")
    out["available_at"] = out["accepted_at"].fillna(fallback)
    cols = ["adsh", "cik", "name", "form", "filing_period", "filed", "accepted",
            "accepted_at", "available_at", "prevrpt"]
    for c in cols:
        if c not in out.columns:
            out[c] = None
    return out[cols].reset_index(drop=True)


def prepare_num(num: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Entity-wide, standard-taxonomy numbers with typed columns, and counts of what
    was dropped. A file without a `segments` column cannot be checked for segment rows;
    that is reported in the counts, not assumed away."""
    n = num.copy()
    info = {"rows": len(n), "has_segments_column": "segments" in n.columns}

    def blank(col: str) -> pd.Series:
        if col not in n.columns:
            return pd.Series(True, index=n.index)
        return n[col].isna() | (n[col].astype(str).str.strip() == "")

    entity = blank("coreg") & blank("segments")
    info["dropped_segment_or_coreg"] = int((~entity).sum())
    standard = n["version"].astype(str).str.lower().str.startswith(TAXONOMIES)
    info["dropped_custom_tags"] = int((entity & ~standard).sum())
    n = n.loc[entity & standard].copy()
    n["period_end"] = month_end(n["ddate"])
    n["qtrs"] = pd.to_numeric(n["qtrs"], errors="coerce")
    n["value"] = pd.to_numeric(n["value"], errors="coerce")
    n["uom"] = n["uom"].astype(str).str.strip()
    ok = n["period_end"].notna() & n["qtrs"].notna() & n["value"].notna()
    info["dropped_no_value_or_date"] = int((~ok).sum())
    n = n.loc[ok, ["adsh", "tag", "version", "period_end", "qtrs", "uom", "value"]]
    n["qtrs"] = n["qtrs"].astype(int)
    n = n.drop_duplicates(["adsh", "tag", "period_end", "qtrs", "uom", "value"])
    # One filing giving two different entity-wide values for the same tag, period and
    # unit cannot be resolved here: drop both and count them (missing, not guessed).
    dup = n.duplicated(["adsh", "tag", "period_end", "qtrs", "uom"], keep=False)
    info["dropped_ambiguous_in_filing"] = int(dup.sum())
    return n.loc[~dup].reset_index(drop=True), info


# ----------------------------------------------------------------- metrics

def resolve_metrics(num: pd.DataFrame, sub: pd.DataFrame,
                    candidates: dict[str, tuple[str, ...]] = CANDIDATES) -> pd.DataFrame:
    """One row per (filing, metric, period, duration, unit): the value under the first
    candidate tag that the filing carries. Gross profit is the GrossProfit tag, else
    revenue minus cost of revenue from the same filing, period and unit."""
    n = num.merge(sub[["adsh", "cik"]], on="adsh", how="inner")
    per_filing = ["adsh", "period_end", "qtrs", "uom"]
    frames = []
    for metric, tags in candidates.items():
        rank = {t: i for i, t in enumerate(tags)}
        m = n.loc[n["tag"].isin(tags)].copy()
        stock = metric in STOCK_METRICS
        m = m.loc[(m["qtrs"] == 0) if stock else m["qtrs"].between(1, 4)]
        m["rank"] = m["tag"].map(rank)
        m = m.sort_values("rank", kind="stable").drop_duplicates(per_filing)
        m["metric"] = metric
        frames.append(m.drop(columns=["rank", "version"]))
    facts = pd.concat(frames, ignore_index=True)
    rev = facts.loc[facts["metric"] == "revenue"]
    cost = facts.loc[facts["metric"] == "cost_of_revenue"]
    d = rev.merge(cost, on=["cik", *per_filing], suffixes=("", "_cost"))
    d["value"] = d["value"] - d["value_cost"]
    d["tag"] = "derived:" + d["tag"] + "-" + d["tag_cost"]
    d["metric"] = "gross_profit"
    have = facts.loc[facts["metric"] == "gross_profit", per_filing].assign(_tagged=True)
    d = d.merge(have, on=per_filing, how="left")
    d = d.loc[d["_tagged"].isna(), facts.columns]
    return pd.concat([facts, d], ignore_index=True)


def first_reported(facts: pd.DataFrame, sub: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(first, revisions). `first` has one row per key: the value in the filing with the
    earliest acceptance time, available from that time. `revisions` has one row for
    each later filing whose value differs from the value last reported before it, with
    that filing's own time. Kinds: `amendment` (a .../A form), `later_comparative` (a
    filing for a later period restating this one), `same_period_refiling` (another
    form for the same period, for example an 8-K recast)."""
    f = facts.merge(sub[["adsh", "form", "filing_period", "available_at"]], on="adsh")
    f = f.dropna(subset=["available_at"])
    f = f.sort_values([*KEY, "available_at", "adsh"], kind="stable").reset_index(drop=True)
    g = f.groupby(KEY, sort=False)
    f["seq"] = g.cumcount()
    f["prior_value"] = g["value"].shift()
    for col in ("value", "tag", "adsh"):
        f[f"first_{col}"] = g[col].transform("first")
    f["n_filings"] = g["adsh"].transform("size")
    f["own_period"] = f["filing_period"] == f["period_end"]
    first = f.loc[f["seq"] == 0].copy()
    # The earliest filing seen is for a later period: the original report is not in the
    # loaded quarters, so this "first" value may already be a restated one.
    first["first_is_comparative"] = first["filing_period"] > first["period_end"]
    rev = f.loc[(f["seq"] > 0) & (f["value"] != f["prior_value"])].copy()
    rev["kind"] = "same_period_refiling"
    rev.loc[rev["filing_period"] > rev["period_end"], "kind"] = "later_comparative"
    rev.loc[rev["form"].str.endswith("/A"), "kind"] = "amendment"
    rev["change"] = rev["value"] - rev["prior_value"]
    rev["rel_change"] = rev["change"] / rev["prior_value"].abs().where(rev["prior_value"] != 0)
    rev["tag_changed"] = rev["tag"] != rev["first_tag"]
    first = first.merge(rev.groupby(KEY).size().rename("n_revisions").reset_index(),
                        on=KEY, how="left")
    first["n_revisions"] = first["n_revisions"].fillna(0).astype(int)
    fcols = [*KEY, "value", "available_at", "adsh", "form", "tag", "own_period",
             "first_is_comparative", "n_filings", "n_revisions"]
    rcols = [*KEY, "first_value", "prior_value", "value", "change", "rel_change",
             "available_at", "adsh", "form", "kind", "own_period", "tag", "tag_changed",
             "first_adsh"]
    return first[fcols].reset_index(drop=True), rev[rcols].reset_index(drop=True)


# ----------------------------------------------------------------- single quarters

def single_quarters(first: pd.DataFrame, revisions: pd.DataFrame,
                    metrics: tuple[str, ...] | None = None) -> pd.DataFrame:
    """Single-quarter flows by period end date. A value reported for one quarter is
    used as is. Otherwise the quarter is a cumulative figure (2, 3 or 4 quarters) minus
    the cumulative figure one quarter shorter that ends 70 to 115 days earlier: this
    covers year-to-date cash-flow items and Q4 = full year minus nine months. Its
    availability is the later of the two first-reported times, and both numbers are
    taken as they were known at that time (a revision published by then is used; one
    published after is not)."""
    metrics = metrics or tuple(m for m in first["metric"].unique() if m not in STOCK_METRICS)
    history: dict[tuple, list] = {}
    for r in revisions.sort_values("available_at").itertuples(index=False):
        k = (r.cik, r.metric, r.period_end, r.qtrs, r.uom)
        history.setdefault(k, []).append((r.available_at, r.value))

    def known(row, when) -> float:
        value = row.value
        for ts, v in history.get((row.cik, row.metric, row.period_end, row.qtrs, row.uom), []):
            if ts <= when:
                value = v
        return value

    out = []
    flows = first.loc[first["metric"].isin(metrics) & first["qtrs"].between(1, 4)]
    for (cik, metric, uom), grp in flows.groupby(["cik", "metric", "uom"], sort=True):
        by = {q: g.sort_values("period_end") for q, g in grp.groupby("qtrs")}
        base = {"cik": cik, "metric": metric, "uom": uom}
        direct = set()
        for r in by.get(1, grp.iloc[:0]).itertuples(index=False):
            direct.add(r.period_end)
            out.append({**base, "period_end": r.period_end, "value": r.value,
                        "available_at": r.available_at, "method": "reported",
                        "adsh": r.adsh, "adsh_prior": None})
        for n in (2, 3, 4):
            if n not in by or n - 1 not in by:
                continue
            prev = by[n - 1]
            for r in by[n].itertuples(index=False):
                if r.period_end in direct:
                    continue
                gap = (r.period_end - prev["period_end"]).dt.days
                near = prev.loc[gap.between(70, 115)]
                if near.empty:
                    continue
                p = near.iloc[((r.period_end - near["period_end"]).dt.days - 91).abs().argmin()]
                when = max(r.available_at, p["available_at"])
                out.append({**base, "period_end": r.period_end,
                            "value": known(r, when) - known(p, when), "available_at": when,
                            "method": "fy_minus_9m" if n == 4 else "ytd_diff",
                            "adsh": r.adsh, "adsh_prior": p["adsh"]})
    cols = ["cik", "metric", "uom", "period_end", "value", "available_at", "method",
            "adsh", "adsh_prior"]
    q = pd.DataFrame(out, columns=cols)
    return q.sort_values(["cik", "metric", "uom", "period_end"]).reset_index(drop=True)


# ----------------------------------------------------------------- summaries

def filer_types(sub: pd.DataFrame, ciks: list[int]) -> pd.Series:
    """Per company, from the forms actually present: `10-K/10-Q`, `20-F/40-F`
    (annual report only), `other forms only`, or `none`."""
    forms = sub.assign(base=sub["form"].str.replace("/A", "", regex=False)).groupby("cik")["base"]
    seen = forms.agg(set).to_dict()

    def kind(cik: int) -> str:
        s = seen.get(cik, set())
        if s & set(QUARTERLY_FORMS):
            return "10-K/10-Q"
        if s & set(ANNUAL_ONLY_FORMS):
            return "20-F/40-F"
        return "other forms only" if s else "none"

    return pd.Series({c: kind(c) for c in ciks}, name="filer_type")


def company_summary(ciks: list[int], first: pd.DataFrame, revisions: pd.DataFrame,
                    quarters: pd.DataFrame, metrics: tuple[str, ...] = METRICS,
                    material: float = 0.005) -> pd.DataFrame:
    """One row per company and metric, including the ones that did not resolve: those
    have `resolved` False and every figure missing (never zero)."""
    rows = []
    for cik in ciks:
        for metric in metrics:
            f = first.loc[(first["cik"] == cik) & (first["metric"] == metric)]
            row = {"cik": cik, "metric": metric, "resolved": not f.empty}
            if f.empty:
                rows.append(row)
                continue
            unit = f["uom"].value_counts().idxmax()  # the unit with the most periods
            r = revisions.loc[(revisions["cik"] == cik) & (revisions["metric"] == metric)
                              & (revisions["uom"] == unit)]
            fu = f.loc[f["uom"] == unit]
            if metric in STOCK_METRICS:
                n_q = fu["period_end"].nunique()
            else:
                q = quarters.loc[(quarters["cik"] == cik) & (quarters["metric"] == metric)
                                 & (quarters["uom"] == unit)]
                n_q = q["period_end"].nunique()
            rel = r["rel_change"].abs().dropna()
            row.update({
                "tags": "; ".join(sorted(fu["tag"].unique())),
                "n_tags": fu["tag"].nunique(),
                "unit": unit,
                "other_units": "; ".join(sorted(set(f["uom"]) - {unit})),
                "first_period": fu["period_end"].min().date().isoformat(),
                "last_period": fu["period_end"].max().date().isoformat(),
                "n_quarters": n_q,
                "n_annual": int((fu["qtrs"] == 4).sum()),
                "n_first_from_comparative": int(fu["first_is_comparative"].sum()),
                "n_revisions": len(r),
                "n_amendment_revisions": int((r["kind"] == "amendment").sum()),
                "n_comparative_revisions": int((r["kind"] == "later_comparative").sum()),
                "n_material_revisions": int((rel >= material).sum()),
                "median_abs_rel_revision": rel.median() if len(rel) else float("nan"),
                "max_abs_rel_revision": rel.max() if len(rel) else float("nan"),
            })
            rows.append(row)
    return pd.DataFrame(rows)


def tag_usage(num: pd.DataFrame, sub: pd.DataFrame,
              candidates: dict[str, tuple[str, ...]] = CANDIDATES) -> pd.DataFrame:
    """How many companies and filings carry each candidate tag, and each related
    standard tag that is not a candidate: the check of the tag list against the data.
    A candidate that nobody uses is listed with zero companies."""
    metric_of = {t: m for m, tags in candidates.items() for t in tags}
    n = num.merge(sub[["adsh", "cik"]], on="adsh")
    use = n.groupby("tag").agg(companies=("cik", "nunique"), filings=("adsh", "nunique"))
    use = use.reindex(use.index.union(pd.Index(list(metric_of)))).fillna(0).astype(int)
    use["metric"] = [metric_of.get(t, "") for t in use.index]
    use["candidate"] = use["metric"] != ""
    return use.reset_index(names="tag").sort_values(
        ["candidate", "metric", "companies"], ascending=[False, True, False]
    ).reset_index(drop=True)


def timestamp_checks(sub: pd.DataFrame, max_lag_days: int = 60) -> tuple[dict, pd.DataFrame]:
    """Sanity of acceptance times against filing dates and period ends. A filing
    accepted after 17:30 Eastern is dated the next business day, so an acceptance date
    before the filing date is expected; one after it is not."""
    s = sub.copy()
    eastern = s["accepted_at"].dt.tz_convert("America/New_York")
    acc_date = eastern.dt.tz_localize(None).dt.normalize()
    s["accepted_minus_filed_days"] = (acc_date - s["filed"]).dt.days
    minutes = eastern.dt.hour * 60 + eastern.dt.minute
    s["after_1730_eastern"] = minutes > 17 * 60 + 30
    s["lag_days"] = (s["available_at"].dt.tz_localize(None) - s["filing_period"]).dt.days
    periodic = s.loc[s["form"].isin(QUARTERLY_FORMS + ANNUAL_ONLY_FORMS)]
    lag = periodic["lag_days"].dropna()
    diff = s["accepted_minus_filed_days"]
    stats = {
        "filings": len(s),
        "accepted_missing_or_ambiguous": int(s["accepted_at"].isna().sum()),
        "accepted_same_day_as_filed": int((diff == 0).sum()),
        "accepted_before_filed_date": int((diff < 0).sum()),
        "accepted_before_filed_and_not_after_1730": int(
            ((diff < 0) & ~s["after_1730_eastern"]).sum()),
        "accepted_after_filed_date": int((diff > 0).sum()),
        "largest_gap_days": int(diff.abs().max()) if diff.notna().any() else None,
        "flagged_previously_reported": int((s["prevrpt"].astype(str).str.strip() == "1").sum()),
        "periodic_filings": len(periodic),
        "median_lag_days": float(lag.median()) if len(lag) else None,
        "max_lag_days_threshold": max_lag_days,
        "share_lag_over_threshold": float((lag > max_lag_days).mean()) if len(lag) else None,
        "negative_lag": int((lag < 0).sum()),
    }
    cols = ["adsh", "cik", "form", "filing_period", "filed", "accepted", "available_at",
            "accepted_minus_filed_days", "after_1730_eastern", "lag_days"]
    return stats, s[cols]
