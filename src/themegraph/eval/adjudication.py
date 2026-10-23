"""Human adjudication of candidate-to-theme matches (DESIGN.md §7.3).

Automatic matching uses issuer overlap and timing only, so a match can pair a theme
with an unrelated topic that happens to involve the same companies. Each run can
produce a worksheet, `adjudication.yaml`, for a reviewer to record whether:

- a matched candidate really is the labelled theme (`confirmed` / `rejected`);
- a candidate matching a negative control is the hyped topic (`confirmed` /
  `rejected`); a confirmed one is a real false alarm;
- an unmatched candidate is a real but unlabelled theme, noise, or a duplicate
  (`real_unlabelled_theme` / `noise` / `duplicate`). These entries are blind: they
  show the as-of keywords and quotation references only, no labels or theme names.

Decisions are read only by evaluation reporting; discovery never sees them.
Regenerating the worksheet keeps existing decisions. Nothing is filled in
automatically; undecided entries stay `pending`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

DECISIONS = {
    "matched_theme": {"confirmed", "rejected"},
    "negative_control_match": {"confirmed", "rejected"},
    "unmatched_candidate": {"real_unlabelled_theme", "noise", "duplicate"},
}
WORKSHEET = "adjudication.yaml"


def _explanations(run_dir: Path) -> dict:
    path = run_dir / "explanations.json"
    if not path.exists():
        return {}
    return {e["candidate_id"]: e for e in json.loads(path.read_text())}


def build_entries(run_dir: Path) -> list[dict]:
    run_dir = Path(run_dir)
    ledger = pd.read_parquet(run_dir / "candidate_ledger.parquet")
    mpath = run_dir / "eval_matches.parquet"
    matches = pd.read_parquet(mpath) if mpath.exists() else pd.DataFrame()
    expl = _explanations(run_dir)
    firsts = ledger.loc[ledger["t"] == ledger["t_first_flag"]] if len(ledger) else ledger
    entries, matched = [], set()

    def evidence(cid):
        e = expl.get(cid, {})
        return {"as_of_keywords": list(e.get("keywords", [])),
                "quote_passage_ids": [q["passage_id"] for q in e.get("quotes", [])]}

    if len(matches):
        for r in matches.itertuples():
            if not getattr(r, "detected", False) or not r.candidate_id:
                continue
            matched.add(r.candidate_id)
            kind = "matched_theme" if r.kind == "theme" else "negative_control_match"
            entries.append({"id": f"{kind}:{r.theme}:{r.candidate_id}", "kind": kind,
                            "theme": r.theme, "candidate_id": r.candidate_id,
                            **evidence(r.candidate_id), "decision": "pending",
                            "reviewer": None, "reviewed_on": None, "notes": ""})
    for r in firsts.itertuples():
        if r.candidate_id in matched:
            continue
        entries.append({"id": f"unmatched_candidate:{r.candidate_id}",
                        "kind": "unmatched_candidate", "candidate_id": r.candidate_id,
                        **evidence(r.candidate_id), "decision": "pending",
                        "reviewer": None, "reviewed_on": None, "notes": ""})
    return entries


def load(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    entries = yaml.safe_load(path.read_text()) or []
    for e in entries:
        allowed = DECISIONS[e["kind"]] | {"pending"}
        if e.get("decision") not in allowed:
            raise ValueError(f"{e['id']}: decision {e.get('decision')!r} not in "
                             f"{sorted(allowed)}")
        if e["decision"] != "pending" and not e.get("reviewer"):
            raise ValueError(f"{e['id']}: a decision needs a reviewer")
    return entries


def write_worksheet(run_dir: Path) -> Path:
    """Create or refresh the worksheet, keeping every decision already recorded."""
    run_dir = Path(run_dir)
    path = run_dir / WORKSHEET
    old = {e["id"]: e for e in load(path)}
    merged = []
    for e in build_entries(run_dir):
        prev = old.pop(e["id"], None)
        if prev:
            e.update({k: prev[k] for k in ("decision", "reviewer", "reviewed_on", "notes")})
        merged.append(e)
    merged += [{**e, "stale": True} for e in old.values()]  # kept, never silently dropped
    path.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True))
    return path


def summary(entries: list[dict], themes_evaluable: int) -> dict:
    """Adjudicated counts. Confirmed recall uses only reviewer-confirmed matches."""
    by = {}
    for e in entries:
        if e.get("stale"):
            continue
        by.setdefault(e["kind"], []).append(e["decision"])
    count = lambda kind, d: sum(x == d for x in by.get(kind, []))  # noqa: E731
    return {
        "pending": sum(x == "pending" for v in by.values() for x in v),
        "matches_confirmed": count("matched_theme", "confirmed"),
        "matches_rejected": count("matched_theme", "rejected"),
        "confirmed_recall": (count("matched_theme", "confirmed") / themes_evaluable
                             if themes_evaluable else None),
        "negative_controls_confirmed_false_alarms": count("negative_control_match",
                                                          "confirmed"),
        "unmatched_real_unlabelled": count("unmatched_candidate", "real_unlabelled_theme"),
        "unmatched_noise": count("unmatched_candidate", "noise"),
        "unmatched_duplicate": count("unmatched_candidate", "duplicate"),
    }
