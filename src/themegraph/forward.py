"""Forward (as-of) prediction log: the only clean evidence (DESIGN.md §7.1).

`predict` runs the frozen-in-spirit pipeline at a cutoff using only data that was
both published and actually downloaded by then (`first_seen_at` <= cutoff, the
"observed" basis), and appends one immutable record per candidate active at the
cutoff to forward/predictions.jsonl. Re-running the same cutoff with the same
specification and inputs adds nothing (idempotent); with different inputs it is
refused, because a recorded prediction must never be replaced.

`evaluate_outcomes` later appends outcome records to forward/outcomes.jsonl for
horizons that have fully elapsed. It reads predictions and never writes to them.

This is a research log, not a trading system.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pandas as pd

from . import pipeline as P
from .config import PipelineConfig, UniverseConfig
from .data.pit import snapshot_ts
from .eval.returns import holding_return
from .text.embed import Embedder

PREDICTIONS = "predictions.jsonl"
OUTCOMES = "outcomes.jsonl"


class ForwardLogError(RuntimeError):
    pass


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _append(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True, default=str) + "\n")


def observed(df: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[pd.DataFrame, str]:
    """Rows usable at the cutoff. With `first_seen_at`, a row must also have been
    downloaded by then (observed basis); without it, availability is reconstructed
    from publication times and the record says so."""
    if not len(df):
        return df, "observed"
    keep = df["available_at"] <= cutoff
    if "first_seen_at" in df:
        keep &= pd.to_datetime(df["first_seen_at"], utc=True) <= cutoff
        return df.loc[keep], "observed"
    return df.loc[keep], "reconstructed"


def coverage(prices: pd.DataFrame, docs: pd.DataFrame, universe: UniverseConfig,
             cutoff: pd.Timestamp) -> dict:
    """Freshness at the cutoff: days since each issuer's last price and document."""
    out = {}
    for iss in universe.issuers:
        p = prices.loc[prices["symbol"] == iss.symbol, "available_at"] if len(prices) else []
        d = docs.loc[docs["symbol"] == iss.symbol, "available_at"] if len(docs) else []
        out[iss.symbol] = {
            "price_age_days": (cutoff - max(p)).days if len(p) else None,
            "doc_age_days": (cutoff - max(d)).days if len(d) else None,
        }
    ages = [v["doc_age_days"] for v in out.values() if v["doc_age_days"] is not None]
    return {"issuers": out,
            "issuers_with_price": sum(v["price_age_days"] is not None for v in out.values()),
            "issuers_with_text": len(ages),
            "median_doc_age_days": float(pd.Series(ages).median()) if ages else None}


def spec_hashes(root: Path | None = None) -> dict:
    """Code, config and environment hashes (labels are not used by discovery)."""
    from .eval.freeze import current_hashes
    h = current_hashes(root) if root else current_hashes()
    return {k: v for k, v in h.items() if k != "labels"}


