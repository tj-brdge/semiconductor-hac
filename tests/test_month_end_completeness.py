"""A month-end is processed only once its documents have been downloaded after a
settling period; otherwise it stays pending (docs/MONITOR.md, "Month-end
completeness")."""

import datetime as dt
import json

import pandas as pd
import pytest

from themegraph.config import Issuer, UniverseConfig
from themegraph.data.store import Store
from themegraph.monitor import refresh as M
from themegraph.monitor.config import load_monitor

MC = load_monitor()
FEB = "2018-02-28"


def _hook(watermark, late=None):
    def hook(ctx, as_of):
        docs = ctx["docs"].assign(first_seen_at=pd.Timestamp("2017-01-01", tz="UTC"))
        if late is not None:
            docs.loc[late(docs), "first_seen_at"] = pd.Timestamp("2018-03-15", tz="UTC")
        wm = None if watermark is None else {
            "at": watermark, "sources": {"documents_transcripts": watermark}}
        return {"doc_watermark": wm, "docs": docs}
    return hook


def _run(root, as_of, watermark, **kw):
    return M.refresh(root / "state", root / "site", fixtures=True, as_of=as_of,
                     log=lambda *a: None, inputs_hook=_hook(watermark, **kw))


def _reg(root):
    return json.loads((root / "state" / "registry.json").read_text())


