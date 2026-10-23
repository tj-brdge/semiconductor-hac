import json

import pandas as pd
import pytest
import yaml

from themegraph.eval import adjudication as A


def _run(tmp_path):
    t = pd.Timestamp("2019-01-31", tz="UTC")
    pd.DataFrame({"candidate_id": ["c1", "c2"], "track_id": [1, 2], "t": [t, t],
                  "t_first_flag": [t, t], "issuers": [["A", "B"], ["C", "D"]]}
                 ).to_parquet(tmp_path / "candidate_ledger.parquet")
    pd.DataFrame({"theme": ["hbm"], "kind": ["theme"], "evaluable": [True],
                  "detected": [True], "candidate_id": ["c1"]}
                 ).to_parquet(tmp_path / "eval_matches.parquet")
    (tmp_path / "explanations.json").write_text(json.dumps([
        {"candidate_id": "c1", "keywords": ["hbm", "stacking"],
         "quotes": [{"passage_id": "d#1"}]}]))
    return tmp_path


def test_worksheet_lists_matches_and_blind_unmatched(tmp_path):
    run = _run(tmp_path)
    entries = A.load(A.write_worksheet(run))
    kinds = {e["candidate_id"]: e for e in entries}
    assert kinds["c1"]["kind"] == "matched_theme" and kinds["c1"]["theme"] == "hbm"
    assert kinds["c1"]["as_of_keywords"] == ["hbm", "stacking"]
    assert kinds["c2"]["kind"] == "unmatched_candidate" and "theme" not in kinds["c2"]
    assert all(e["decision"] == "pending" for e in entries)


def test_decisions_survive_regeneration_and_are_validated(tmp_path):
    run = _run(tmp_path)
    path = A.write_worksheet(run)
    entries = yaml.safe_load(path.read_text())
    entries[0].update(decision="confirmed", reviewer="TJ", reviewed_on="2026-10-24")
    path.write_text(yaml.safe_dump(entries))
    entries = A.load(A.write_worksheet(run))
    assert entries[0]["decision"] == "confirmed"
    s = A.summary(entries, themes_evaluable=2)
    assert s["matches_confirmed"] == 1 and s["confirmed_recall"] == 0.5 and s["pending"] == 1
    entries[1].update(decision="maybe", reviewer="TJ")
    path.write_text(yaml.safe_dump(entries))
    with pytest.raises(ValueError, match="not in"):
        A.load(path)
    entries[1].update(decision="noise", reviewer=None)
    path.write_text(yaml.safe_dump(entries))
    with pytest.raises(ValueError, match="reviewer"):
        A.load(path)
