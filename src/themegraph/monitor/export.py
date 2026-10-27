"""Static data feed for the dashboard (schema documented in docs/MONITOR.md).

Layout under the site directory:
  index.html                       the dashboard (static, reads the feed)
  data/latest.json                 pointer to the current release (replaced atomically)
  data/status.json                 freshness and the last refresh attempt (always written)
  data/releases/<id>/index.json    overview: every published theme, summary measures
  data/releases/<id>/themes/<theme>.json   full theme record and basket index

A release is written completely, checked, and only then made current by replacing
latest.json. A failed refresh never touches the current release. The same
directory can be uploaded to any static host.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from pathlib import Path

import pandas as pd

from .baskets import market_history

SCHEMA_VERSION = 1
KEEP_RELEASES = 5
PAGE = Path(__file__).with_name("site") / "index.html"


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))


def _latest_measure(th: dict) -> dict:
    present = [r for r in th["measures"] if r.get("present")]
    return present[-1] if present else {}


def _market_trend(hist: list[dict], bench: str, threshold_pp: float) -> dict:
    key = f"excess_3m_vs_{bench}"
    pts = [h for h in hist if h.get(key) is not None]
    if len(pts) < 2:
        return {"trend": None}
    change = (pts[-1][key] - pts[-2][key]) * 100
    trend = "gaining" if change >= threshold_pp else "losing" if change <= -threshold_pp \
        else "steady"
    return {"trend": trend, "excess_3m_pp": round(pts[-1][key] * 100, 2),
            "change_pp_1m": round(change, 2)}


def theme_document(th: dict, perf: dict, snapshots: list[str], mcfg) -> dict:
    hist = market_history(perf, snapshots[-24:])
    bench = mcfg.market.benchmarks[0]
    pending = None
    if perf.get("status") == "ok":
        used = th["versions"][perf["current_version"] - 1]["constituents"]
        # The basket shown must be the basket valued.
        if [c["symbol"] for c in perf["constituents"]] != [c["symbol"] for c in used]:
            raise ValueError(f"{th['id']}: displayed basket differs from the valued basket")
        if perf["current_version"] < len(th["versions"]):
            nxt = th["versions"][-1]
            pending = {"version": nxt["version"], "entry_after": nxt["entry_after"],
                       "constituents": [c["symbol"] for c in nxt["constituents"]],
                       "note": "decided; bought at the first close after entry_after"}
    return {
        "schema_version": SCHEMA_VERSION, "id": th["id"], "name": th["name"],
        "description": describe(th), "keywords": th["keywords"],
        "status": th["status"], "route": th["route"], "basis": th["basis"],
        "first_seen": th["first_seen"], "published_snapshot": th["published_snapshot"],
        "published_at": th["published_at"], "last_seen": th["last_seen"],
        "name_history": th["name_history"], "status_history": th["status_history"],
        "lineage": th["lineage"], "merged_into": th.get("merged_into"),
        "evidence": th["evidence"],
        "measures": [{k: v for k, v in r.items()} for r in th["measures"]],
        "basket_versions": th["versions"],
        "performance": perf, "pending_version": pending, "market_history": hist,
        "market_trend": _market_trend(hist, bench, mcfg.market.trend_threshold_pp),
    }


def describe(th: dict) -> str:
    m = _latest_measure(th)
    if not m:
        return "No current discussion."
    roles = m.get("evidence_roles", {})
    mgmt = roles.get("management_remarks", 0) + roles.get("management_answer", 0)
    return (f"Discussed by {m['n_issuers']} companies ({', '.join(m['issuers'][:6])}"
            f"{'…' if m['n_issuers'] > 6 else ''}) in {m['n_passages']} passages from "
            f"{m['n_documents']} documents in the six months to {m['t'][:10]}. "
            f"Key terms: {', '.join(th['keywords'][:6])}. Evidence mix: "
            f"{mgmt:.0%} management, {roles.get('filing', 0):.0%} filings, "
            f"{roles.get('analyst_question', 0):.0%} analyst questions.")


def summary_row(doc: dict) -> dict:
    m = next((r for r in reversed(doc["measures"]) if r.get("present")), {})
    perf = doc["performance"]
    r = perf.get("returns", {})
    return {
        "id": doc["id"], "name": doc["name"], "status": doc["status"],
        "route": doc["route"], "basis": doc["basis"],
        "published_snapshot": doc["published_snapshot"], "last_seen": doc["last_seen"],
        "emergence": {"growth_3m": m.get("growth_3m"), "share": m.get("share"),
                      "comparable_issuers": len(m.get("comparable_issuers", [])),
                      "n_issuers": m.get("n_issuers"),
                      "new_issuers_3m": m.get("new_issuers_3m"),
                      "flagged_recently": any(x.get("flagged_comparable", x.get("flagged"))
                                              for x in doc["measures"][-3:])},
        "sectors": m.get("sectors", []),
        "recently_added": m.get("recently_added", []),
        "strength": {"coherence": m.get("coherence"), "n_issuers": m.get("n_issuers"),
                     "n_segments": m.get("n_segments"),
                     "persistence_12": doc["measures"][-1].get("persistence_12")
                     if doc["measures"] else None,
                     "evidence_roles": m.get("evidence_roles")},
        "market": {"status": perf.get("status"),
                   "since_publication": r.get("since_publication"),
                   "3m": r.get("3m"), "trend": doc["market_trend"],
                   "coverage_latest": perf.get("coverage_latest"),
                   "n_constituents": len(perf.get("constituents", []))},
    }


def overview_lists(rows: list[dict], mcfg) -> dict:
    live = [r for r in rows if r["status"] not in ("inactive", "merged")]
    emerging = sorted([r for r in live if r["emergence"]["flagged_recently"]
                       or (r["route"] == "emergence" and r["status"] in ("new",
                                                                         "strengthening"))],
                      key=lambda r: -(r["emergence"]["growth_3m"] or 0))
    established = sorted(
        [r for r in live if (r["strength"]["persistence_12"] or 0) >= mcfg.established.present
         and (r["strength"]["n_issuers"] or 0) >= mcfg.established.min_issuers],
        key=lambda r: (-(r["strength"]["persistence_12"] or 0), -(r["strength"]["n_issuers"]
                                                                    or 0)))
    trend = [r for r in rows if r["market"]["trend"].get("trend")]
    return {"emerging": [r["id"] for r in emerging],
            "established": [r["id"] for r in established],
            "market_gaining": [r["id"] for r in sorted(
                (r for r in trend if r["market"]["trend"]["trend"] == "gaining"),
                key=lambda r: -r["market"]["trend"]["change_pp_1m"])],
            "market_losing": [r["id"] for r in sorted(
                (r for r in trend if r["market"]["trend"]["trend"] == "losing"),
                key=lambda r: r["market"]["trend"]["change_pp_1m"])],
            "inactive": [r["id"] for r in rows if r["status"] in ("inactive", "merged")]}


def publish(site: Path, docs: list[dict], meta: dict, mcfg) -> str:
    """Write a release and make it current. Returns the release id."""
    site = Path(site)
    rel_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    rel = site / "data" / "releases" / rel_id
    staging = site / "data" / "releases" / f".staging-{rel_id}"
    rows = [summary_row(d) for d in docs]
    index = {"schema_version": SCHEMA_VERSION, "release": rel_id, **meta,
             "themes": rows, "lists": overview_lists(rows, mcfg)}
    _dump(staging / "index.json", index)
    for d in docs:
        _dump(staging / "themes" / f"{d['id']}.json", d)
    # Check before going live: every listed theme has its file and parses.
    for r in rows:
        json.loads((staging / "themes" / f"{r['id']}.json").read_text())
    os.replace(staging, rel)
    pointer = site / "data" / "latest.json"
    tmp = pointer.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "release": rel_id,
                               "path": f"releases/{rel_id}/"}))
    os.replace(tmp, pointer)
    site.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PAGE, site / "index.html")
    old = sorted(p for p in (site / "data" / "releases").iterdir()
                 if p.is_dir() and not p.name.startswith("."))[:-KEEP_RELEASES]
    for p in old:
        shutil.rmtree(p, ignore_errors=True)
    return rel_id


def write_status(site: Path, status: dict) -> None:
    p = Path(site) / "data" / "status.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=1, default=str))
    os.replace(tmp, p)


def price_date(book) -> str | None:
    return str(pd.Timestamp(book.last_date).date()) if book.last_date is not None else None
