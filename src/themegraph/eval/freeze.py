"""Freeze mechanics (DESIGN.md §7.4): hash code, config, labels and primary metrics;
refuse holdout evaluation without a valid freeze; allow one holdout run."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from ..config import ROOT, load_pipeline, load_universe
from .labels import load_labels

FREEZE_PATH = ROOT / "freeze.json"
HOLDOUT_LOG = ROOT / "holdout_log.jsonl"


def _tree_hash(paths: list[Path], root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def current_hashes(root: Path = ROOT) -> dict:
    src = [p for p in (root / "src").rglob("*.py")]
    cfg = load_pipeline(root / "config" / "pipeline.yaml")
    return {
        "code": _tree_hash(src, root),
        "config": _tree_hash(list((root / "config").glob("*.yaml")), root),
        "labels": _tree_hash([root / "labels" / "themes.yaml"], root),
        "primary_metrics": hashlib.sha256(
            json.dumps(cfg.evaluation.primary_metrics).encode()).hexdigest(),
    }


def freeze(root: Path = ROOT) -> dict:
    labels = load_labels(root / "labels" / "themes.yaml")
    symbols = {i.symbol for i in load_universe(root / "config" / "universe.yaml").issuers}
    problems = labels.problems(symbols)
    if not labels.frozen:
        problems.insert(0, "labels/themes.yaml must set `frozen: true`")
    if problems:
        raise RuntimeError("cannot freeze:\n  " + "\n  ".join(problems))
    record = {"frozen_at": dt.datetime.now(dt.UTC).isoformat(), "hashes": current_hashes(root)}
    (root / "freeze.json").write_text(json.dumps(record, indent=1))
    return record


def check_freeze(root: Path = ROOT) -> dict:
    path = root / "freeze.json"
    if not path.exists():
        raise RuntimeError("no freeze.json: run `themegraph freeze` before holdout evaluation")
    record = json.loads(path.read_text())
    now = current_hashes(root)
    changed = [k for k, v in record["hashes"].items() if now.get(k) != v]
    if changed:
        raise RuntimeError(f"frozen inputs changed since freeze: {changed}")
    return record


def guard_holdout(results_sha256: str | None = None, root: Path = ROOT) -> dict:
    """Validate the freeze and enforce a single holdout run. A rerun is permitted only
    to verify an identical result (same freeze and same results hash)."""
    record = check_freeze(root)
    log = root / "holdout_log.jsonl"
    key = json.dumps(record["hashes"], sort_keys=True)
    prior = []
    if log.exists():
        prior = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    same = [p for p in prior if json.dumps(p["hashes"], sort_keys=True) == key]
    if same and results_sha256 is not None and same[0].get("results_sha256") != results_sha256:
        raise RuntimeError("holdout already run under this freeze with a different result")
    return {"record": record, "already_run": bool(same)}


def log_holdout(results_sha256: str, root: Path = ROOT) -> None:
    record = check_freeze(root)
    with open(root / "holdout_log.jsonl", "a") as f:
        f.write(json.dumps({"ran_at": dt.datetime.now(dt.UTC).isoformat(),
                            "hashes": record["hashes"], "results_sha256": results_sha256})
                + "\n")
