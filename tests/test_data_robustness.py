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


class _Provider:
    """Adjusted closes by symbol and day; counts the days requested."""

    def __init__(self, days):
        self.days, self.px, self.requested = days, {}, []

    def prices(self, sym, start, end):
        rows = [{"date": str(d.date()), "close": v, "adjClose": v}
                for d, v in zip(self.days, self.px.get(sym, []), strict=False)
                if start <= d.date() <= end]
        self.requested.append((sym, len(rows)))
        return rows


def _universe(symbols=("A", "B")):
    from themegraph.config import Issuer, UniverseConfig
    return UniverseConfig(issuers=[Issuer(symbol=s, segment="x", role="core") for s in symbols],
                          benchmarks=["SPY"], sectors={"x": "X"})


def test_stored_history_is_reused_until_the_provider_restates_it(tmp_path, monkeypatch):
    import numpy as np

    from themegraph import ingest as I
    from themegraph.data.store import Store
    cfg = load_pipeline()
    days = pd.bdate_range("2024-01-02", periods=300)
    base = 100 + np.arange(300.0)
    prov = _Provider(days)
    prov.px = {"SPY": base.copy(), "A": base.copy() * 2, "B": base.copy() * 3}
    store, u = Store(tmp_path), _universe()
    ids = iter(["20250101T000000Z", "20250102T000000Z", "20250103T000000Z",
                "20250104T000000Z"])
    monkeypatch.setattr(I, "vintage_id", lambda: next(ids))
    info: dict = {}
    I.ingest_fmp_prices(prov, cfg, u, store, days[279].date(), log=lambda *a: None, info=info)
    assert info == {"reused": 0, "reloaded": {}, "full": 3}  # first time: everything

    # Next day: 20 more sessions exist; nothing restated. Only the recent stretch is
    # requested, and the stored series is extended.
    prov.requested.clear()
    info = {}
    I.ingest_fmp_prices(prov, cfg, u, store, days[299].date(), log=lambda *a: None, info=info)
    assert info["reused"] == 3 and info["full"] == 0 and not info["reloaded"]
    assert all(n < 80 for _, n in prov.requested) and len(prov.requested) == 3
    now = store.read("prices")
    a = now.loc[now["symbol"] == "A"].sort_values("date")
    assert len(a) == 300 and a["close"].tolist() == (base * 2).tolist()
    assert set(now["vintage"]) == {"20250102T000000Z"}  # each whole series in one vintage

    # A 2:1 split in A: the provider restates A's whole history.
    prov.px["A"] = base.copy()
    prov.requested.clear()
    info = {}
    I.ingest_fmp_prices(prov, cfg, u, store, days[299].date(), log=lambda *a: None, info=info)
    assert list(info["reloaded"]) == ["A"] and "restated" in info["reloaded"]["A"]
    assert info["reused"] == 2 and info["full"] == 1
    a = store.read("prices").query("symbol == 'A'").sort_values("date")
    assert a["close"].tolist() == base.tolist()  # the whole history on the new basis
    assert ("A", 300) in prov.requested           # A was downloaded in full, the others not

    # --full-prices downloads everything again.
    info = {}
    I.ingest_fmp_prices(prov, cfg, u, store, days[299].date(), log=lambda *a: None,
                        info=info, full=True)
    assert info["reused"] == 0 and info["full"] == 3


def test_a_symbol_with_nothing_new_keeps_its_stored_history(tmp_path, monkeypatch):
    import numpy as np

    from themegraph import ingest as I
    from themegraph.data.store import Store
    cfg = load_pipeline()
    days = pd.bdate_range("2024-01-02", periods=200)
    prov = _Provider(days)
    prov.px = {"SPY": 100 + np.arange(200.0), "A": 50 + np.arange(200.0),
               "B": (10 + np.arange(120.0))}  # B stopped trading after 120 sessions
    store, u = Store(tmp_path), _universe()
    ids = iter(["20250101T000000Z", "20250102T000000Z"])
    monkeypatch.setattr(I, "vintage_id", lambda: next(ids))
    I.ingest_fmp_prices(prov, cfg, u, store, days[-1].date(), log=lambda *a: None)
    info: dict = {}
    I.ingest_fmp_prices(prov, cfg, u, store, days[-1].date(), log=lambda *a: None, info=info)
    assert info["reused"] == 3 and not info["reloaded"]
    b = store.read("prices").query("symbol == 'B'")
    assert len(b) == 120  # kept, not lost and not downloaded again
