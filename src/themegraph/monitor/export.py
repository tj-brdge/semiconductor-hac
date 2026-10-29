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
import math
import os
import shutil
from pathlib import Path

import pandas as pd

from . import naming
from .baskets import market_history

SCHEMA_VERSION = 1
KEEP_RELEASES = 5
SITE = Path(__file__).with_name("site")


def _nonfinite(obj, where: str = "") -> str | None:
    """Path of the first NaN or infinity in `obj`, or None."""
    if isinstance(obj, float):
        return None if math.isfinite(obj) else where or "."
    if isinstance(obj, dict):
        items = obj.items()
    elif isinstance(obj, (list, tuple)):
        items = enumerate(obj)
    else:
        return None
    for k, v in items:
        hit = _nonfinite(v, f"{where}/{k}")
        if hit:
            return hit
    return None


def _dump(path: Path, obj) -> None:
    """Strict JSON: browsers cannot parse NaN or Infinity. A measurement that is
    legitimately unavailable is written as null with a reason; a non-finite number
    here is a calculation error, so the release fails rather than ship it."""
    bad = _nonfinite(obj)
    if bad:
        raise ValueError(f"{path.name}: non-finite number at {bad}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str, allow_nan=False))


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
    label = th.get("label") or naming.keyword_label(th)
    return {
        "schema_version": SCHEMA_VERSION, "id": th["id"], "name": th["name"],
        "title": label["title"], "summary": label["summary"],
        "label": {k: v for k, v in label.items() if k not in ("title", "summary")},
        "label_history": th.get("label_history", []),
        "description": describe(th), "keywords": th["keywords"],
        "status": th["status"], "route": th["route"], "basis": th["basis"],
        "first_seen": th["first_seen"], "published_snapshot": th["published_snapshot"],
        "published_at": th["published_at"], "last_seen": th["last_seen"],
        "name_history": th["name_history"], "status_history": th["status_history"],
        "lineage": th["lineage"], "merged_into": th.get("merged_into"),
        "status_points": status_points(th),
        "evidence": th["evidence"],
        "measures": [{k: v for k, v in r.items()} for r in th["measures"]],
        "basket_versions": th["versions"],
        "performance": perf, "pending_version": pending, "market_history": hist,
        "benchmarks": [c for c in (perf.get("series") or [{}])[0] if c not in
                       ("date", "theme", "coverage", "basis")] or mcfg.market.benchmarks,
        "sector_benchmark": th.get("sector_benchmark"),
        "newly_covered": th.get("newly_covered"),
        "market_trend": _market_trend(hist, bench, mcfg.market.trend_threshold_pp),
    }


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _month(t: str | None) -> str:
    if not t:
        return "—"
    return f"{MONTHS[int(t[5:7]) - 1]} {t[:4]}"


def _day(t: str | None) -> str:
    if not t:
        return "—"
    return f"{int(t[8:10])} {MONTHS[int(t[5:7]) - 1]} {t[:4]}"


def _list(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _pct(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "n/a"
    v = x * 100
    return f"{'+' if signed and v > 0 else '−' if v < 0 else ''}{abs(v):.1f}%"


# Status changes as points on the charts: which way discussion moved, and why.
STATUS_POINTS = {
    "strengthening": ("up", "Strengthening"),
    "cooling": ("down", "Cooling"),
    "inactive": ("down", "Went quiet"),
}


def status_points(th: dict) -> list[dict]:
    """Every change in discussion status that marks strengthening or weakening:
    to strengthening (up); to cooling or inactive (down); from cooling or inactive
    back to stable (up, recovered); from strengthening back to stable (down, growth
    levelled off). Each carries the cohort ratio that decided it, when recorded."""
    out = []
    for h in th.get("status_history", []):
        a, b = h.get("from"), h.get("to")
        if b in STATUS_POINTS:
            kind, label = STATUS_POINTS[b]
        elif b == "stable" and a in ("cooling", "inactive"):
            kind, label = "up", "Recovered"
        elif b == "stable" and a == "strengthening":
            kind, label = "down", "Growth levelled off"
        else:
            continue
        ratio = (h.get("basis") or {}).get("ratio")
        out.append({"t": h["t"], "kind": kind, "label": label, "from": a, "to": b,
                    "ratio": ratio})
    return out


def theme_overview(doc: dict) -> list[dict]:
    """A longer summary built only from what the monitor recorded: what the theme
    is, who discusses it, how it developed, where the evidence comes from and,
    separately, how its stocks did. No claim beyond the data; written labels (the
    short summary) are shown above it."""
    m = next((r for r in reversed(doc["measures"]) if r.get("present")), {})
    out = []
    kw = doc.get("keywords", [])[:6]
    if kw:
        out.append({"heading": "What it is", "text":
                    f"The passages in this theme share these distinctive terms: {_list(kw)}. "
                    + ("The title and summary above were written from cited passages."
                       if (doc.get("label") or {}).get("source") in ("claude", "curated")
                       else "No written description yet: the title comes from these terms.")})
    if m:
        by = m.get("passages_by_issuer") or {}
        tot = sum(by.values()) or 1
        top = sorted(by.items(), key=lambda kv: -kv[1])[:3]
        lead = _list([f"{s} ({n / tot:.0%})" for s, n in top])
        txt = (f"{m.get('n_issuers', len(m.get('issuers', [])))} companies discuss it, in "
               f"{_list(m.get('sectors', [])) or 'one sector'}. Most of the discussion comes "
               f"from {lead}.")
        if m.get("new_issuers_3m"):
            txt += f" New in the last three months: {_list(m['new_issuers_3m'])}."
        if m.get("recently_added"):
            txt += (f" Recently added to coverage, shown but not yet counted in growth: "
                    f"{_list(m['recently_added'])}.")
        out.append({"heading": "Who discusses it", "text": txt})
    route = {"emergence": "the emergence rule (fast growth across comparable companies)",
             "established": "the established-theme rule (persistent and broad)",
             "newly_covered": "the newly-covered rule (long-running among newly added "
                              "companies)"}.get(doc.get("route"), doc.get("route"))
    txt = (f"First seen in {_month(doc.get('first_seen'))}; entered the monitor in "
           f"{_month(doc.get('published_snapshot'))} through {route}.")
    pts = doc.get("status_points") or status_points(doc)
    if pts:
        txt += (" Changes in discussion: " + _list([f"{p['label'].lower()} ({_month(p['t'])})"
                                                    for p in pts[-4:]])
                + (f", after {len(pts) - 4} earlier changes" if len(pts) > 4 else "") + ".")
    pres = [r for r in doc["measures"][-12:] if r.get("present")]
    if pres:
        peak = max(pres, key=lambda r: r.get("share", 0))
        txt += (f" It was discussed in {m.get('persistence_12', len(pres))} of the last 12 "
                f"months, with its largest share of all monitored text "
                f"({peak['share']:.1%}) in {_month(peak['t'])}.")
    if m.get("growth_3m") is not None:
        when = "" if m["t"] == doc["measures"][-1]["t"] else f" when last seen ({_month(m['t'])})"
        txt += (f" Among comparable companies its share{when} was {m['growth_3m']:.2f}× what "
                "it had been three months earlier.")
    txt += f" Current status: {doc.get('status')}."
    out.append({"heading": "How it has developed", "text": txt})
    tone = m.get("tone") or {}
    if tone.get("direction") in ("headwind", "tailwind", "mixed"):
        out.append({"heading": "What kind of news", "text": tone_sentence(tone)})
    roles = m.get("evidence_roles") or {}
    if roles:
        mg = roles.get("management_remarks", 0) + roles.get("management_answer", 0)
        txt = (f"{mg:.0%} of its passages are management speaking (prepared remarks and "
               f"answers), {roles.get('filing', 0):.0%} come from SEC filings and "
               f"{roles.get('analyst_question', 0):.0%} are analysts' questions.")
        if roles.get("analyst_question", 0) >= 0.25:
            txt += (" A large share of questions means investors are asking about it; "
                    "that is attention, not what companies say they are doing.")
        out.append({"heading": "Where the evidence comes from", "text": txt})
    perf = doc.get("performance") or {}
    if perf.get("status") == "ok":
        r = (perf.get("returns") or {}).get("full_period") or {}
        b = (doc.get("benchmarks") or ["SPY"])[0]
        basis = {"live": "observed", "reconstructed": "simulated",
                 "mixed": "simulated, then observed"}.get(r.get("basis"), "")
        txt = (f"An equal-weight basket of its {len(perf.get('constituents', []))} companies "
               f"returned {_pct(r.get('theme'))} from {_day(r.get('start'))} to "
               f"{_day(r.get('end'))} ({basis}), against {_pct(r.get(b))} for {b}.")
        tt = (doc.get("trading_together") or {}).get("now")
        if tt:
            txt += " " + together_sentence(tt)
        txt += " Prices only: separate from how much the theme is discussed."
        out.append({"heading": "How its stocks have done", "text": txt})
    return out


def tone_sentence(tone: dict) -> str:
    """What the phrase counts say about a theme's news, and nothing more."""
    neg, ratio = tone["negative_share"], tone.get("negative_ratio")
    usual = f", {ratio:.1f}× the usual rate for this kind of text" if ratio else ""
    terms = tone.get("headwind_terms") or []
    used = f" (most often: {_list(terms[:4])})" if terms else ""
    lead = {"headwind": "A headwind: companies are describing problems.",
            "tailwind": "A tailwind: companies are describing growth and demand.",
            "mixed": "Mixed: neither problem nor growth language stands out."}[
                tone["direction"]]
    return (f"{lead} {neg:.0%} of its management and filing passages use more problem "
            f"language than growth language{usual}{used}. Counted with fixed phrase "
            "lists, which miss negation and context; read the quoted passages.")


VERDICT_TEXT = {"together": "they are trading together",
                "less": "they are less connected than usual",
                "usual": "that is not unusual"}


def together_sentence(tt: dict) -> str:
    """One sentence on the last six months' "trading together" measure, claiming no
    more than the counts: how many random baskets the theme's was above, how many
    tied, and whether the segment mix was matched exactly."""
    if not tt.get("informative"):
        return ("Whether its stocks trade together beyond the market cannot be judged for "
                f"the last six months: {tt.get('note') or 'not enough price history'}.")
    n, below, ties = tt["random_baskets"], tt["below"], tt.get("ties", 0)
    mix = ("the same size and segment mix" if tt.get("segment_matched") else
           f"the same size, drawn from the same segments where possible "
           f"({tt['same_segment_share']:.0%} of places)")
    tied = f", and equal to {ties}" if ties else ""
    return (f"Over the last six months, beyond what the market explains, its stocks moved "
            f"together more than {below:,} of {n:,} random baskets with {mix}{tied}: "
            f"{VERDICT_TEXT[tt['verdict']]}.")


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


PERIODS = ("1m", "3m", "6m", "12m", "full_period", "live", "reconstructed")


def spark(perf: dict, bench: str, sessions: int = 252, step: int = 5) -> dict | None:
    """Weekly points over the last year for the overview's small chart."""
    if perf.get("status") != "ok":
        return None
    rows = perf["series"][-sessions - 1:]
    pts = rows[::-1][::step][::-1]  # keep the latest point
    return {"bench": bench, "dates": [r["date"] for r in pts],
            "theme": [r["theme"] for r in pts], "benchmark": [r.get(bench) for r in pts],
            "live_from": next((r["date"] for r in rows if r["basis"] == "live"), None)}


def summary_row(doc: dict, bench: str) -> dict:
    m = next((r for r in reversed(doc["measures"]) if r.get("present")), {})
    perf = doc["performance"]
    r = perf.get("returns", {})
    cons = sorted(perf.get("constituents", []), key=lambda c: -(c.get("exposure") or 0))
    return {
        "id": doc["id"], "name": doc["name"], "title": doc["title"],
        "summary": doc["summary"], "label_source": doc["label"].get("source"),
        "label_status": doc["label"].get("status"), "status": doc["status"],
        "route": doc["route"], "basis": doc["basis"],
        "published_snapshot": doc["published_snapshot"], "last_seen": doc["last_seen"],
        "published_at": doc["published_at"], "benchmarks": doc["benchmarks"],
        "emergence": {"growth_3m": m.get("growth_3m"), "share": m.get("share"),
                      "comparable_issuers": len(m.get("comparable_issuers", [])),
                      "n_issuers": m.get("n_issuers"),
                      "new_issuers_3m": m.get("new_issuers_3m"),
                      "flagged_recently": any(x.get("flagged_comparable", x.get("flagged"))
                                              for x in doc["measures"][-3:])},
        "sectors": m.get("sectors", []),
        "recently_added": m.get("recently_added", []),
        "family": {"id": f["family"], "lead": f["lead"], "size": len(f["members"])}
        if (f := doc.get("family")) else None,
        "tone": {k: (m.get("tone") or {}).get(k) for k in
                 ("direction", "negative_share", "negative_ratio", "headwind_terms")},
        "key_constituents": [{"symbol": c["symbol"], "sector": c.get("sector")}
                             for c in cons[:5]],
        "strength": {"coherence": m.get("coherence"), "n_issuers": m.get("n_issuers"),
                     "n_segments": m.get("n_segments"),
                     "persistence_12": doc["measures"][-1].get("persistence_12")
                     if doc["measures"] else None,
                     "evidence_roles": m.get("evidence_roles")},
        "market": {"status": perf.get("status"), "note": perf.get("note"),
                   "returns": {k: r.get(k) for k in PERIODS},
                   "sessions": len(perf.get("series", [])),
                   "first_entry": perf.get("first_entry"),
                   "live_from": perf.get("live_from"), "trend": doc["market_trend"],
                   "coverage_latest": perf.get("coverage_latest"),
                   "n_constituents": len(perf.get("constituents", [])),
                   "trading_together_pct": ((doc.get("trading_together") or {}).get("now")
                                            or {}).get("percentile"),
                   "trading_together_verdict": ((doc.get("trading_together") or {}).get("now")
                                                or {}).get("verdict"),
                   "spark": spark(perf, bench)},
    }


def overview_lists(rows: list[dict], mcfg, snapshots: list[str] | None = None) -> dict:
    recent_snaps = set((snapshots or [])[-6:])
    live = [r for r in rows if r["status"] not in ("inactive", "merged")]
    # Headwinds: what companies describe as problems (monitor/tone.py), listed on
    # their own and left out of Emerging and Established so each theme shows once.
    # Growing ones first: a spreading problem is the case for a short idea.
    head = [r for r in live if (r.get("tone") or {}).get("direction") == "headwind"]
    headwinds = sorted(head, key=lambda r: (-(r["emergence"]["growth_3m"] or 0),
                                            -(r["tone"].get("negative_ratio") or 0)))
    hw = {r["id"] for r in head}
    live = [r for r in live if r["id"] not in hw]
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
            "headwinds": [r["id"] for r in headwinds],
            "established": [r["id"] for r in established],
            "market_gaining": [r["id"] for r in sorted(
                (r for r in trend if r["market"]["trend"]["trend"] == "gaining"),
                key=lambda r: -r["market"]["trend"]["change_pp_1m"])],
            "market_losing": [r["id"] for r in sorted(
                (r for r in trend if r["market"]["trend"]["trend"] == "losing"),
                key=lambda r: r["market"]["trend"]["change_pp_1m"])],
            "cooling": [r["id"] for r in rows if r["status"] == "cooling"],
            # Published through the newly-covered route in the last six months:
            # visible because coverage expanded, not because discussion grew.
            "newly_covered": [r["id"] for r in rows if r["route"] == "newly_covered"
                              and r["status"] not in ("inactive", "merged")
                              and r["published_snapshot"] in recent_snaps],
            "inactive": [r["id"] for r in rows if r["status"] in ("inactive", "merged")]}


def _reject(name: str):
    raise ValueError(f"non-standard JSON constant {name}")


def publish(site: Path, docs: list[dict], meta: dict, mcfg,
            snapshots: list[str] | None = None) -> str:
    """Write a release and make it current. Returns the release id."""
    site = Path(site)
    rel_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    rel = site / "data" / "releases" / rel_id
    staging = site / "data" / "releases" / f".staging-{rel_id}"
    rows = [summary_row(d, mcfg.market.benchmarks[0]) for d in docs]
    index = {"schema_version": SCHEMA_VERSION, "release": rel_id, **meta,
             "themes": rows, "lists": overview_lists(rows, mcfg, snapshots)}
    try:
        _dump(staging / "index.json", index)
        for d in docs:
            _dump(staging / "themes" / f"{d['id']}.json", d)
        # Check before going live: every listed theme has its file and parses as
        # strict JSON (a browser's JSON.parse rejects NaN and Infinity).
        for r in rows:
            json.loads((staging / "themes" / f"{r['id']}.json").read_text(),
                       parse_constant=_reject)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)  # the current release stays current
        raise
    os.replace(staging, rel)
    pointer = site / "data" / "latest.json"
    tmp = pointer.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "release": rel_id,
                               "path": f"releases/{rel_id}/"}))
    os.replace(tmp, pointer)
    site.mkdir(parents=True, exist_ok=True)
    for p in SITE.iterdir():  # the static page and its assets
        if p.is_file():
            shutil.copyfile(p, site / p.name)
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
