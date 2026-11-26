"""Fundamentals feasibility test on the SEC Financial Statement Data Sets (experiment).

Not part of the monitor: it reads quarterly data-set zips, resolves five metrics for
the companies in a universe file and writes a Markdown report and CSV files to an
output folder that is not in git. Nothing it writes feeds a published figure.

    # zips already on disk (2024q4.zip, 2025q1.zip, ...)
    .venv/bin/python scripts/fundamentals_feasibility.py --zips ~/sec-zips

    # or download them (needs EDGAR_USER_AGENT, as the project's EDGAR client does)
    .venv/bin/python scripts/fundamentals_feasibility.py --download 2024q2 2024q3 2024q4 2025q1

Companies without a `cik` in the universe file are looked up in the SEC's ticker file:
pass a saved copy with --ticker-map, or set EDGAR_USER_AGENT and it is fetched once.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from themegraph.config import ROOT, load_universe
from themegraph.data.http import env_value
from themegraph.experimental import fundamentals as F

URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
PAUSE_SECONDS = 1.0  # the SEC limit is 10 requests a second; this stays far below it


def user_agent() -> str:
    ua = env_value("EDGAR_USER_AGENT")
    if not ua or "@" not in ua:
        raise SystemExit("EDGAR_USER_AGENT must name you and a contact email, per SEC "
                         "fair-access policy. Set it in your environment and run again.")
    return ua


def download(quarters: list[str], dest: Path, template: str) -> list[Path]:
    """One request per missing zip, paced, no retry: a refusal stops the run."""
    ua = user_agent()
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    with httpx.Client(headers={"User-Agent": ua}, timeout=300.0, follow_redirects=True) as c:
        for q in quarters:
            if not re.fullmatch(r"(19|20)\d\dq[1-4]", q):
                raise SystemExit(f"not a quarter name (expected e.g. 2025q1): {q}")
            path = dest / f"{q}.zip"
            paths.append(path)
            if path.exists() and zipfile.is_zipfile(path):
                print(f"  {q}: already downloaded")
                continue
            url = template.format(quarter=q)
            time.sleep(PAUSE_SECONDS)
            with c.stream("GET", url) as r:
                if r.status_code != 200:
                    raise SystemExit(f"SEC answered {r.status_code} for {url}. Not retried. "
                                     "Check the address on the SEC's data-set page "
                                     "(--url-template) and your EDGAR_USER_AGENT.")
                tmp = path.with_suffix(".part")
                with tmp.open("wb") as fh:
                    for block in r.iter_bytes(1 << 20):
                        fh.write(block)
            if not zipfile.is_zipfile(tmp):
                tmp.unlink()
                raise SystemExit(f"{url} did not return a zip file. Not retried.")
            tmp.rename(path)
            print(f"  {q}: {path.stat().st_size / 1e6:.0f} MB")
    return paths


def ticker_map(path: Path | None, out: Path) -> dict[str, int]:
    """Ticker -> CIK from the SEC's company_tickers.json: a saved copy, or one request."""
    saved = out / "company_tickers.json"
    if path is None and not saved.exists():
        ua = user_agent()
        time.sleep(PAUSE_SECONDS)
        r = httpx.get(TICKERS, headers={"User-Agent": ua}, timeout=60.0)
        if r.status_code != 200:
            raise SystemExit(f"SEC answered {r.status_code} for {TICKERS}. Not retried.")
        saved.write_bytes(r.content)
    data = json.loads((path or saved).read_text())
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def companies(universe_path: Path, tickers_path: Path | None, out: Path,
              allow_missing: bool) -> pd.DataFrame:
    u = load_universe(universe_path)
    need = [i for i in u.issuers if i.sec_filer and i.cik is None]
    tickers: dict[str, int] = {}
    if need:
        try:
            tickers = ticker_map(tickers_path, out)
        except SystemExit:
            if not allow_missing:
                print(f"{len(need)} companies have no cik in {universe_path.name}; pass "
                      "--ticker-map, set EDGAR_USER_AGENT, or use --allow-missing-cik.",
                      file=sys.stderr)
                raise
    rows = []
    for i in u.issuers:
        cik = i.cik or (tickers.get(i.symbol.replace(".", "-")) if i.sec_filer else None)
        source = "universe file" if i.cik else ("SEC ticker file" if cik else "")
        rows.append({"symbol": i.symbol, "name": i.name or i.symbol, "segment": i.segment,
                     "sec_filer": i.sec_filer, "cik": cik, "cik_source": source})
    df = pd.DataFrame(rows)
    df["cik"] = df["cik"].astype("Int64")
    return df


