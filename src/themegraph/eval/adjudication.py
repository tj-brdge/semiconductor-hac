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

Decisions can also be imported from the Theme Adjudication Desk (a review page that
shows Claude's suggestion beside each entry). Each imported decision records who
made it (`decided_by`: human or ai), Claude's suggestion and its reason, the model
tier and prompt version, and, for the blind sample, the person's decision made
before seeing Claude's. AI decisions are reported separately and are never
counted as human review; blind agreement is reported beside them.
"""

from __future__ import annotations

import hashlib
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
IMPORT_FORMAT = "themegraph-adjudication/1"
PROVENANCE = ("decided_by", "ai_suggestion", "ai_reason", "ai_model", "ai_prompt_version",
              "blind_human_decision")
KEEP = ("decision", "reviewer", "reviewed_on", "notes") + PROVENANCE


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
        if e.get("decided_by") not in (None, "human", "ai"):
            raise ValueError(f"{e['id']}: decided_by must be human or ai")
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
            e.update({k: prev[k] for k in KEEP if k in prev})
        merged.append(e)
    merged += [{**e, "stale": True} for e in old.values()]  # kept, never silently dropped
    path.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True))
    write_packet(run_dir, merged)
    return path


def import_decisions(run_dir: Path, path: Path, replace: bool = False) -> dict:
    """Merge decisions exported by the review page into the run's worksheet.
    Refuses unknown or stale ids, decisions not allowed for the entry's kind, and
    (unless `replace`) any change to a decision already recorded."""
    run_dir = Path(run_dir)
    payload = json.loads(Path(path).read_text())
    if payload.get("format") != IMPORT_FORMAT:
        raise ValueError(f"not a {IMPORT_FORMAT} file")
    ws = run_dir / WORKSHEET
    entries = load(ws)
    if not entries:
        raise ValueError("no worksheet in this run: run `themegraph adjudicate RUN` first")
    by_id = {e["id"]: e for e in entries if not e.get("stale")}
    warnings, problems, applied = [], [], 0
    packet = run_dir / PACKET
    if payload.get("packet_sha256") and packet.exists() and \
            hashlib.sha256(packet.read_bytes()).hexdigest() != payload["packet_sha256"]:
        warnings.append("the packet changed since these decisions were made (it is rewritten "
                        "on every refresh); entries are matched by id")
    for d in payload.get("entries", []):
        e = by_id.get(d.get("id"))
        if e is None:
            problems.append(f"{d.get('id')}: not in this run's worksheet (wrong run?)")
            continue
        if d.get("decision") not in DECISIONS[e["kind"]]:
            problems.append(f"{e['id']}: decision {d.get('decision')!r} not allowed")
            continue
        if not d.get("reviewer") or d.get("decided_by") not in ("human", "ai"):
            problems.append(f"{e['id']}: needs a reviewer and decided_by human/ai")
            continue
        if e["decision"] != "pending" and not replace and \
                (e["decision"], e.get("reviewer")) != (d["decision"], d["reviewer"]):
            problems.append(f"{e['id']}: already decided {e['decision']!r} by "
                            f"{e.get('reviewer')}; use --replace to overwrite")
            continue
        e.update({"decision": d["decision"], "reviewer": d["reviewer"],
                  "reviewed_on": d.get("reviewed_on"), "notes": d.get("notes") or ""})
        e.update({k: d.get(k) for k in PROVENANCE})
        applied += 1
    if problems:
        raise ValueError("nothing imported:\n  " + "\n  ".join(problems))
    ws.write_text(yaml.safe_dump(entries, sort_keys=False, allow_unicode=True))
    write_packet(run_dir, entries)
    return {"applied": applied, "warnings": warnings,
            "blind_sample": payload.get("blind_sample")}


PACKET = "adjudication_packet.md"


def write_packet(run_dir: Path, entries: list[dict]) -> Path:
    """Reading copy for reviewers: each entry's as-of keywords and quoted passages,
    flag date and issuers. No returns, market alignment or co-movement: semantic
    judgements are made without outcomes (report.html shows them; don't use it
    while adjudicating). Unmatched entries also show no theme name."""
    run_dir = Path(run_dir)
    expl = _explanations(run_dir)
    led = pd.read_parquet(run_dir / "candidate_ledger.parquet")
    firsts = led.loc[led["t"] == led["t_first_flag"]].drop_duplicates("candidate_id") \
        .set_index("candidate_id") if len(led) else pd.DataFrame()
    lines = ["# Adjudication packet", "",
             "Record decisions in adjudication.yaml. Judge from the text only.", ""]
    for e in entries:
        if e.get("stale"):
            continue
        cid = e["candidate_id"]
        head = f"## {e['id']}" if e["kind"] != "unmatched_candidate" else f"## {cid}"
        lines.append(head)
        if cid in firsts.index:
            r = firsts.loc[cid]
            lines.append(f"First flag {pd.Timestamp(r['t']).date()} · issuers "
                         f"{', '.join(r['issuers'])}")
        x = expl.get(cid, {})
        lines.append(f"Keywords: {', '.join(x.get('keywords', []))}")
        for q in x.get("quotes", []):
            lines.append(f"> [{q.get('symbol')} · {str(q.get('available_at'))[:10]} · "
                         f"{q.get('passage_id')}] {q.get('text', '')}")
        lines.append(f"Decision: {e['decision']}")
        lines.append("")
    out = run_dir / PACKET
    out.write_text("\n".join(lines))
    return out


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
        **review_sources(entries),
    }


def review_sources(entries: list[dict]) -> dict:
    """Who decided, and how often a person and Claude agreed where the person
    decided first without seeing Claude's suggestion (the blind sample)."""
    live = [e for e in entries if not e.get("stale") and e.get("decision") != "pending"]
    pairs = [e for e in entries if not e.get("stale") and e.get("blind_human_decision")
             and e.get("ai_suggestion")]
    agree = sum(e["blind_human_decision"] == e["ai_suggestion"] for e in pairs)
    return {"decided_by_ai": sum(e.get("decided_by") == "ai" for e in live),
            "decided_by_human": sum(e.get("decided_by") != "ai" for e in live),
            "blind_pairs": len(pairs),
            "blind_agreement": round(agree / len(pairs), 3) if pairs else None}
