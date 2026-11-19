"""SK hynix's SEC filings reach theme detection through the ordinary EDGAR path
(config/universe.yaml; docs/MONITOR.md, "Universe changes"). Nothing here puts
the company in a theme: that depends on what its passages say."""

import datetime as dt

import pandas as pd

from themegraph import ingest as I
from themegraph.config import load_pipeline, load_universe
from themegraph.data.store import Store
from themegraph.monitor.coverage import Coverage
from themegraph.monitor.profiles import load_profile
from themegraph.text.passages import passages_from_documents

CIK = 2120882
PROSE = ("Demand for high bandwidth memory used in AI servers continued to grow and the "
         "company expanded advanced packaging capacity at its new fabrication plant. " * 12)


def test_one_tracked_security_now_read_from_edgar():
    for prof in ("semis", "tech-v2"):
        u = load_universe(load_profile(prof).universe)
        hits = [i for i in u.issuers if i.cik == CIK or "hynix" in (i.name or "").lower()]
        assert len(hits) == 1, prof                 # the ADR is not a second company
        sk = hits[0]
        assert sk.symbol == "000660.KS" and sk.currency == "KRW" and sk.cik == CIK
        assert sk.sec_filer and sk.segment == "memory" and sk.role == "core"
        assert sk.covered_from is None              # coverage itself did not change
        # The other issuers that do not file with the SEC are left as they were.
        assert {i.symbol for i in u.issuers if not i.sec_filer} == \
            {"005930.KS", "2408.TW", "8035.T", "6857.T"}
        assert not any(c.added or c.removed for c in u.changes if "SK hynix" in c.note)


class FakeEdgar:
    """The filing list shape EDGAR returns for SK hynix: 6-Ks with and without an
    exhibit 99, an amendment, and forms the pipeline does not read."""

    def __init__(self):
        self.asked = []
        self.rows = pd.DataFrame([
            ("0001193125-26-303983", "6-K", "2026-07-15T06:10:00.000Z", "a6k.htm"),
            ("0001193125-26-321989", "6-K", "2026-07-29T06:05:00.000Z", "b6k.htm"),
            ("0001193125-26-430987", "6-K/A", "2026-10-26T06:00:00.000Z", "c6ka.htm"),
            ("0001193125-26-280172", "F-1", "2026-06-24T16:00:00.000Z", "f1.htm"),
            ("0001193125-26-459140", "SCHEDULE 13G", "2026-11-13T16:00:00.000Z", "g.htm"),
        ], columns=["accessionNumber", "form", "acceptanceDateTime", "primaryDocument"])
        self.rows["filingDate"] = self.rows["acceptanceDateTime"].str[:10]
        self.rows["reportDate"] = ""

    def ticker_map(self):
        return {}                                   # a Korean ticker is not in the SEC map

    def filings(self, cik):
        self.asked.append(cik)
        return self.rows.assign(cik=cik)

    def filing_index(self, cik, accession):
        return ["b6k.htm", "dex991.htm"] if accession.endswith("321989") else ["a6k.htm"]

    def document(self, cik, accession, name):
        body = {"a6k.htm": "Quarterly business report. " + PROSE,
                "dex991.htm": "Earnings release. " + PROSE,
                "b6k.htm": "Cover page only."}[name]
        return f"<html><body><p>{body}</p></body></html>".encode()


def test_its_6k_filings_become_documents_and_passages(tmp_path):
    cfg = load_pipeline(load_profile("tech-v2").pipeline)
    u = load_universe(load_profile("tech-v2").universe)
    client, store = FakeEdgar(), Store(tmp_path)
    I.ingest_edgar(client, cfg, u, store, dt.date(2026, 11, 19), symbols=["000660.KS"],
                   log=lambda *a: None)
    assert client.asked == [CIK]                    # the configured CIK, no ticker lookup
    docs = store.read("documents_edgar")
    assert set(docs["symbol"]) == {"000660.KS"}
    # Two 6-Ks: the amendment, the F-1 and the 13G are not read.
    assert sorted(docs["doc_type"]) == ["6-K:ex99", "6-K:ex99"] and len(docs) == 2
    by = {d.doc_id: d for d in docs.itertuples()}
    plain = by["edgar:0001193125-26-303983:ex99"]   # no exhibit: the primary document
    assert "Quarterly business report" in plain.text
    release = by["edgar:0001193125-26-321989:ex99"]  # with an exhibit: the exhibit only
    assert "Earnings release" in release.text and "Cover page" not in release.text
    assert plain.url.startswith(f"https://www.sec.gov/Archives/edgar/data/{CIK}/")
    # Usable only after it was filed: nothing is backdated.
    assert (docs["available_at"] >= docs["published_at"]).all()
    assert docs["published_at"].min() >= pd.Timestamp("2026-07-15", tz="UTC")

    p = passages_from_documents(docs, cfg.text.chunk_words, cfg.text.min_chunk_words)
    assert len(p) and set(p["symbol"]) == {"000660.KS"} and set(p["genre"]) == {"6-K:ex99"}
    assert len(set(p["passage_id"])) == len(p)      # one earnings event, no repeated passage


def test_new_documents_do_not_count_as_growth_or_rewrite_history():
    """Its SEC history starts in July 2026. Until twelve months of documents exist it
    is listed with a theme but left out of growth comparisons, like any company whose
    documents are new (monitor/coverage.py)."""
    u = load_universe(load_profile("tech-v2").universe)
    first = {"000660.KS": pd.Timestamp("2026-07-15", tz="UTC"),
             "MU": pd.Timestamp("2010-02-01", tz="UTC")}
    cov = Coverage(u, first, 12)
    assert cov.covered("000660.KS", "2026-11-30")
    assert not cov.comparable("000660.KS", "2026-11-30", "2026-08-31")
    assert not cov.comparable("000660.KS", "2027-06-30", "2027-03-31")
    assert cov.comparable("000660.KS", "2027-07-31", "2027-04-30")
    assert cov.comparable("MU", "2026-11-30", "2026-08-31")
