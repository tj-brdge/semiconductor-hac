"""Local review server: blind sample, who may decide what, worksheet round trip, token."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pandas as pd
import pytest

from themegraph.eval import adjudication as A
from themegraph.review import server as R


def _run(tmp_path, n_unmatched=25):
    t = pd.Timestamp("2019-01-31", tz="UTC")
    ids = ["m1"] + [f"u{i:02d}" for i in range(n_unmatched)]
    pd.DataFrame({"candidate_id": ids, "track_id": range(len(ids)), "t": t, "t_first_flag": t,
                  "issuers": [["A", "B"]] * len(ids)}).to_parquet(
        tmp_path / "candidate_ledger.parquet")
    pd.DataFrame({"theme": ["hbm_2023"], "kind": ["theme"], "evaluable": [True],
                  "detected": [True], "candidate_id": ["m1"]}).to_parquet(
        tmp_path / "eval_matches.parquet")
    (tmp_path / "explanations.json").write_text(json.dumps([
        {"candidate_id": c, "keywords": ["hbm", c],
         "quotes": [{"passage_id": f"{c}#1", "symbol": "MU", "available_at": "2019-01-20",
                     "text": "HBM stacking capacity"}]} for c in ids]))
    return tmp_path


def _suggest_all(store, run="w"):
    for _ in range(50):
        if not store.suggest_next(run)["remaining"]:
            return
    raise AssertionError("suggestions did not finish")


class FakeClaude:
    def __init__(self, answer=None):
        self.calls, self.answer = [], answer

    def __call__(self, prompt, choices):
        self.calls.append(prompt)
        ids = [line[4:] for line in prompt.splitlines() if line.startswith("id: ")]
        pick = self.answer if self.answer in choices else choices[0]
        return [{"id": i, "decision": pick, "duplicate_of": "", "reason": "r"} for i in ids], \
            "claude-opus-5-5"


def test_blind_sample_hides_suggestions_until_the_person_decides(tmp_path):
    run = _run(tmp_path)
    store = R.ReviewStore({"w": run}, FakeClaude("noise"))
    _suggest_all(store)
    v = {e["id"]: e for e in store.view("w")["entries"]}
    sample = [i for i, e in v.items() if e["in_sample"]]
    assert len(sample) == R.BLIND_SAMPLE
    assert all("ai_suggestion" not in v[i] for i in sample)  # hidden server-side
    outside = next(i for i, e in v.items() if not e["in_sample"]
                   and e["kind"] == "unmatched_candidate")
    assert v[outside]["ai_suggestion"] == "noise"
    store.decide("w", sample[0], "real_unlabelled_theme", "TJ")
    e = {x["id"]: x for x in store.view("w")["entries"]}[sample[0]]
    assert e["ai_suggestion"] == "noise" and e["blind_human_decision"] == "real_unlabelled_theme"
    store.decide("w", sample[0], "noise", "TJ")  # changing it later keeps the blind answer
    e = {x["id"]: x for x in store.view("w")["entries"]}[sample[0]]
    assert e["blind_human_decision"] == "real_unlabelled_theme" and e["decision"] == "noise"
    # The sample is fixed by the ledger: a new server picks the same entries.
    again = R.ReviewStore({"w": run}, None)
    assert {e["id"] for e in again.view("w")["entries"] if e["in_sample"]} == set(sample)


def test_ai_decisions_only_for_unmatched_after_the_sample(tmp_path):
    run = _run(tmp_path)
    store = R.ReviewStore({"w": run}, FakeClaude("noise"))
    _suggest_all(store)
    assert store.accept_ai("w") == 0  # sample not done yet
    for e in store.view("w")["entries"]:
        if e["in_sample"]:
            store.decide("w", e["id"], "noise", "TJ")
    n = store.accept_ai("w")
    entries = {e["id"]: e for e in A.load(run / A.WORKSHEET)}
    assert n == 25 - R.BLIND_SAMPLE
    assert entries["matched_theme:hbm_2023:m1"]["decision"] == "pending"  # never bulk
    ai = [e for e in entries.values() if e.get("decided_by") == "ai"]
    assert len(ai) == n and all(e["reviewer"] == "claude-opus-5-5" for e in ai)
    s = A.summary(list(entries.values()), 1)
    assert s["blind_pairs"] == R.BLIND_SAMPLE and s["blind_agreement"] == 1.0
    assert s["decided_by_ai"] == n


def test_decisions_are_validated_and_need_a_name(tmp_path):
    run = _run(tmp_path, n_unmatched=3)
    store = R.ReviewStore({"w": run}, None)
    with pytest.raises(ValueError, match="name"):
        store.decide("w", "matched_theme:hbm_2023:m1", "confirmed", " ")
    with pytest.raises(ValueError, match="not a decision"):
        store.decide("w", "matched_theme:hbm_2023:m1", "noise", "TJ")
    store.decide("w", "matched_theme:hbm_2023:m1", "confirmed", "TJ", "clear HBM subject")
    e = A.load(run / A.WORKSHEET)[0]
    assert (e["decision"], e["decided_by"], e["notes"]) == ("confirmed", "human",
                                                              "clear HBM subject")
    assert "Decision: confirmed" in (run / A.PACKET).read_text()
    with pytest.raises(RuntimeError, match="review extra"):
        store.suggest_next("w")


def test_prompts_carry_text_only_and_matches_go_first(tmp_path):
    run = _run(tmp_path, n_unmatched=2)
    fake = FakeClaude()
    store = R.ReviewStore({"w": run}, fake)
    store.suggest_next("w")
    assert "labelled theme: hbm 2023" in fake.calls[0] and "u00" not in fake.calls[0]
    store.suggest_next("w")
    p = fake.calls[1]
    assert "hbm_2023" not in p and "labelled theme" not in p  # unmatched prompts are blind
    assert "HBM stacking capacity" in p and "return" not in p.lower()


def test_api_requires_the_session_token(tmp_path):
    run = _run(tmp_path, n_unmatched=2)
    store = R.ReviewStore({"w": run}, None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), R.make_handler(store, "tok"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Theme Adjudication Desk" in page
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/run/w")
        assert e.value.code == 403
        req = urllib.request.Request(base + "/api/decide", method="POST",
                                     headers={"X-Review-Token": "tok",
                                              "Content-Type": "application/json"},
                                     data=json.dumps({"run": "w",
                                                      "id": "matched_theme:hbm_2023:m1",
                                                      "decision": "rejected",
                                                      "reviewer": "TJ"}).encode())
        out = json.loads(urllib.request.urlopen(req).read())
        assert out["stats"]["decided_by_human"] == 1
    finally:
        httpd.shutdown()
