"""Freeze and holdout lifecycle (DESIGN.md §7.4).

- `freeze` hashes code, config, labels, primary metrics and the dependency
  environment (uv.lock plus Python minor version).
- A holdout run first appends an `attempt` record to the append-only
  holdout_log.jsonl and takes an exclusive lock, before any data is built or any
  outcome computed. An interrupted attempt therefore still counts as consumed.
- Only the first attempt ever made is clean. Any later attempt, under this or any
  other freeze, needs an explicit reason, is logged, and is marked not clean: a
  changed specification can be tested forward, not on the same history again.
- `verify` re-runs an existing completed attempt and checks every binding (freeze
  hashes, environment, input data and vintages, ledger, episode outputs, summary).
- Synthetic/demo runs never touch the log.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import uuid
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
    lock = root / "uv.lock"
    env = hashlib.sha256((lock.read_bytes() if lock.exists() else b"") +
                         ".".join(platform.python_version_tuple()[:2]).encode()).hexdigest()
    return {
        "environment": env,
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


class HoldoutError(RuntimeError):
    pass


def _log_path(root: Path) -> Path:
    return root / "holdout_log.jsonl"


def holdout_records(root: Path = ROOT) -> list[dict]:
    path = _log_path(root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(root: Path, record: dict) -> None:
    with open(_log_path(root), "a") as f:
        f.write(json.dumps({"logged_at": dt.datetime.now(dt.UTC).isoformat(), **record},
                           sort_keys=True, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def begin_holdout(inputs: dict, reason: str | None = None, root: Path = ROOT) -> dict:
    """Record a holdout attempt before anything is computed; returns the attempt."""
    record = check_freeze(root)
    attempts = [r for r in holdout_records(root) if r["event"] == "attempt"]
    if attempts and not reason:
        first = attempts[0]
        raise HoldoutError(
            f"the holdout was already consumed by attempt {first['attempt_id']} "
            f"({first['logged_at']}). A changed specification should be evaluated "
            "forward. To run again anyway, pass a reason; the attempt is logged and "
            "marked not clean.")
    lock = root / "holdout.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as e:
        raise HoldoutError(
            f"{lock} exists: another holdout attempt is running or one was interrupted. "
            "An interrupted attempt is already logged as consumed; inspect the log, "
            "then delete the lock to proceed.") from e
    inspected = [r for r in holdout_records(root) if r["event"] == "holdout_topic_inspection"]
    attempt = {"event": "attempt", "attempt_id": uuid.uuid4().hex,
               "freeze": record["hashes"], "inputs": inputs,
               "clean": not attempts and not inspected,
               "contaminated_by": [r["logged_at"] for r in inspected], "reason": reason}
    os.write(fd, json.dumps({"attempt_id": attempt["attempt_id"], "pid": os.getpid()}).encode())
    os.close(fd)
    _append(root, attempt)
    return attempt


def complete_holdout(attempt: dict, outputs: dict, root: Path = ROOT) -> None:
    _append(root, {"event": "completed", "attempt_id": attempt["attempt_id"],
                   "outputs": outputs})
    (root / "holdout.lock").unlink(missing_ok=True)


def completed_attempt(root: Path = ROOT) -> dict:
    """The completed attempt that verification must reproduce (the latest under the
    current freeze)."""
    record = check_freeze(root)
    recs = holdout_records(root)
    done = {r["attempt_id"]: r for r in recs if r["event"] == "completed"}
    matching = [r for r in recs if r["event"] == "attempt" and r["attempt_id"] in done
                and r["freeze"] == record["hashes"]]
    if not matching:
        raise HoldoutError("nothing to verify: no completed holdout attempt under the "
                           "current freeze. Verification never runs a first attempt.")
    a = matching[-1]
    return {**a, "outputs": done[a["attempt_id"]]["outputs"]}


def verify_holdout(inputs: dict, outputs: dict, root: Path = ROOT) -> dict:
    """Compare a re-run with the completed attempt; log and raise on any mismatch."""
    a = completed_attempt(root)
    diffs = [f"inputs.{k}" for k in sorted(set(a["inputs"]) | set(inputs))
             if a["inputs"].get(k) != inputs.get(k)]
    diffs += [f"outputs.{k}" for k in sorted(set(a["outputs"]) | set(outputs))
              if a["outputs"].get(k) != outputs.get(k)]
    _append(root, {"event": "verified" if not diffs else "verify_mismatch",
                   "attempt_id": a["attempt_id"], "differences": diffs})
    if diffs:
        raise HoldoutError(f"verification failed; differs in: {diffs}")
    return a
