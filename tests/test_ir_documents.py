"""Company investor-relations documents as a third, separately labelled source
(data/ir.py; docs/MONITOR.md, "Company IR documents")."""

import datetime as dt
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from themegraph import ingest as I
from themegraph.config import load_pipeline, load_universe
from themegraph.data import ir as R
from themegraph.data.store import Store
from themegraph.monitor.clusters import passage_roles
from themegraph.monitor.profiles import load_profile
from themegraph.text.passages import passages_from_documents

CFG = load_pipeline(load_profile("tech-v2").pipeline)
U = load_universe(load_profile("tech-v2").universe)
PROSE = ("Demand for server memory stayed strong and the company raised capital spending "
         "to add capacity for high bandwidth memory used in AI systems. " * 10)


def _pdf(text: str) -> bytes:
    """A one-page PDF with one line of text."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
        b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref


def _entry(sym="2408.TW", day="2026-10-12", kind="earnings_release", url=None):
    return R.IrDocument(symbol=sym, date=dt.date.fromisoformat(day), kind=kind,
                        url=url or f"https://example.com/{sym}-{day}-{kind}.html",
                        title="Third quarter results", period="2026Q3")


def test_web_pages_and_pdfs_are_turned_into_text():
    html = f"<html><script>x()</script><body><h1>Results</h1><p>{PROSE}</p></body></html>"
    assert "server memory" in R.extract_text(html.encode(), "https://example.com/a.html")
    pdf = _pdf("Server memory demand stayed strong in the quarter")
    assert R.is_pdf(pdf) and "Server memory demand" in R.extract_text(pdf)
    assert R.extract_text(_pdf(""), "https://example.com/scan.pdf") == ""   # nothing to read


def test_the_manifest_is_checked(tmp_path):
    assert R.load_manifest() == []                    # shipped empty: nothing is downloaded
    p = tmp_path / "m.yaml"
    row = "{symbol: 2408.TW, date: 2026-10-12, kind: %s, url: '%s'}"
    p.write_text("documents:\n  - " + row % ("earnings_release", "https://example.com/a"))
    assert R.load_manifest(p)[0].date == dt.date(2026, 10, 12)
    for bad, why in ((row % ("gossip", "https://example.com/a"), "kind must be"),
                     (row % ("news", "http://example.com/a"), "only https"),
                     ("\n  - ".join([row % ("news", "https://example.com/a")] * 2), "twice")):
        p.write_text("documents:\n  - " + bad)
        with pytest.raises(ValueError, match=why):
            R.load_manifest(p)


def test_listed_documents_are_read_once_and_labelled_as_their_own_source(tmp_path):
    store = Store(tmp_path)
    a, b = _entry(), _entry(kind="presentation")
    outside = _entry(sym="NOTCOVERED")
    scan = _entry(day="2026-07-10", kind="business_report")
    asked = []

    def text(d):
        asked.append(d.url)
        return "" if d is scan else PROSE
    client = NS(text=text)
    lines = []
    I.ingest_ir(client, CFG, U, store, [a, b, outside, scan], dt.date(2026, 11, 19),
                log=lines.append)
    docs = store.read("documents_ir")
    assert sorted(docs["doc_type"]) == ["ir:earnings_release", "ir:presentation"]
    assert set(docs["symbol"]) == {"2408.TW"} and set(docs["url"]) == {a.url, b.url}
    assert outside.url not in asked                   # not a covered company
    assert any("no readable text" in x for x in lines)
    # Published on a date, usable only after it: never on the day itself.
    assert (docs["published_at"] == pd.Timestamp("2026-10-13", tz="UTC")).all()
    assert (docs["available_at"] >= docs["published_at"]).all()
    # A second run reads only what is new.
    asked.clear()
    assert I.ingest_ir(client, CFG, U, store, [a, b], dt.date(2026, 11, 19),
                       log=lines.append) is None
    assert asked == []
    # In theme detection it is a company document: not a filing, not a call.
    p = passages_from_documents(I.load_documents(store, universe=U),
                                CFG.text.chunk_words, CFG.text.min_chunk_words)
    assert set(p["genre"]) == {"ir:earnings_release", "ir:presentation"}
    assert set(passage_roles(p)) == {"ir_document"}


def test_a_release_already_filed_with_the_sec_is_not_read_twice(tmp_path):
    store = Store(tmp_path)
    filed = pd.DataFrame([{"symbol": "000660.KS", "doc_id": "edgar:x:ex99",
                           "doc_type": "6-K:ex99", "period": "2026-10-28",
                           "published_at": pd.Timestamp("2026-10-28T06:00", tz="UTC"),
                           "available_at": pd.Timestamp("2026-10-29T06:00", tz="UTC"),
                           "text": PROSE, "url": "https://www.sec.gov/x"}])
    store.write("documents_edgar", I._stamp(filed), I.vintage_id())
    release = _entry(sym="000660.KS", day="2026-10-27")
    deck = _entry(sym="000660.KS", day="2026-10-27", kind="presentation")
    old = _entry(sym="000660.KS", day="2025-10-29")   # before it filed with the SEC
    lines = []
    I.ingest_ir(NS(text=lambda d: PROSE), CFG, U, store, [release, deck, old],
                dt.date(2026, 11, 19), log=lines.append)
    got = store.read("documents_ir")
    assert set(got["url"]) == {deck.url, old.url}
    assert any("SEC filing" in x and "skipped" in x for x in lines)


def test_a_failed_download_does_not_stop_the_rest(tmp_path):
    ok, bad = _entry(), _entry(kind="news")

    def text(d):
        if d is bad:
            raise OSError("HTTP 403")
        return PROSE
    lines = []
    I.ingest_ir(NS(text=text), CFG, U, Store(tmp_path), [bad, ok], dt.date(2026, 11, 19),
                log=lines.append)
    assert len(Store(tmp_path).read("documents_ir")) == 1 and any("403" in x for x in lines)
