import json

import pandas as pd
import pytest

from themegraph.config import Availability
from themegraph.data import edgar as E


def test_html_to_text_drops_hidden_and_scripts():
    html = """<html><head><title>x</title></head><body>
      <ix:header><div>hidden xbrl</div></ix:header>
      <div style="display: none"><div>nested hidden</div><p>still hidden</p></div>
      <p>Revenue grew&nbsp;20%.</p><script>var a=1;</script><div>Visible <b>bold</b></div>
    </body></html>"""
    text = E.html_to_text(html)
    assert "Revenue grew 20%." in text and "Visible bold" in text
    for bad in ("hidden", "var a", "x\n"):
        assert bad not in text


def test_split_sections_skips_table_of_contents():
    body = " ".join(["word"] * 120)
    text = (f"Item 1. Business 3\nItem 1A. Risk Factors 9\nItem 7. Management's Discussion 30\n"
            f"Item 8. 50\nItem 1. Business\n{body}\nItem 1A. Risk Factors\n{body} risks\n"
            f"Item 1B. Unresolved\nItem 7. Management's Discussion and Analysis\n{body} demand"
            f"\nItem 7A. Quantitative\nItem 8. Financial")
    s = E.split_sections(text, "10-K")
    assert set(s) == {"business", "risk_factors", "mdna"}
    assert s["mdna"].endswith("demand")


def test_acceptance_time_is_eastern():
    out = E.accepted_utc(pd.Series(["2024-01-25T16:05:00.000Z", "2024-07-25T16:05:00.000Z"]))
    assert out[0] == pd.Timestamp("2024-01-25 21:05", tz="UTC")  # EST
    assert out[1] == pd.Timestamp("2024-07-25 20:05", tz="UTC")  # EDT


def test_client_requires_contact_user_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError):
        E.EdgarClient(tmp_path, user_agent="no contact")


def test_filings_pages_and_selection(fake_fetcher, cfg):
    cols = ["accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form",
            "primaryDocument"]
    recent = dict(zip(cols, [["0001-24-000002", "0001-24-000003"], ["2024-03-01", "2024-03-02"],
                             ["2023-12-31", ""], ["2024-03-01T17:00:00.000Z",
                                                  "2024-03-02T08:00:00.000Z"],
                             ["10-K", "4"], ["k.htm", "f4.xml"]], strict=True))
    older = dict(zip(cols, [["0001-15-000001"], ["2015-03-01"], ["2014-12-31"],
                            ["2015-03-01T16:30:00.000Z"], ["10-K"], ["old.htm"]], strict=True))
    f = fake_fetcher({
        "CIK0000000001.json": json.dumps({"filings": {"recent": recent, "files": [
            {"name": "CIK0000000001-submissions-001.json"}]}}).encode(),
        "CIK0000000001-submissions-001.json": json.dumps(older).encode(),
        "k.htm": b"<p>Item 7. Management's Discussion " + b"demand " * 80 + b"</p>",
    })
    c = E.EdgarClient(None, fetcher=f)
    filings = c.filings(1)
    assert len(filings) == 3
    sel = E.select_filings(filings, "2013-01-01", "2024-12-31", {"10-K"})
    assert list(sel["accessionNumber"]) == ["0001-15-000001", "0001-24-000002"]
    texts = {"0001-24-000002": E.split_sections(
        E.html_to_text(c.document(1, "0001-24-000002", "k.htm")), "10-K")}
    docs = E.filing_documents(sel, "XYZ", texts, Availability())
    assert docs["doc_type"].tolist() == ["10-K:mdna"]
    assert docs["available_at"].iloc[0] == pd.Timestamp("2024-03-01 22:00", tz="UTC")


def test_exhibit_names():
    assert E.exhibit_names(["a8k.htm", "ex99-1.htm", "EX-99.2.htm", "ex99.jpg"]) == \
        ["ex99-1.htm", "EX-99.2.htm"]
