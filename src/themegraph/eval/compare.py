"""Compare two development runs (e.g. window vs anchored topic tracking).

Comparability first: both runs must use identical inputs (canonical hashes and
vintages), embedder and configuration apart from declared overrides; otherwise the
comparison is refused. Then the pre-declared criteria (DESIGN.md, "Window vs anchored
comparison") are tabulated side by side. Detection counts come from reviewer
adjudication where it exists; automatic issuer-overlap matches are shown separately
and labelled as such, because overlap and timing alone do not show that a candidate
is the labelled theme. Outcome returns are not shown: they play no part in choosing
a tracking method.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import adjudication as A


def _load(run: Path) -> dict:
    run = Path(run)
    m = json.loads((run / "manifest.json").read_text())
    res = json.loads((run / "results.json").read_text()) if (run / "results.json").exists() \
        else {}
    entries = A.load(run / A.WORKSHEET)
    mp = run / "eval_matches.parquet"
    return {"dir": run, "manifest": m, "q1": res.get("q1", {}), "adjudication": entries,
            "matches": pd.read_parquet(mp) if mp.exists() else pd.DataFrame()}


def comparability(a: dict, b: dict) -> list[str]:
    """Reasons the runs are not comparable (empty = comparable)."""
    ma, mb = a["manifest"], b["manifest"]
    out = []
    ia, ib = ma.get("inputs", {}), mb.get("inputs", {})
    for k in ("scheme", "prices", "documents", "vintages", "input_vintage_upto"):
        if ia.get(k) != ib.get(k):
            out.append(f"inputs.{k} differ")
    for k in ("period", "end", "embedder", "snapshots"):
        if ma.get(k) != mb.get(k):
            out.append(f"{k} differs: {ma.get(k)} vs {mb.get(k)}")
    if ma.get("config_sha256") != mb.get("config_sha256"):
        out.append("config files differ (compare with --topic-mode overrides on one config)")
    oa, ob = ia.get("config_overrides", {}), ib.get("config_overrides", {})
    extra = {k for k in set(oa) | set(ob) if oa.get(k) != ob.get(k)} - {"text.topic_mode"}
    if extra:
        out.append(f"overrides other than text.topic_mode: {sorted(extra)}")
    return out


def _adjudicated(run: dict) -> dict:
    themes = run["q1"].get("themes_evaluable", 0)
    return A.summary(run["adjudication"], themes) if run["adjudication"] else {}


def _themes(run: dict) -> pd.DataFrame:
    """Per labelled theme: automatic match, lead, and the reviewer's decision."""
    m = run["matches"]
    if not len(m):
        return pd.DataFrame(columns=["theme", "auto_detected", "lead_days", "adjudicated"])
    dec = {e["theme"]: e["decision"] for e in run["adjudication"]
           if e.get("kind") in ("matched_theme", "negative_control_match")
           and not e.get("stale")}
    cols = [c for c in ("theme", "kind", "evaluable", "detected", "lead_days") if c in m]
    t = m[cols].rename(columns={"detected": "auto_detected"}).copy()
    t["adjudicated"] = t["theme"].map(dec).fillna("—")
    return t


def compare(run_a: Path, run_b: Path) -> dict:
    a, b = _load(run_a), _load(run_b)
    problems = comparability(a, b)

    def mode(r):
        return r["manifest"].get("text_tracking", {}).get("topic_mode", "window")

    rows = []

    def add(section, name, fa, fb):
        rows.append({"section": section, "criterion": name,
                     mode(a): fa(a), mode(b): fb(b) if fb else fa(b)})

    adj = {id(r): _adjudicated(r) for r in (a, b)}
    for key, label in [("matches_confirmed", "themes detected (confirmed)"),
                       ("confirmed_recall", "confirmed recall"),
                       ("negative_controls_confirmed_false_alarms",
                        "false alarms on negative controls (confirmed)"),
                       ("unmatched_noise", "unmatched candidates judged noise"),
                       ("unmatched_duplicate", "unmatched candidates judged duplicate"),
                       ("unmatched_real_unlabelled", "real but unlabelled themes"),
                       ("pending", "adjudication entries pending")]:
        add("1 adjudicated", label, lambda r, k=key: adj[id(r)].get(k), None)
    for key, label in [("detection_recall", "auto-matched recall (issuer overlap only)"),
                       ("median_lead_days", "auto-matched median lead days"),
                       ("negative_controls_flagged", "auto-matched negative controls"),
                       ("themes_evaluable", "themes evaluable (dated labels)")]:
        add("2 automatic", label, lambda r, k=key: r["q1"].get(k), None)
    for key, label in [("candidates", "candidates (first flags)"),
                       ("duplicate_pairs", "near-duplicate candidate pairs"),
                       ("candidates_with_duplicate", "candidates with a near-duplicate")]:
        add("3 candidates", label, lambda r, k=key: r["manifest"].get("diagnostics", {})
            .get(k), None)
    for key, label in [("unassigned_share_median", "unassigned passage share (median)"),
                       ("births_per_window", "topic births per window"),
                       ("births_lasting_per_window", "births lasting > window span"),
                       ("step_cosine_median", "month-to-month member cosine (median)"),
                       ("drift_cosine_median", "first-to-last member cosine (median)"),
                       ("tightness_median", "topic tightness (median)")]:
        add("4 topics", label, lambda r, k=key: r["manifest"].get("diagnostics", {}).get(k),
            None)
    for key, label in [("median_track_life_windows", "median topic life (windows)"),
                       ("share_tracks_persisting_beyond_span", "topics outliving a window"),
                       ("refit_continued_share", "refit continuity (anchored)"),
                       ("refit_splits", "refit splits (anchored)")]:
        add("4 topics", label, lambda r, k=key: r["manifest"].get("text_tracking", {}).get(k),
            None)
    for key in ("market_s", "text_s", "candidates_s", "diagnostics_s"):
        add("5 runtime", f"{key[:-2]} seconds",
            lambda r, k=key: r["manifest"].get("timings", {}).get(k), None)
    themes = _themes(a).merge(_themes(b), on=[c for c in ("theme", "kind", "evaluable")
                                                 if c in _themes(a)],
                              how="outer", suffixes=(f"_{mode(a)}", f"_{mode(b)}"))
    pending = any(adj[id(r)].get("pending", 1) for r in (a, b)) or not all(
        adj[id(r)] for r in (a, b))
    return {"comparable": not problems, "problems": problems,
            "adjudication_complete": not pending, "table": pd.DataFrame(rows),
            "themes": themes}


def render(result: dict) -> str:
    lines = []
    if result["problems"]:
        lines.append("NOT COMPARABLE:")
        lines += [f"  - {p}" for p in result["problems"]]
        lines.append("")
    if not result["adjudication_complete"]:
        lines.append("Adjudication incomplete in at least one run: section 1 is not yet a "
                     "result; do not choose a method from sections 2-5 alone.")
        lines.append("")
    lines.append(result["table"].to_string(index=False))
    if len(result["themes"]):
        lines += ["", "Per labelled theme:", result["themes"].to_string(index=False)]
    return "\n".join(lines)
