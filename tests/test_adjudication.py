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


def _export(path, *entries, sha=None):
    path.write_text(json.dumps({"format": A.IMPORT_FORMAT, "run": "window",
                                "packet_sha256": sha, "entries": list(entries)}))
    return path


def _d(eid, decision, by="human", **kw):
    who = "TJ" if by == "human" else "claude:default"
    return {"id": eid, "decision": decision, "reviewer": who, "reviewed_on": "2026-10-26",
            "notes": "", "decided_by": by, **kw}


def test_import_merges_review_page_decisions_with_provenance(tmp_path):
    run = _run(tmp_path)
    A.write_worksheet(run)
    f = _export(tmp_path / "d.json",
                _d("matched_theme:hbm:c1", "confirmed", ai_suggestion="confirmed",
                   ai_reason="HBM stacking", ai_model="claude:default",
                   ai_prompt_version="adj-prompt-1"),
                _d("unmatched_candidate:c2", "noise", by="ai", ai_suggestion="noise",
                   blind_human_decision=None))
    out = A.import_decisions(run, f)
    assert out["applied"] == 2
    entries = A.load(A.write_worksheet(run))  # provenance survives a refresh
    e = {x["candidate_id"]: x for x in entries}
    assert e["c1"]["decided_by"] == "human" and e["c1"]["ai_reason"] == "HBM stacking"
    assert e["c2"]["decided_by"] == "ai" and e["c2"]["reviewer"] == "claude:default"
    s = A.summary(entries, themes_evaluable=1)
    assert s["decided_by_ai"] == 1 and s["decided_by_human"] == 1
    assert "Decision: noise" in (run / A.PACKET).read_text()


def test_import_refuses_wrong_run_bad_decisions_and_silent_overwrites(tmp_path):
    run = _run(tmp_path)
    A.write_worksheet(run)
    with pytest.raises(ValueError, match="wrong run"):
        A.import_decisions(run, _export(tmp_path / "a.json",
                                        _d("unmatched_candidate:zz", "noise")))
    with pytest.raises(ValueError, match="not allowed"):
        A.import_decisions(run, _export(tmp_path / "b.json",
                                        _d("unmatched_candidate:c2", "confirmed")))
    with pytest.raises(ValueError, match="decided_by"):
        A.import_decisions(run, _export(tmp_path / "c.json",
                                        _d("unmatched_candidate:c2", "noise", by="robot")))
    A.import_decisions(run, _export(tmp_path / "d.json", _d("unmatched_candidate:c2", "noise")))
    with pytest.raises(ValueError, match="--replace"):
        A.import_decisions(run, _export(tmp_path / "e.json",
                                        _d("unmatched_candidate:c2", "duplicate")))
    A.import_decisions(run, tmp_path / "e.json", replace=True)
    assert {e["candidate_id"]: e["decision"] for e in A.load(run / A.WORKSHEET)}["c2"] \
        == "duplicate"
    with pytest.raises(ValueError, match="not a themegraph-adjudication"):
        (tmp_path / "x.json").write_text("{}")
        A.import_decisions(run, tmp_path / "x.json")


def test_blind_agreement_counts_only_blind_pairs(tmp_path):
    run = _run(tmp_path)
    A.write_worksheet(run)
    A.import_decisions(run, _export(
        tmp_path / "d.json",
        _d("matched_theme:hbm:c1", "confirmed", ai_suggestion="rejected"),  # not blind
        _d("unmatched_candidate:c2", "noise", ai_suggestion="real_unlabelled_theme",
           blind_human_decision="noise")))
    s = A.summary(A.load(run / A.WORKSHEET), 1)
    assert s["blind_pairs"] == 1 and s["blind_agreement"] == 0.0


def test_ai_decisions_count_only_above_the_declared_agreement_bar():
    from themegraph.eval.compare import ai_decisions_admissible as ok
    assert ok({"decided_by_ai": 0})
    assert not ok({"decided_by_ai": 5, "blind_pairs": 20, "blind_agreement": 0.75})
    assert not ok({"decided_by_ai": 5, "blind_pairs": 12, "blind_agreement": 1.0})
    assert ok({"decided_by_ai": 5, "blind_pairs": 20, "blind_agreement": 0.8})