# ----------------------------------------------------------------- report

def table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_none_\n"
    cells = df.astype(object).where(df.notna(), "").astype(str)
    head = "| " + " | ".join(cells.columns) + " |\n|" + "---|" * len(cells.columns) + "\n"
    return head + "".join("| " + " | ".join(r) + " |\n" for r in cells.itertuples(index=False))


def pct(x) -> str:
    return "" if pd.isna(x) else f"{100 * x:.2f}%"


def hand_checks(co: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in co.itertuples():
        why = []
        if pd.isna(c.cik):
            why.append("does not file with the SEC" if not c.sec_filer else "no CIK found")
        elif c.filer_type == "none":
            why.append("no filing in the loaded quarters")
        else:
            s = summary.loc[summary["cik"] == c.cik].set_index("metric")
            missing = [m for m in F.METRICS if not s.loc[m, "resolved"]]
            if missing:
                why.append("not resolved: " + ", ".join(missing))
            ok = s.loc[s["resolved"]]
            if c.filer_type != "10-K/10-Q":
                why.append(f"{c.filer_type}: no quarterly reports")
            multi = ok.index[ok["n_tags"] > 1].tolist()
            if multi:
                why.append("more than one tag: " + ", ".join(multi))
            if (ok["other_units"] != "").any():
                why.append("more than one unit")
            if "gross_profit" in ok.index and "derived:" in ok.loc["gross_profit", "tags"]:
                why.append("gross profit derived from revenue and cost")
            if (ok["n_material_revisions"] > 0).any():
                why.append(f"{int(ok['n_material_revisions'].sum())} revisions over threshold")
            if (ok["n_first_from_comparative"] > 0).any():
                why.append("some first values come from a later filing's comparative")
        if why:
            rows.append({"symbol": c.symbol, "name": c.name, "reasons": "; ".join(why)})
    return pd.DataFrame(rows, columns=["symbol", "name", "reasons"])


def build_report(a, zips, co, summary, first, rev, quarters, usage, info, stats) -> str:
    sym = co.dropna(subset=["cik"]).set_index("cik")["symbol"].to_dict()
    n = len(co)
    out = ["# Fundamentals feasibility: measured results\n\n",
           "Generated by `scripts/fundamentals_feasibility.py`. Experimental: nothing here "
           "feeds a published figure. Every number below is measured on the files listed.\n\n",
           "## Data\n\n",
           "".join(f"- `{z.name}` ({z.stat().st_size / 1e6:.0f} MB)\n" for z in zips),
           f"- Universe: `{a.universe.name}`, {n} companies; "
           f"{int(co['cik'].notna().sum())} with a CIK "
           f"({int((co['cik_source'] == 'SEC ticker file').sum())} from the SEC ticker file); "
           f"{int((~co['sec_filer']).sum())} marked as not filing.\n",
           f"- Number rows read for these companies: {info['rows']}; dropped as segment or "
           f"co-registrant rows: {info['dropped_segment_or_coreg']}; company-specific tags: "
           f"{info['dropped_custom_tags']}; two values for one fact in one filing: "
           f"{info['dropped_ambiguous_in_filing']}.\n"]
    if not info["has_segments_column"]:
        out.append("- **At least one file has no `segments` column: segment rows could not "
                   "be excluded there.**\n")

    out.append("\n## Coverage by filer type\n\nCompanies with the metric resolved "
               "(at least one entity-wide value under a candidate tag).\n\n")
    res = summary.merge(co[["cik", "filer_type"]], on="cik")
    cov = res.pivot_table(index="filer_type", columns="metric", values="resolved",
                          aggfunc="sum").reindex(columns=list(F.METRICS)).astype(int)
    cov.insert(0, "companies", co.groupby("filer_type").size())
    cov = cov.reindex(co["filer_type"].value_counts().index).fillna(0).astype(int)
    cov.loc["all"] = cov.sum()
    out.append(table(cov.reset_index(names="filer type")))
    with_q = res.loc[res["n_quarters"].fillna(0) > 0].groupby("metric")["cik"].nunique()
    out.append("\nCompanies with at least one single quarter (reported or derived): "
               + ", ".join(f"{m} {int(with_q.get(m, 0))}" for m in F.METRICS) + f" of {n}.\n")
    if not quarters.empty:
        shown = quarters.loc[quarters["metric"].isin(F.METRICS)]
        meth = shown.groupby(["metric", "method"]).size().unstack(fill_value=0)
        out.append("\nSingle quarters by how they were obtained:\n\n"
                   + table(meth.reset_index()))

    out.append("\n## Revisions\n\n")
    if rev.empty:
        out.append("No later filing changed a first-reported value in the loaded quarters.\n")
    else:
        r = rev.assign(abs_rel=rev["rel_change"].abs())
        limit = a.material
        by = r.groupby("kind").agg(revisions=("value", "size"), companies=("cik", "nunique"),
                                   median_size=("abs_rel", "median"),
                                   over_threshold=("abs_rel", lambda x: int((x >= limit).sum())),
                                   tag_changed=("tag_changed", "sum"))
        by["median_size"] = by["median_size"].map(pct)
        out.append(f"{len(first)} first-reported values; {int((first['n_revisions'] > 0).sum())} "
                   f"were later changed. Threshold for 'over': {pct(a.material)} of the prior "
                   "value (smaller changes are often rounding).\n\n")
        out.append(table(by.reset_index()))
        top = r.loc[r["metric"].isin(F.METRICS)].sort_values("abs_rel", ascending=False)
        for kind in ("later_comparative", "amendment", "same_period_refiling"):
            ex = top.loc[top["kind"] == kind].head(8).copy()
            if ex.empty:
                continue
            ex["symbol"] = ex["cik"].map(sym)
            ex["period"] = ex["period_end"].dt.date.astype(str) + " / " + ex["qtrs"].astype(str)
            ex["size"] = ex["rel_change"].map(pct)
            ex["revised_at"] = ex["available_at"].dt.strftime("%Y-%m-%d %H:%M UTC")
            out.append(f"\nLargest `{kind}` revisions (hand-check these against the filings):\n\n")
            out.append(table(ex[["symbol", "metric", "period", "uom", "prior_value", "value",
                                 "size", "form", "adsh", "first_adsh", "revised_at"]]))

    out.append("\n## Timestamps\n\n")
    out.append("".join(f"- {k.replace('_', ' ')}: {round(v, 3) if isinstance(v, float) else v}\n"
                       for k, v in stats.items()))
    cmp_share = first["first_is_comparative"].mean() if len(first) else float("nan")
    out.append(f"- first values that come from a later filing's comparative (original not in "
               f"the loaded quarters): {pct(cmp_share)}\n")

    out.append("\n## Tags seen\n\nCandidate tags, with the number of companies using each as an "
               "entity-wide value. A candidate with 0 companies is unverified by this run.\n\n")
    out.append(table(usage.loc[usage["candidate"], ["metric", "tag", "companies", "filings"]]))
    other = usage.loc[~usage["candidate"] & (usage["companies"] >= a.min_tag_companies)]
    out.append(f"\nRelated standard tags that are not candidates, used by at least "
               f"{a.min_tag_companies} companies (possible gaps in the candidate lists):\n\n")
    out.append(table(other[["tag", "companies", "filings"]].head(40)))

    checks = hand_checks(co, summary)
    out.append(f"\n## Companies needing a hand check ({len(checks)} of {n})\n\n")
    out.append(table(checks))

    out.append("\n## Per company\n\nTag used and, in brackets, single quarters and revisions. "
               "Empty = not resolved. Full detail: `company_metrics.csv`.\n\n")
    s = summary.loc[summary["resolved"]].copy()
    s["cell"] = (s["tags"].str.replace("derived:", "d:") + " (" + s["n_quarters"].astype(int)
                 .astype(str) + "q, " + s["n_revisions"].astype(int).astype(str) + "r)")
    wide = s.pivot(index="cik", columns="metric", values="cell").reindex(columns=list(F.METRICS))
    per = co[["symbol", "filer_type", "cik"]].merge(wide, left_on="cik", right_index=True,
                                                    how="left").drop(columns="cik")
    out.append(table(per))
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--zips", type=Path, help="folder of already downloaded data-set zips")
    src.add_argument("--download", nargs="+", metavar="QUARTER", help="e.g. 2024q4 2025q1")
    p.add_argument("--universe", type=Path, default=ROOT / "config" / "tech_v2" / "universe.yaml")
    p.add_argument("--out", type=Path, default=ROOT / "runs" / "fundamentals_feasibility")
    p.add_argument("--ticker-map", type=Path, help="saved copy of SEC company_tickers.json")
    p.add_argument("--allow-missing-cik", action="store_true",
                   help="run without the companies whose CIK cannot be looked up")
    p.add_argument("--url-template", default=URL, help="download address with {quarter}")
    p.add_argument("--max-lag-days", type=int, default=60,
                   help="report the share of periodic filings accepted later than this "
                        "many days after the period end")
    p.add_argument("--material", type=float, default=0.005,
                   help="relative size from which a revision is listed as over threshold")
    p.add_argument("--min-tag-companies", type=int, default=3)
    a = p.parse_args(argv)

    a.out.mkdir(parents=True, exist_ok=True)
    if a.download:
        zips = download(a.download, a.out / "zips", a.url_template)
    else:
        zips = sorted(a.zips.glob("*.zip"))
        if not zips:
            raise SystemExit(f"no .zip files in {a.zips}")
    co = companies(a.universe, a.ticker_map, a.out, a.allow_missing_cik)
    ciks = sorted(int(c) for c in co["cik"].dropna().unique())

    subs, nums = [], []
    for z in zips:
        print(f"reading {z.name} ...")
        s, n = F.read_dataset(z, set(ciks))
        subs.append(s)
        nums.append(n)
        print(f"  {len(s)} filings, {len(n)} candidate number rows")
    sub = F.prepare_sub(pd.concat(subs, ignore_index=True))
    raw_num = pd.concat(nums, ignore_index=True)
    has_segments = all("segments" in n.columns for n in nums if len(n))
    num, info = F.prepare_num(raw_num)
    info["has_segments_column"] = has_segments
    facts = F.resolve_metrics(num, sub)
    first, rev = F.first_reported(facts, sub)
    quarters = F.single_quarters(first, rev)
    summary = F.company_summary(ciks, first, rev, quarters, material=a.material)
    co["filer_type"] = co["cik"].map(F.filer_types(sub, ciks)).fillna("none")
    usage = F.tag_usage(num, sub)
    stats, per_filing = F.timestamp_checks(sub, a.max_lag_days)

    labelled = co[["symbol", "name", "segment", "filer_type", "cik"]].merge(
        summary, on="cik", how="left")
    labelled["resolved"] = labelled["resolved"].fillna(False).astype(bool)
    labelled.to_csv(a.out / "company_metrics.csv", index=False)
    first.to_csv(a.out / "first_reported.csv", index=False)
    rev.to_csv(a.out / "revisions.csv", index=False)
    quarters.to_csv(a.out / "single_quarters.csv", index=False)
    per_filing.to_csv(a.out / "filings.csv", index=False)
    usage.to_csv(a.out / "tag_usage.csv", index=False)
    report = build_report(a, zips, co, summary, first, rev, quarters, usage, info, stats)
    (a.out / "report.md").write_text(report)
    print(f"wrote {a.out / 'report.md'} and 6 CSV files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