def _index(root):
    site = root / "site" / "data"
    return json.loads((site / json.loads((site / "latest.json").read_text())["path"]
                       / "index.json").read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("complete")
    out = {"root": root}
    # 3 Mar: documents were last downloaded on 20 Feb. January is settled, February not.
    out["early"] = _run(root, dt.date(2018, 3, 3), "2018-02-20 06:00:00+00:00")
    out["reg_early"] = _reg(root)
    out["idx_early"] = _index(root)
    # 8 Mar: a download was made on 6 Mar, more than five days after 28 Feb.
    out["later"] = _run(root, dt.date(2018, 3, 8), "2018-03-06 06:00:00+00:00")
    out["reg_later"] = _reg(root)
    out["again"] = _run(root, dt.date(2018, 3, 8), "2018-03-06 06:00:00+00:00")
    out["reg_again"] = _reg(root)
    return out


def test_month_end_waits_for_a_settled_document_download(runs):
    st, reg = runs["early"], runs["reg_early"]
    assert st["ok"], st["errors"]  # waiting is not a failure
    assert reg["last_snapshot"].startswith("2018-01-31")  # before: February processed
    (p,) = st["pending_snapshots"]
    assert p["t"].startswith(FEB) and p["needs_download_after"].startswith("2018-03-05")
    assert "last downloaded on 2018-02-20" in p["reason"]
    idx = runs["idx_early"]  # the release says so too, and stops at January
    assert idx["pending_snapshots"][0]["t"].startswith(FEB)
    assert idx["themes_processed_through"].startswith("2018-01-31")
    assert idx["completeness"]["settle_days"] == 5


def test_it_is_processed_once_the_condition_is_met_and_reruns_change_nothing(runs):
    st, reg = runs["later"], runs["reg_later"]
    assert st["ok"] and st["pending_snapshots"] == []
    assert reg["last_snapshot"].startswith(FEB)
    meta = reg["snapshot_meta"][reg["last_snapshot"]]
    assert meta["doc_watermark"].startswith("2018-03-06") and meta["processed_at"].startswith(
        "2018-03-08")
    # January keeps the record of what it was built from.
    jan = next(k for k in reg["snapshot_meta"] if k.startswith("2018-01-31"))
    assert reg["snapshot_meta"][jan]["doc_watermark"].startswith("2018-02-20")
    assert runs["again"]["ok"] and runs["reg_again"] == reg  # idempotent


def test_no_download_record_means_nothing_is_ready(tmp_path):
    todo = [pd.Timestamp("2018-01-31 23:59:59", tz="UTC")]
    ready, pending = M._complete(todo, {"at": None, "sources": {
        "documents_transcripts": "2018-03-01", "documents_edgar": None}}, MC)
    assert ready == [] and "documents_edgar" in pending[0]["reason"]
    assert M._complete(todo, None, MC) == (todo, [])  # fixture data: complete by construction
    # In order: a later month-end never jumps ahead of an earlier pending one.
    two = todo + [pd.Timestamp("2018-02-28 23:59:59", tz="UTC")]
    ready, pending = M._complete(two, {"at": "2018-02-06 00:00:00+00:00", "sources": {}}, MC)
    assert ready == todo and [p["t"][:10] for p in pending] == [FEB]


def test_failed_ingest_neither_advances_the_snapshot_nor_publishes(runs, monkeypatch):
    root = runs["root"]
    before_reg = (root / "state" / "registry.json").read_text()
    before_latest = (root / "site" / "data" / "latest.json").read_text()
    fixture_inputs = M.load_inputs

    def boom(kind, as_of, log=print, profile=None):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(M, "ingest_step", boom)
    monkeypatch.setattr(M, "load_inputs", lambda fixtures, as_of, log=print, profile=None:
                        fixture_inputs(True, as_of, log, profile))
    st = M.refresh(root / "state", root / "site", fixtures=False, as_of=dt.date(2018, 4, 10),
                   ingest_prices=False, ingest_documents=True, now="2018-04-10T23:59:59+00:00",
                   log=lambda *a: None)
    assert not st["ok"] and "provider unavailable" in st["errors"]["ingest_documents"]
    assert {"load", "themes", "valuation", "export"} <= set(st["skipped"])
    assert (root / "state" / "registry.json").read_text() == before_reg
    assert (root / "site" / "data" / "latest.json").read_text() == before_latest


def test_late_documents_are_reported_not_silently_lost(tmp_path):
    def late(docs):  # three February documents first downloaded on 15 March
        feb = docs.index[(docs["available_at"] > pd.Timestamp("2018-02-01", tz="UTC"))
                         & (docs["available_at"] <= pd.Timestamp("2018-02-28", tz="UTC"))]
        return feb[:3]
    first = _run(tmp_path, dt.date(2018, 3, 8), "2018-03-06 06:00:00+00:00")
    assert first["ok"] and first["late_documents"] is None
    st = _run(tmp_path, dt.date(2018, 3, 20), "2018-03-16 06:00:00+00:00", late=late)
    rep = st["late_documents"]
    assert rep["total"] == 3
    (ts,) = rep["by_snapshot"]
    assert ts.startswith(FEB) and rep["by_snapshot"][ts]["documents"] == 3
    assert "append-only" in rep["note"] and "later" in rep["note"]
    assert _index(tmp_path)["late_documents"]["total"] == 3
    assert _reg(tmp_path)["last_snapshot"].startswith(FEB)  # February itself is not rebuilt


def _docs(rows, seen):
    return pd.DataFrame([{"symbol": s, "doc_id": i, "doc_type": "8-K", "text": "x",
                          "published_at": seen, "available_at": seen, "first_seen_at": seen}
                         for s, i in rows])


def test_watermark_needs_every_source_and_ignores_partial_batches(tmp_path):
    u = UniverseConfig(issuers=[Issuer(symbol=s, segment="x", role="core") for s in "ABCDEFGHIJ"],
                       benchmarks=["SPY"], sectors={"x": "X"})
    st = Store(tmp_path)
    d = pd.Timestamp("2018-01-01", tz="UTC")
    everyone = [(s, f"t-{s}") for s in "ABCDEFGHIJ"]
    st.write("documents_transcripts", _docs(everyone, d), "20180205T060000Z")
    wm = M.document_watermark(st, u, 0.9)
    assert wm["at"].startswith("2018-02-05")  # one source in use: its download time
    st.write("documents_edgar", _docs([(s, f"e-{s}") for s in "ABCDEFGHIJ"], d),
             "20180201T060000Z")
    assert M.document_watermark(st, u, 0.9)["at"].startswith("2018-02-01")  # the earlier one
    # A later batch for two companies only does not count as a download of the universe.
    st.write("documents_edgar", _docs([("A", "e2-A"), ("B", "e2-B")], d), "20180310T060000Z")
    wm = M.document_watermark(st, u, 0.9)
    assert wm["sources"]["documents_edgar"].startswith("2018-02-01")
    st.write("documents_edgar", _docs([(s, f"e3-{s}") for s in "ABCDEFGHI"], d),
             "20180312T060000Z")  # nine of ten: counts
    assert M.document_watermark(st, u, 0.9)["at"].startswith("2018-02-05")


def test_plain_refresh_downloads_documents_when_they_are_due():
    from themegraph.monitor.profiles import load_profile
    prof = load_profile("tech-v2")
    now = "2018-03-08T22:30:00+00:00"

    def due(at):
        return M.documents_due(prof, MC, dt.date(2018, 3, 8), now,
                               watermark={"at": at, "sources": {}})
    assert due(None) == "no complete document download yet"
    assert due("2018-02-27 06:00:00+00:00") == "last download 9 days ago"
    # Downloaded 4 days ago, but before February settled (5 Mar): due again.
    assert "2018-02-28 month-end has settled" in due("2018-03-04 06:00:00+00:00")
    assert due("2018-03-06 06:00:00+00:00") is None  # recent and after the settle date


def test_prices_that_stopped_updating_are_named():
    import numpy as np

    from themegraph.monitor.baskets import PriceBook
    cal = pd.bdate_range("2024-01-02", periods=40, tz="UTC") + pd.Timedelta(hours=22)
    df = pd.DataFrame({s: 100 + np.arange(40.0) for s in ("SPY", "A", "B")}, index=cal)
    df.loc[df.index[-12:], "B"] = np.nan  # B stopped twelve sessions ago
    book = PriceBook(df, "SPY", {c: "USD" for c in df}, None, None, 5)
    u = UniverseConfig(issuers=[Issuer(symbol=s, segment="x", role="core") for s in "ABC"],
                       benchmarks=["SPY"], sectors={"x": "X"})
    gaps = {g["symbol"]: g for g in M.price_gaps(book, u, MC)}
    assert set(gaps) == {"B", "C"}  # A and SPY are current
    assert gaps["B"]["sessions_behind"] == 12 and gaps["B"]["last_price"] == str(
        cal[-13].date())
    assert gaps["C"]["reason"] == "no prices stored" and gaps["C"]["last_price"] is None
