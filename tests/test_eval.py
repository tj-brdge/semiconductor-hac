import json
import shutil

import numpy as np
import pandas as pd
import pytest
import yaml

from themegraph.config import ROOT
from themegraph.eval import freeze as F
from themegraph.eval.comovement import matched_null_sets
from themegraph.eval.detection import detection_metrics, match_candidates
from themegraph.eval.labels import LabelFile, Marker, Theme, load_labels


def _theme(id_, issuers, consensus, kind="theme"):
    return Theme(id=id_, period="dev", kind=kind, issuers=issuers,
                 emergence_marker=Marker(date="2019-01-01", source="x"),
                 consensus_marker=Marker(date=consensus, source="x"))


def test_matching_respects_window_and_overlap():
    led = pd.DataFrame({
        "candidate_id": ["c1", "c2", "c3"],
        "t": pd.to_datetime(["2017-01-31", "2019-03-31", "2019-06-30"], utc=True),
        "issuers": [["A", "B", "C"], ["A", "B", "X"], ["A", "B", "C"]],
    })
    m = match_candidates(led, [_theme("t", ["A", "B", "C"], "2019-12-31")], 0.34, 540)
    assert m["candidate_id"].iloc[0] == "c2" and m["lead_days"].iloc[0] == 275
    metrics = detection_metrics(m, led, 5)
    assert metrics["detection_recall"] == 1.0 and metrics["unmatched_candidates"] == 2


def test_null_sets_exclude_members_and_match_segments():
    segs = {f"{g}{i}": g for g in "AB" for i in range(6)}
    sets = matched_null_sets(["A0", "A1", "B0"], list(segs), segs,
                             np.random.default_rng(0), 50)
    for s in sets:
        assert not {"A0", "A1", "B0"} & set(s)
        assert sorted(segs[x] for x in s) == ["A", "A", "B"]


def test_repo_label_file_loads_and_is_not_freezable_yet():
    labels = load_labels()
    assert not labels.frozen
    assert any("needs a date" in p for p in labels.problems())


def _repo_copy(tmp_path):
    for d in ("src", "config", "labels"):
        shutil.copytree(ROOT / d, tmp_path / d)
    return tmp_path


def test_freeze_refuses_incomplete_labels(tmp_path):
    root = _repo_copy(tmp_path)
    with pytest.raises(RuntimeError, match="frozen: true"):
        F.freeze(root)


def test_freeze_detects_changes_and_single_holdout(tmp_path):
    root = _repo_copy(tmp_path)
    lf = LabelFile(frozen=True, themes=[_theme("t", ["MU", "NVDA"], "2020-06-30")])
    (root / "labels" / "themes.yaml").write_text(
        yaml.safe_dump(json.loads(lf.model_dump_json())))
    F.freeze(root)
    assert F.guard_holdout("abc", root)["already_run"] is False
    F.log_holdout("abc", root)
    assert F.guard_holdout("abc", root)["already_run"] is True
    with pytest.raises(RuntimeError, match="different result"):
        F.guard_holdout("xyz", root)
    cfg = root / "config" / "pipeline.yaml"
    cfg.write_text(cfg.read_text().replace("window_days: 180", "window_days: 90"))
    with pytest.raises(RuntimeError, match="config"):
        F.check_freeze(root)


def test_metrics_when_no_label_has_dates():
    led = pd.DataFrame({"candidate_id": ["c1"], "t": pd.to_datetime(["2019-01-31"], utc=True),
                        "issuers": [["A", "B"]]})
    undated = Theme(id="t", period="dev", kind="theme", issuers=["A", "B"],
                    emergence_marker=Marker(), consensus_marker=Marker())
    m = match_candidates(led, [undated], 0.34, 540)
    metrics = detection_metrics(m, led, 5)
    assert metrics["themes_evaluable"] == 0 and metrics["themes_not_evaluable"] == 1
    assert metrics["unmatched_candidates"] == 1
    assert np.isnan(metrics["detection_recall"])
