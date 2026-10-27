"""Readable theme labels: grounded in cited evidence, honest fallback, history kept."""

import json

import pandas as pd

from themegraph.monitor import naming as N
from themegraph.monitor import refresh as M


def _theme(tid="th-00001", name="ethernet · transceivers · enterprise"):
    q = [{"passage_id": f"p{i}", "symbol": s, "date": "2021-05-01", "role": r,
          "text": f"text {i}"}
         for i, (s, r) in enumerate([("NET1", "management_remarks"),
                                     ("NET2", "management_answer"),
                                     ("NET1", "analyst_question")])]
    return {"id": tid, "name": name, "keywords": name.split(" · ") + ["optics"],
            "measures": [{"t": "2021-05-31", "present": True, "n_issuers": 2,
                          "issuers": ["NET1", "NET2"], "sectors": ["Networking"],
                          "evidence_roles": {"management_remarks": 0.7, "filing": 0.3}}],
            "evidence": {"latest": q, "first": []}}


def _namer(answer):
    calls = []

    def f(prompt):
        calls.append(prompt)
        return answer, "test-model"
    f.calls = calls
    return f


GOOD = {"status": "clear", "title": "Faster data-centre networking",
        "summary": "Companies describe customers upgrading switches and optics.",
        "evidence": ["p0", "p1"], "reason": ""}


def test_keyword_label_claims_no_thesis():
    lab = N.keyword_label(_theme())
    assert lab["title"] == "Ethernet, transceivers and enterprise"
    assert lab["source"] == "keywords" and lab["status"] == "automatic"
    assert lab["summary"].startswith("Discussed by 2 monitored companies in Networking")


def test_grounded_claude_label_is_used_and_recorded():
    th, namer = _theme(), _namer(GOOD)
    c = N.apply_labels([th], {}, namer, curated={}, now="t1")
    assert c["claude"] == 1 and th["label"]["title"] == GOOD["title"]
    assert th["label"]["evidence"] == ["p0", "p1"] and th["label"]["model"] == "test-model"
    assert "analyst_question" in namer.calls[0]  # roles are shown to the writer
    # Rerun: nothing to do, no call, no new history.
    N.apply_labels([th], {}, namer, curated={}, now="t2")
    assert len(namer.calls) == 1 and len(th["label_history"]) == 1
    # Keywords drift: the theme keeps its ID, gets a new label, history is kept.
    th["name"] = "copper · cables · reach"
    N.apply_labels([th], {}, namer, curated={}, now="t3")
    assert th["id"] == "th-00001" and len(th["label_history"]) == 2


def test_ungrounded_or_ambiguous_answers_fall_back_honestly():
    for bad in ({**GOOD, "evidence": ["p0", "p9"]},           # cites a passage not shown
                {**GOOD, "evidence": ["p0", "p2"]},           # one company only
                {**GOOD, "status": "ambiguous", "reason": "two topics"},
                {**GOOD, "title": "A very long title that goes on and on forever"}):
        th = _theme()
        N.apply_labels([th], {}, _namer(bad), curated={}, now="t")
        lab = th["label"]
        assert lab["status"] == "ambiguous" and lab["source"] == "keywords"
        assert lab["title"] == "Ethernet, transceivers and enterprise" and lab["note"]


def test_curated_label_wins_and_automatic_labels_upgrade_later():
    th = _theme()
    N.apply_labels([th], {}, None, curated={}, now="t1")
    assert th["label"]["status"] == "automatic"
    N.apply_labels([th], {}, _namer(GOOD), curated={}, now="t2")  # writer now available
    assert th["label"]["source"] == "claude"
    cur = {"th-00001": {"title": "Data-centre networking", "summary": "Edited.",
                        "source": "curated", "status": "ok", "evidence": [], "model": None}}
    N.apply_labels([th], {}, _namer(GOOD), curated=cur, now="t3")
    assert th["label"]["source"] == "curated"
    assert [h["source"] for h in th["label_history"]] == ["keywords", "claude", "curated"]


def test_refresh_labels_themes_and_a_writer_failure_never_blocks_a_release(tmp_path):
    state, site = tmp_path / "state", tmp_path / "site"

    def broken(prompt):
        raise RuntimeError("API unavailable")
    st = M.refresh(state, site, fixtures=True, as_of=pd.Timestamp("2021-06-30").date(),
                   log=lambda *a: None, namer=broken)
    assert st["ok"] and "API unavailable" in st["warnings"]["labels"]
    rel = site / "data" / json.loads((site / "data" / "latest.json").read_text())["path"]
    idx = json.loads((rel / "index.json").read_text())
    assert all(r["title"] and r["summary"] and r["label_source"] == "keywords"
               for r in idx["themes"])

    def fake(prompt):  # cites the first two passages from different companies
        ids = [line[1:line.index("]")] for line in prompt.splitlines()
               if line.startswith("[")]
        syms = [line.split("] ")[1].split(" ·")[0] for line in prompt.splitlines()
                if line.startswith("[")]
        pick = [ids[0]] + [i for i, s in zip(ids, syms, strict=True) if s != syms[0]][:1]
        return {**GOOD, "evidence": pick}, "fake"
    st = M.refresh(state, site, fixtures=True, as_of=pd.Timestamp("2021-06-30").date(),
                   log=lambda *a: None, namer=fake)
    assert st["ok"] and not st["warnings"]
    reg = json.loads((state / "registry.json").read_text())
    pub = [t for t in reg["themes"].values() if t["published"]]
    assert all(t["label"]["source"] == "claude" for t in pub)
