"""Ingest must survive provider errors and interrupted writes."""

import httpx
import pandas as pd

from themegraph.config import load_pipeline
from themegraph.data import fmp as F
from themegraph.data.cache import RawCache
from themegraph.data.http import CachedFetcher


def test_error_sent_with_http_200_is_not_cached(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        body = {"Error Message": "Limit Reach"} if len(calls) == 1 else [{"x": 1}]
        return httpx.Response(200, json=body)

    f = CachedFetcher("fmp", RawCache(tmp_path), client=httpx.Client(
        transport=httpx.MockTransport(handler)))
    c = F.FMPClient(tmp_path, api_key="k", fetcher=f)
    try:
        c._get("quote")
        raise AssertionError("expected the provider's error")
    except RuntimeError as e:
        assert "Limit Reach" in str(e)
    assert c._get("quote") == [{"x": 1}] and len(calls) == 2  # before: the error forever
    assert c._get("quote") == [{"x": 1}] and len(calls) == 2  # the good answer is cached


def test_interrupted_cache_write_is_a_miss(tmp_path):
    cache = RawCache(tmp_path)
    cache.put("abcd", b"body", {"source": "t"})
    body, meta = cache._paths("abcd")
    meta.unlink()  # as if the process died between the two writes
    assert cache.get("abcd") is None
    assert cache.put("abcd", b"new", {"source": "t"}).body == b"new"


def test_empty_provider_responses_do_not_crash():
    rules = load_pipeline().availability
    assert F.normalise_treasury([], "year10", rules).empty
    assert F.normalise_monthly_economic([], rules).empty
    assert list(F.normalise_treasury([], "year10", rules).columns) == F.MACRO_COLUMNS


def test_one_symbol_without_prices_does_not_stop_the_ingest(tmp_path, monkeypatch):
    from themegraph import ingest as I
    from themegraph.config import Issuer, UniverseConfig
    from themegraph.data.store import Store
    cfg = load_pipeline()
    days = pd.bdate_range("2024-01-01", periods=5)

    class Client:
        def prices(self, sym, start, end):
            if sym == "EMPTY.KS":
                return []
            return [{"date": str(d.date()), "close": 100.0 + i, "adjClose": 100.0 + i,
                     "volume": 1} for i, d in enumerate(days)]
    u = UniverseConfig(issuers=[Issuer(symbol="EMPTY.KS", segment="x", role="core",
                                       currency="KRW"),
                                Issuer(symbol="OK", segment="x", role="core")],
                       benchmarks=["SPY"], sectors={"x": "X"})
    path = I.ingest_fmp_prices(Client(), cfg, u, Store(tmp_path), days[-1].date(),
                               log=lambda *a: None)
    got = pd.read_parquet(path)
    assert set(got["symbol"]) == {"SPY", "OK"}  # before: MergeError, nothing written
