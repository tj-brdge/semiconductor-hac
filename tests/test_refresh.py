"""Refresh behaviour: changing data is re-fetched per vintage, fixed data is reused."""

import datetime as dt
import json

import httpx

from themegraph.data.cache import RawCache
from themegraph.data.edgar import EdgarClient
from themegraph.data.fmp import FMPClient
from themegraph.data.http import CachedFetcher


def _client(tmp_path, source, body=b"[]"):
    calls = []

    def handler(request):
        calls.append(request.url.path + "?" + request.url.query.decode())
        return httpx.Response(200, content=body)

    fetcher = CachedFetcher(source, RawCache(tmp_path / source),
                            client=httpx.Client(transport=httpx.MockTransport(handler)))
    return fetcher, calls


def test_prices_refetched_per_vintage_transcripts_reused(tmp_path):
    f, calls = _client(tmp_path, "fmp")
    a = FMPClient(None, api_key="SECRETKEY123", fetcher=f, vintage="2026-10-01")
    a.prices("MU", dt.date(2020, 1, 1), dt.date(2021, 12, 31))
    a.prices("MU", dt.date(2020, 1, 1), dt.date(2021, 12, 31))
    a.transcript("MU", 2021, 1)
    assert len(calls) == 3  # two yearly price chunks + one transcript; repeat was cached

    b = FMPClient(None, api_key="SECRETKEY123", fetcher=f, vintage="2027-01-15")
    b.prices("MU", dt.date(2020, 1, 1), dt.date(2021, 12, 31))
    b.transcript("MU", 2021, 1)
    b.transcript_dates("MU")
    assert len(calls) == 6  # both price chunks again (one consistent vintage) + dates
    assert all("apikey=SECRETKEY123" in c for c in calls)  # the key is sent but never cached
    assert "SECRETKEY123" not in json.dumps(RawCache(tmp_path / "fmp").manifest())


def test_treasury_quarters_reused_when_start_moves_earlier(tmp_path):
    f, calls = _client(tmp_path, "fmp")
    c = FMPClient(None, api_key="SECRETKEY123", fetcher=f, vintage="2026-10-01")
    c.treasury(dt.date(2013, 1, 1), dt.date(2013, 12, 31))
    assert len(calls) == 4
    c.treasury(dt.date(2012, 11, 15), dt.date(2013, 12, 31))
    assert len(calls) == 5  # only the new partial quarter in 2012


def test_edgar_filing_list_refreshes_documents_do_not(tmp_path):
    sub = json.dumps({"filings": {"recent": {"accessionNumber": [], "form": []}}}).encode()
    f, calls = _client(tmp_path, "edgar", sub)
    for vintage in ("2026-10-01", "2027-01-15"):
        e = EdgarClient(None, fetcher=f, vintage=vintage)
        e.filings(1)
        e.document(1, "0001-24-000001", "k.htm")
    paths = [c.split("?")[0] for c in calls]
    assert paths.count("/submissions/CIK0000000001.json") == 2
    assert paths.count("/Archives/edgar/data/1/000124000001/k.htm") == 1