def predict(prices: pd.DataFrame, docs: pd.DataFrame, cfg: PipelineConfig,
            universe: UniverseConfig, embedder: Embedder, cache_dir: Path, out_dir: Path,
            cutoff: dt.date, spec: dict | None = None, log=print) -> list[dict]:
    cut = snapshot_ts(cutoff)
    prices_o, basis_p = observed(prices, cut)
    docs_o, basis_d = observed(docs, cut)
    spec = spec if spec is not None else spec_hashes()
    inputs = {"prices": P.frame_sha256(prices_o.drop(columns=["first_seen_at", "vintage"],
                                                     errors="ignore")),
              "documents": P.frame_sha256(docs_o[["doc_id", "text"]]) if len(docs_o) else None}
    log_path = Path(out_dir) / PREDICTIONS
    previous = [r for r in _read(log_path) if r["cutoff"] == str(cutoff)
                and r["spec_sha256"] == _sha(spec)]
    if previous and any(r["inputs"] != inputs for r in previous):
        raise ForwardLogError(
            f"predictions for cutoff {cutoff} were already recorded from different inputs; "
            "recorded predictions are immutable. Use a new cutoff.")
    if previous:
        log(f"cutoff {cutoff} already recorded ({len(previous)} records); nothing appended")
        return previous
    ctx = P.build(prices_o, docs_o, cfg, universe, embedder, cache_dir, "forward",
                  log=log, end=cutoff)
    led = ctx.ledger
    active = led.loc[led["t"] == ctx.snapshots[-1]] if led is not None and len(led) else led
    expl = {} if ctx.explanations is None or not len(ctx.explanations) else \
        ctx.explanations.set_index("candidate_id").to_dict(orient="index")
    recorded_at = dt.datetime.now(dt.UTC).isoformat()
    cov = coverage(prices_o, docs_o, universe, cut)
    records = []
    for r in (active.itertuples() if active is not None and len(active) else []):
        body = {"cutoff": str(cutoff), "snapshot": str(r.t), "spec_sha256": _sha(spec),
                "issuers": sorted(r.issuers), "track_id": int(r.track_id),
                "first_flag": str(r.t_first_flag)}
        e = expl.get(r.candidate_id, {})
        records.append({
            "prediction_id": _sha(body)[:16], **body, "spec": spec, "inputs": inputs,
            "availability_basis": {"prices": basis_p, "documents": basis_d},
            "new_flag": r.t == r.t_first_flag, "market_affinity_null_pct":
                r.market_affinity_null_pct,
            "as_of_keywords": list(e.get("keywords", [])),
            "quote_passage_ids": [q["passage_id"] for q in e.get("quotes", [])],
            "coverage": {k: v for k, v in cov.items() if k != "issuers"},
            "recorded_at": recorded_at,
        })
    if not records:  # record the empty result too: "no candidates" is a prediction
        body = {"cutoff": str(cutoff), "snapshot": str(ctx.snapshots[-1]),
                "spec_sha256": _sha(spec), "issuers": [], "track_id": None,
                "first_flag": None}
        records.append({"prediction_id": _sha(body)[:16], **body, "spec": spec,
                        "inputs": inputs, "no_candidates": True,
                        "availability_basis": {"prices": basis_p, "documents": basis_d},
                        "coverage": {k: v for k, v in cov.items() if k != "issuers"},
                        "recorded_at": recorded_at})
    _append(log_path, records)
    (Path(out_dir) / f"coverage_{cutoff}.json").write_text(json.dumps(cov, indent=1,
                                                                      default=str))
    return records


def evaluate_outcomes(out_dir: Path, closes: pd.DataFrame, benchmark: str,
                      horizons: list[int], data_end: pd.Timestamp,
                      delisted: dict | None = None) -> list[dict]:
    """Append outcomes for (prediction, horizon) pairs whose horizon has elapsed and
    that have no outcome yet. Predictions are read, never modified."""
    out_dir = Path(out_dir)
    done = {(o["prediction_id"], o["h"]) for o in _read(out_dir / OUTCOMES)}
    new = []
    for p in _read(out_dir / PREDICTIONS):
        if p.get("no_candidates"):
            continue
        t = pd.Timestamp(p["snapshot"])
        for h in horizons:
            if (p["prediction_id"], h) in done:
                continue
            b = holding_return(closes, p["issuers"], t, h, data_end, delisted)
            if b["status"] == "incomplete_horizon":
                continue  # not yet knowable; evaluated on a later call
            bench = holding_return(closes, [benchmark], t, h, data_end)
            new.append({"prediction_id": p["prediction_id"], "h": h, "status": b["status"],
                        "entry": str(b["entry"]), "exit": str(b["exit"]),
                        "basket": b["value"], "benchmark": bench["value"],
                        "excess_vs_benchmark": b["value"] - bench["value"],
                        "constituents": b["statuses"],
                        "evaluated_at": dt.datetime.now(dt.UTC).isoformat()})
    _append(out_dir / OUTCOMES, new)
    return new
