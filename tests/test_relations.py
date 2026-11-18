"""Named business relationships between covered companies (monitor/relations.py)."""

import datetime as dt
import json

import pandas as pd

from themegraph.config import Issuer, UniverseConfig
from themegraph.monitor import relations as R

U = UniverseConfig(
    issuers=[Issuer(symbol="NVDA", name="Nvidia", aliases=["NVIDIA"], segment="x", role="core"),
             Issuer(symbol="TSM", name="Taiwan Semiconductor (TSMC)", segment="x", role="core"),
             Issuer(symbol="MU", name="Micron Technology", segment="x", role="core"),
             Issuer(symbol="COHR", name="Coherent", segment="x", role="core")],
    benchmarks=["SPY"], sectors={"x": "X"})

FILL = " ".join(["capacity"] * 60)


def _doc(sym, doc_id, day, text, doc_type="10-K"):
    ts = pd.Timestamp(day, tz="UTC")
    return {"symbol": sym, "doc_id": doc_id, "doc_type": doc_type, "published_at": ts,
            "available_at": ts, "text": text}


def _docs():
    return pd.DataFrame([
        _doc("NVDA", "n1", "2026-03-01",
             f"{FILL} We rely on TSMC to manufacture our wafers. {FILL}"),
        _doc("NVDA", "n0", "2025-06-01", f"{FILL} Taiwan Semiconductor builds our chips. {FILL}"),
        _doc("NVDA", "old", "2020-01-01", f"{FILL} Micron supplies memory to us. {FILL}"),
        _doc("TSM", "t1", "2026-02-01", f"{FILL} NVIDIA is one of our largest customers. {FILL}"),
        _doc("MU", "m1", "2026-04-01",
             f"{FILL} We compete with coherent pricing, and MU is us. {FILL}"),
        _doc("MU", "m2", "2026-04-02", f"{FILL} Coherent buys our parts. {FILL}"),
    ])


def test_names_are_recognised_but_tickers_and_ordinary_words_are_not():
    al = R.aliases(U)
    assert al["TSM"] == ["Taiwan Semiconductor", "TSMC"]
    assert al["MU"] == ["Micron Technology", "Micron"] and "NVIDIA" in al["NVDA"]
    c = R.candidates(_docs(), U, pd.Timestamp("2026-06-30"))
    assert set(c) == {("NVDA", "TSM"), ("TSM", "NVDA"), ("MU", "COHR")}
    # Newest first, one passage per document; the 2020 statement is too old to read.
    assert [r["date"] for r in c[("NVDA", "TSM")]] == ["2026-03-01", "2025-06-01"]
    assert "TSMC" in c[("NVDA", "TSM")][0]["text"]
    # "coherent pricing" (lower case) and the ticker "MU" are not names.
    assert len(c[("MU", "COHR")]) == 1 and c[("MU", "COHR")][0]["date"] == "2026-04-02"


def test_analyst_questions_are_not_company_statements():
    call = ("Jane Doe: Good afternoon. " + FILL + ".\n"
            "Operator: We will now begin the question-and-answer session.\n"
            "Analyst One: How dependent are you on TSMC for supply? " + FILL + ".\n"
            "Jane Doe: We have a long partnership with TSMC. " + FILL + ".\n")
    docs = pd.DataFrame([_doc("NVDA", "c1", "2026-05-01", call, "transcript")])
    c = R.candidates(docs, U, pd.Timestamp("2026-06-30"))
    roles = {r["role"] for r in c.get(("NVDA", "TSM"), [])}
    assert roles and "analyst_question" not in roles


class Namer:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def relation(self, prompt):
        self.calls.append(prompt)
        speaker = prompt.split("passages from ")[1].split("'s own")[0]
        return self.answers[speaker](prompt), "m"


def _first_id(prompt):
    return prompt.split("Passages:\n[")[1].split("]")[0]


def test_statements_are_read_once_checked_and_joined_into_links(tmp_path):
    cands = R.candidates(_docs(), U, pd.Timestamp("2026-06-30"))
    names = {i.symbol: i.name for i in U.issuers}
    namer = Namer({
        "Nvidia": lambda p: {"relationship": "buys_from", "evidence": _first_id(p),
                             "summary": "Nvidia relies on TSMC to manufacture its wafers."},
        "Taiwan Semiconductor (TSMC)": lambda p: {
            "relationship": "supplies", "evidence": _first_id(p),
            "summary": "TSMC names Nvidia as one of its largest customers."},
        "Micron Technology": lambda p: {"relationship": "supplies", "evidence": "made-up#9",
                                        "summary": "x"},
    })
    store = R.Relations(tmp_path)
    c = R.read_pairs(store, cands, names, namer, "now")
    assert c["read"] == 3 and c["kept"] == 2 and c["none"] == 1 and c["waiting"] == 0
    # The answer citing a passage it was not shown is recorded as no relationship.
    assert store.pairs["MU>COHR"]["relationship"] == "none"
    assert "cited no passage" in store.pairs["MU>COHR"]["note"]

    links = R.links(R.Relations(tmp_path), {"NVDA", "TSM", "MU", "COHR"})
    assert len(links) == 1
    link = links[0]
    assert (link["kind"], link["a"], link["b"]) == ("supply", "TSM", "NVDA")   # TSM supplies NVDA
    assert link["both_sides"] and link["last_said"] == "2026-03-01"
    assert {s["said_by"] for s in link["statements"]} == {"NVDA", "TSM"}
    assert all(s["text"] and s["date"] for s in link["statements"])

    # Nothing is read twice; a newer passage for a pair is read again.
    assert R.read_pairs(store, cands, names, namer, "now")["read"] == 0 and len(namer.calls) == 3
    docs = pd.concat([_docs(), pd.DataFrame([_doc(
        "TSM", "t2", "2026-06-01", f"{FILL} Nvidia remains a major customer. {FILL}")])])
    again = R.candidates(docs, U, pd.Timestamp("2026-06-30"))
    assert store.todo(again) == [("TSM", "NVDA")]
    # Without a writer nothing is read and the count waiting is reported.
    empty = R.Relations(tmp_path / "x")
    assert R.read_pairs(empty, cands, names, None, "now")["waiting"] == 3


def test_refresh_publishes_business_links_and_makes_no_call_without_names(tmp_path):
    from themegraph.monitor import refresh as M
    st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                   as_of=dt.date(2021, 6, 30), log=lambda *a: None)
    assert st["ok"], st["errors"]
    data = tmp_path / "site" / "data"
    rel = data / json.loads((data / "latest.json").read_text())["path"]
    biz = json.loads((rel / "index.json").read_text())["business"]
    assert biz["links"] == [] and biz["lookback_months"] == R.LOOKBACK_MONTHS
