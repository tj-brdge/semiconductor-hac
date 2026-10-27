"""Topic-tracking diagnostics computed identically for both topic modes.

Longer topic lives are partly built into anchored topics (identities are held
fixed), so life alone cannot show that a topic still means the same thing. These
measures use the topics' actual member passages in one shared representation: all
passages centred on per-genre means fitted once over the run (diagnostic only; never
used by discovery) and normalised. Centres of different refits or windows sit in
different coordinates and are not compared directly.

- unassigned_share: share of a window's passages in no topic; reported as the median
  over windows and pooled (all unassigned / all passages). The two differ because
  windows differ in size and medians are not additive.
- births_per_window / births_lasting: topics first seen in a window, and those of
  them still present `window_span` windows later (not one-off clusters).
- step_cosine: cosine between a topic's member centroids in consecutive windows it
  is present in (month-to-month consistency; 1 = unchanged meaning).
- drift_cosine: first vs last member centroid for topics living > 2 x window_span.
- tightness: mean cosine of members to their own centroid (how coherent a topic is).
- duplicate candidates: first flags within `dup_days` of each other whose flag-window
  member centroids have cosine >= `dup_cosine`; the same subject flagged twice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DUP_COSINE = 0.8
DUP_DAYS = 365


def shared_representation(p: pd.DataFrame, V: np.ndarray) -> np.ndarray:
    X = V.astype(np.float64, copy=True)
    genres = p["genre"].to_numpy() if "genre" in p else np.zeros(len(p))
    for g in np.unique(genres):
        idx = genres == g
        X[idx] -= X[idx].mean(axis=0)
    n = np.linalg.norm(X, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (X / n).astype(np.float32)


def _centroids(assign: pd.DataFrame, pos: pd.Series, X: np.ndarray) -> pd.DataFrame:
    """Unit member centroid and tightness per (track, t)."""
    a = assign.assign(i=assign["passage_id"].map(pos)).dropna(subset=["i"])
    a["i"] = a["i"].astype(int)
    rows = []
    for (tid, t), g in a.groupby(["track_id", "t"], sort=False):
        M = X[g["i"].to_numpy()]
        c = M.mean(axis=0)
        n = np.linalg.norm(c)
        if n == 0:
            continue
        c = c / n
        rows.append({"track_id": tid, "t": t, "n": len(M), "c": c,
                     "tightness": float((M @ c).mean())})
    return pd.DataFrame(rows)


def pooled_unassigned(windows: pd.DataFrame) -> float | None:
    n = windows["n_passages"].sum() if windows is not None and len(windows) else 0
    return round(float(1 - windows["n_clustered"].sum() / n), 4) if n else None


def topic_diagnostics(windows: pd.DataFrame, assign: pd.DataFrame, p: pd.DataFrame,
                      V: np.ndarray, window_span: int, ledger: pd.DataFrame | None,
                      dup_cosine: float = DUP_COSINE, dup_days: int = DUP_DAYS) -> dict:
    if assign is None or not len(assign) or p is None or not len(p):
        return {}
    X = shared_representation(p, V)
    pos = pd.Series(np.arange(len(p)), index=p["passage_id"].to_numpy())
    cents = _centroids(assign, pos, X).sort_values(["track_id", "t"])
    snaps = sorted(windows["t"])
    order = {t: i for i, t in enumerate(snaps)}

    w = windows.assign(unassigned=1 - windows["n_clustered"] / windows["n_passages"]
                       .where(windows["n_passages"] > 0))
    first = cents.groupby("track_id")["t"].min()
    last = cents.groupby("track_id")["t"].max()
    births = first.map(order).value_counts().reindex(range(len(snaps)), fill_value=0)
    lasting = (last.map(order) - first.map(order) >= window_span)
    births_lasting = first[lasting].map(order).value_counts().reindex(range(len(snaps)),
                                                                      fill_value=0)

    steps, drift = [], []
    for _, g in cents.groupby("track_id", sort=False):
        C = np.vstack(g["c"].to_numpy())
        if len(C) > 1:
            steps.extend((C[1:] * C[:-1]).sum(axis=1).tolist())
        if len(C) > 2 * window_span:
            drift.append(float(C[0] @ C[-1]))

    out = {
        "representation": "members centred on run-wide genre means (diagnostic only)",
        "unassigned_share_median": _r(w["unassigned"].median()),
        "unassigned_share_pooled": pooled_unassigned(windows),
        "unassigned_share_by_year": {int(y): _r(v) for y, v in
                                     w.groupby(w["t"].dt.year)["unassigned"].mean().items()},
        "births_per_window": _r(births.mean()),
        "births_lasting_per_window": _r(births_lasting.mean()),
        "step_cosine_median": _r(np.median(steps)) if steps else None,
        "drift_cosine_median": _r(np.median(drift)) if drift else None,
        "drift_topics": len(drift),
        "tightness_median": _r(cents["tightness"].median()),
    }
    out.update(duplicate_candidates(ledger, cents, dup_cosine, dup_days))
    return out


def duplicate_candidates(ledger: pd.DataFrame | None, cents: pd.DataFrame,
                         dup_cosine: float = DUP_COSINE, dup_days: int = DUP_DAYS) -> dict:
    if ledger is None or not len(ledger):
        return {"candidates": 0, "duplicate_pairs": 0, "candidates_with_duplicate": 0}
    firsts = ledger.loc[ledger["t"] == ledger["t_first_flag"],
                        ["candidate_id", "track_id", "t"]].drop_duplicates("candidate_id")
    c = firsts.merge(cents[["track_id", "t", "c"]], on=["track_id", "t"], how="left")
    c = c.dropna(subset=["c"]).reset_index(drop=True)
    pairs = []
    for i in range(len(c)):
        for j in range(i + 1, len(c)):
            if abs((c.at[i, "t"] - c.at[j, "t"]).days) > dup_days:
                continue
            s = float(c.at[i, "c"] @ c.at[j, "c"])
            if s >= dup_cosine:
                pairs.append((c.at[i, "candidate_id"], c.at[j, "candidate_id"], round(s, 3)))
    involved = {x for a, b, _ in pairs for x in (a, b)}
    return {"candidates": int(len(firsts)), "duplicate_pairs": len(pairs),
            "candidates_with_duplicate": len(involved),
            "duplicate_examples": pairs[:20]}


def _r(x) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 3)


def coverage_samples(windows: pd.DataFrame, assign: pd.DataFrame, p: pd.DataFrame,
                     window_days: int, per_year: int = 8, seed: int = 0,
                     chars: int = 300) -> str:
    """Text-only reading sample for judging coverage: for the June window of each year,
    `per_year` random passages assigned to a topic and `per_year` left unassigned.
    Shows no candidates, outcomes or market data."""
    if assign is None or p is None or not len(p) or windows is None or not len(windows):
        return ""
    rng = np.random.default_rng(seed)
    text = p.set_index("passage_id")
    lines = ["# Coverage samples", "",
             "Judge whether assigned passages are about business subjects and whether "
             "unassigned ones are mostly boilerplate. No outcomes are shown.", ""]
    for _, w in windows.groupby(windows["t"].dt.year):
        june = w.loc[w["t"].dt.month == 6]
        t = (june if len(june) else w)["t"].iloc[0]
        got = assign.loc[assign["t"] == t]
        lab = dict(zip(got["passage_id"], got["track_id"], strict=True))
        lo = t - pd.Timedelta(days=window_days)
        ids = text.index[(text["available_at"] > lo) & (text["available_at"] <= t)]
        assigned = [i for i in ids if i in lab]
        unassigned = [i for i in ids if i not in lab]
        lines += [f"## {t.date()}  ({len(assigned)} assigned, {len(unassigned)} unassigned)", ""]
        for name, pool in (("Assigned", assigned), ("Unassigned", unassigned)):
            lines.append(f"### {name}")
            for i in rng.choice(pool, min(per_year, len(pool)), replace=False) if pool else []:
                r = text.loc[i]
                topic = f" · topic {lab[i]}" if i in lab else ""
                body = " ".join(str(r["text"]).split())[:chars]
                lines.append(f"- [{r['symbol']} · {r.get('genre', '')}{topic}] {body}")
            lines.append("")
    return "\n".join(lines)



def question_share(run_dir) -> dict:
    """How much of a run's Q&A text is analysts' questions rather than management's
    answers, from saved run files only. A Q&A passage counts as a question when its
    speaker gives no prepared remarks in the same call (analysts never do; an
    executive who only speaks in Q&A is miscounted as a questioner, so this is an
    upper-bound estimate). Reported for all passages, passages in topics, candidates'
    members at their first flag, and the quotes shown for adjudication."""
    import json
    from pathlib import Path

    run_dir = Path(run_dir)
    p = pd.read_parquet(run_dir / "passages.parquet", columns=["passage_id", "doc_id",
                                                                "genre", "unit"])
    tr = p["genre"].astype(str).str.startswith("transcript:")
    spk = p["unit"].astype(str).str.rsplit("|", n=1).str[0]
    prepared = set(zip(p.loc[p["genre"] == "transcript:prepared", "doc_id"],
                       spk[p["genre"] == "transcript:prepared"], strict=True))
    qa = p["genre"] == "transcript:qa"
    in_prepared = pd.Series([(d, s) in prepared for d, s in zip(p["doc_id"], spk, strict=True)],
                            index=p.index)
    is_q = qa & ~in_prepared
    q_ids = set(p.loc[is_q, "passage_id"])

    def share(ids) -> dict:
        ids = list(ids)
        n = len(ids)
        k = sum(i in q_ids for i in ids)
        return {"passages": n, "questions": k, "share": round(k / n, 3) if n else None}

    out = {"method": "Q&A speaker with no prepared remarks in the same call (upper bound)",
           "all_passages": share(p["passage_id"]),
           "transcript_passages": share(p.loc[tr, "passage_id"]),
           "qa_passages": share(p.loc[qa, "passage_id"])}
    a = pd.read_parquet(run_dir / "assignments.parquet", columns=["t", "track_id",
                                                                    "passage_id"])
    out["topic_members"] = share(a["passage_id"])
    led = pd.read_parquet(run_dir / "candidate_ledger.parquet")
    firsts = led.loc[led["t"] == led["t_first_flag"], ["candidate_id", "track_id", "t"]] \
        .drop_duplicates("candidate_id")
    m = firsts.merge(a, on=["track_id", "t"])
    out["candidate_members"] = share(m["passage_id"])
    per = m.assign(q=m["passage_id"].isin(q_ids)).groupby("candidate_id")["q"].mean()
    out["candidates_mostly_questions"] = {"candidates": int(len(per)),
                                          "over_half_questions": int((per > 0.5).sum())}
    ex = run_dir / "explanations.json"
    quotes = [q["passage_id"] for e in json.loads(ex.read_text()) for q in e.get("quotes", [])] \
        if ex.exists() else []
    out["adjudication_quotes"] = share(quotes)
    return out
