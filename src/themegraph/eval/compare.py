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
from .diagnostics import pooled_unassigned


def _load(run: Path) -> dict:
    run = Path(run)
    m = json.loads((run / "manifest.json").read_text())
    res = json.loads((run / "results.json").read_text()) if (run / "results.json").exists() \
        else {}
    entries = A.load(run / A.WORKSHEET)
    mp = run / "eval_matches.parquet"
    wp = run / "windows.parquet"
    return {"dir": run, "manifest": m, "q1": res.get("q1", {}), "adjudication": entries,
            "matches": pd.read_parquet(mp) if mp.exists() else pd.DataFrame(),
            "windows": pd.read_parquet(wp) if wp.exists() else None}


def _pooled_unassigned(r: dict):
    """From the manifest, else from the run's saved windows (runs made before the
    pooled figure was recorded)."""
    v = r["manifest"].get("diagnostics", {}).get("unassigned_share_pooled")
    return v if v is not None else pooled_unassigned(r["windows"])


REQUIRED = ("source_sha256", "labels_sha256", "lockfile_sha256", "python", "packages",
            "effective_config", "effective_universe")


def _universe_keyed(u: dict) -> dict:
    """Issuers keyed by symbol so a difference names the issuer and field."""
    return {"benchmarks": u.get("benchmarks"),
            "issuers": {i["symbol"]: i for i in u.get("issuers", [])}}


def _without_mode(cfg: dict) -> dict:
    c = json.loads(json.dumps(cfg))
    c.get("text", {}).pop("topic_mode", None)
    return c


def _diff(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b)) for d in _diff(a.get(k), b.get(k),
                                                                     f"{path}.{k}" if path else k)]
    return [] if a == b else [path]


def comparability(a: dict, b: dict) -> list[str]:
    """Reasons the runs cannot be compared for method selection (empty = comparable).
    Missing provenance is a reason, not a pass: rerun with the current code."""
    ma, mb = a["manifest"], b["manifest"]
    out = []
    for name, m in (("A", ma), ("B", mb)):
        if m.get("period") != "dev":
            out.append(f"run {name} is period {m.get('period')!r}: method selection uses "
                       "development runs only")
        prov = m.get("provenance")
        if not prov:
            out.append(f"run {name} has no provenance (made before it was recorded): rerun it "
                       "with the current code")
            continue
        missing = [k for k in REQUIRED if prov.get(k) in (None, {}, "")]
        if missing:
            out.append(f"run {name} provenance lacks {missing}: rerun with the current code")
        if prov.get("git_dirty"):
            out.append(f"run {name} was made with uncommitted changes to src/config/labels "
                       "(commit them and rerun so the source is identifiable)")
    pa, pb = ma.get("provenance") or {}, mb.get("provenance") or {}
    if pa and pb:
        for k, label in (("source_sha256", "source code"), ("labels_sha256", "labels"),
                         ("lockfile_sha256", "dependency lockfile"), ("python", "Python"),
                         ("packages", "package versions")):
            if pa.get(k) != pb.get(k):
                out.append(f"{label} differ ({k}): run both from one checkout and environment")
        if pa.get("effective_config") and pb.get("effective_config"):
            d = _diff(_without_mode(pa["effective_config"]),
                      _without_mode(pb["effective_config"]))
            if d:
                out.append(f"effective configuration differs beyond text.topic_mode: {d[:8]}")
        if pa.get("effective_universe") and pb.get("effective_universe"):
            d = _diff(_universe_keyed(pa["effective_universe"]),
                      _universe_keyed(pb["effective_universe"]))
            if d:
                out.append(f"effective universe differs: {d[:8]}")
    ia, ib = ma.get("inputs", {}), mb.get("inputs", {})
    for k in ("scheme", "prices", "documents", "vintages", "input_vintage_upto"):
        if k not in ia or k not in ib:
            out.append(f"inputs.{k} missing from a manifest: rerun with the current code")
        elif ia.get(k) != ib.get(k):
            out.append(f"inputs.{k} differ")
    for k in ("end", "embedder", "snapshots"):
        if ma.get(k) != mb.get(k):
            out.append(f"{k} differs: {ma.get(k)} vs {mb.get(k)}")
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

    names = {}
    for tag, r in (("A", a), ("B", b)):
        names[id(r)] = f"{tag}: {r['manifest'].get('text_tracking', {}).get('topic_mode', '?')}"

    def mode(r):
        return names[id(r)]

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
    add("4 topics", "unassigned passage share (pooled)", _pooled_unassigned, None)
    for key, label in [("unassigned_share_median",
                        "unassigned passage share (median of windows)"),
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
