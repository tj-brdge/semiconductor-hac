"""Q1 metrics: recall, lead time, false alarms (DESIGN.md §7.3)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .labels import Theme


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def match_candidates(ledger: pd.DataFrame, themes: list[Theme], min_jaccard: float,
                     window_days: int) -> pd.DataFrame:
    """For each labelled theme with a consensus date: the earliest ledger flag whose
    issuer set overlaps (Jaccard) and falls within `window_days` before consensus."""
    rows = []
    for th in themes:
        c = th.consensus_marker.date
        if c is None:
            rows.append({"theme": th.id, "kind": th.kind, "evaluable": False})
            continue
        C = pd.Timestamp(c, tz="UTC")
        lo = C - pd.Timedelta(days=window_days)
        hit = None
        if not ledger.empty:
            win = ledger.loc[(ledger["t"] >= lo) & (ledger["t"] <= C)].copy()
            win["jaccard"] = [jaccard(i, th.issuers) for i in win["issuers"]]
            win = win.loc[win["jaccard"] >= min_jaccard].sort_values("t")
            hit = win.iloc[0] if len(win) else None
        rows.append({
            "theme": th.id, "kind": th.kind, "evaluable": True,
            "consensus": C, "detected": hit is not None,
            "first_flag": hit["t"] if hit is not None else pd.NaT,
            "candidate_id": hit["candidate_id"] if hit is not None else None,
            "jaccard": hit["jaccard"] if hit is not None else np.nan,
            "lead_days": (C - hit["t"]).days if hit is not None else np.nan,
        })
    return pd.DataFrame(rows)


def detection_metrics(matches: pd.DataFrame, ledger: pd.DataFrame, years: float) -> dict:
    ev = matches.loc[matches.get("evaluable", False) == True]  # noqa: E712
    themes = ev.loc[ev["kind"] == "theme"]
    neg = ev.loc[ev["kind"] == "negative_control"]
    matched_ids = set(ev.loc[ev["detected"] == True, "candidate_id"])  # noqa: E712
    unmatched = 0
    if not ledger.empty:
        firsts = ledger.drop_duplicates("candidate_id")
        unmatched = int((~firsts["candidate_id"].isin(matched_ids)).sum())
    return {
        "themes_evaluable": int(len(themes)),
        "themes_not_evaluable": int((matches.get("evaluable") == False).sum()),  # noqa: E712
        "detection_recall": float(themes["detected"].mean()) if len(themes) else float("nan"),
        "median_lead_days": float(themes["lead_days"].median()) if len(themes) else float("nan"),
        "negative_controls_flagged": int(neg["detected"].sum()) if len(neg) else 0,
        "unmatched_candidates": unmatched,
        "unmatched_candidates_per_year": unmatched / years if years else float("nan"),
    }
