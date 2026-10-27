"""`themegraph monitor refresh`: ingest -> themes -> baskets -> export.

Steps and cadence (docs/MONITOR.md, "Scheduling"):
- prices     daily after the US close; fast
- documents  weekly (transcripts and filings); slow, paid API calls
- themes     when a month-end has passed that the registry has not processed
             (the topic layer works on month-end snapshots of 180-day windows)
- valuation  every run; recomputes each published basket's index from prices
- export     every run whose valuation succeeded

Idempotent: a month-end is processed once; re-running with the same data changes
nothing in the registry and republishes the same content. A failed step is
recorded in data/status.json, later steps that depend on it are skipped, and the
last published release stays current.
Credentials (FMP_API_KEY, VOYAGE_API_KEY, SEC user agent) are read from the
environment of the machine running the job; nothing secret is written to the site.
"""

from __future__ import annotations

import datetime as dt
import traceback
from pathlib import Path

import pandas as pd

from .. import pipeline as P
from ..config import load_pipeline, load_universe
from ..market.returns import price_panel
from .baskets import PriceBook, theme_performance
from .clusters import SnapshotReader
from .config import MonitorConfig, load_monitor
from .coverage import Coverage
from .export import price_date, publish, theme_document, write_status
from .registry import Registry

EVALUATION_NOTE = (
    "The monitor processes data through the current date, which includes the period "
    "2022-01-01..2026-10-31 that the research design reserved as a holdout. From "
    "2026-10-27 that period counts as inspected, not untouched; clean evidence of "
    "the method's value now comes only from the forward prediction log.")


def _cut(as_of: dt.date) -> pd.Timestamp:
    from ..data.pit import snapshot_ts
    return snapshot_ts(as_of)


def _month_ends(start, end) -> list[pd.Timestamp]:
    from ..data.pit import snapshot_ts
    return [snapshot_ts(d) for d in pd.date_range(start, end, freq="ME")]


def load_inputs(fixtures: bool, as_of: dt.date, log=print) -> dict:
    """Data, configuration and universe for one refresh."""
    if fixtures:
        from .. import synthetic as S
        from ..text.embed import HashingEmbedder
        cfg = S.demo_config(load_pipeline())
        u = S.synthetic_universe()
        prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
        cut = _cut(as_of)
        prices = prices.loc[prices["available_at"] <= cut]
        docs = docs.loc[docs["available_at"] <= cut]
        return {"cfg": cfg, "universe": u, "prices": prices, "docs": docs,
                "embedder": HashingEmbedder(cfg.embedding.dimensions), "fixture": True}
    from ..cli import DATA, _store
    from ..ingest import load_documents, load_prices
    from ..text.embed import make_embedder
    cfg, u = load_pipeline(), load_universe()
    store = _store()
    return {"cfg": cfg, "universe": u, "prices": load_prices(store),
            "docs": load_documents(store), "embedder": make_embedder(cfg.embedding),
            "cache": DATA / "cache", "fixture": False}


def ingest_step(kind: str, as_of: dt.date, log=print) -> str:
    from .. import ingest as I
    from ..cli import DATA, _store
    from ..data.fmp import FMPClient
    cfg, u = load_pipeline(), load_universe()
    store = _store()
    client = FMPClient(DATA / "raw" / "fmp")
    if kind == "prices":
        return str(I.ingest_fmp_prices(client, cfg, u, store, as_of))
    out = [str(I.ingest_fmp_transcripts(client, cfg, u, store))]
    from ..data.edgar import EdgarClient
    out.append(str(I.ingest_edgar(EdgarClient(DATA / "raw" / "edgar"), cfg, u, store,
                                  as_of, None)))
    return "; ".join(out)


def price_book(prices: pd.DataFrame, universe, mcfg: MonitorConfig) -> PriceBook:
    panel = price_panel(prices)
    panel.index = pd.DatetimeIndex(panel.index).tz_localize(None).normalize() \
        .tz_localize("UTC") + pd.Timedelta(hours=22)
    venues = {b: "USD" for b in universe.benchmarks}
    venues.update({i.symbol: i.currency for i in universe.issuers})
    return PriceBook(panel, universe.benchmarks[0], venues,
                     {i.symbol: i.delisted for i in universe.issuers if i.delisted},
                     {i.symbol: i.successor for i in universe.issuers if i.successor},
                     mcfg.basket.stale_sessions)


def refresh(state_dir: Path, site_dir: Path, *, fixtures: bool = False,
            as_of: dt.date | None = None, ingest_prices: bool = True,
            ingest_documents: bool = False, now: str | None = None, log=print,
            inputs_hook=None) -> dict:
    as_of = as_of or dt.date.today()
    now = now or (f"{as_of}T23:59:59+00:00" if fixtures else
                  dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))
    state_dir, site_dir = Path(state_dir), Path(site_dir)
    mcfg = load_monitor()
    steps: dict[str, dict] = {}

    def run(name, fn):
        try:
            detail = fn()
            steps[name] = {"ok": True, "at": now, "detail": detail}
            return True
        except Exception as e:  # noqa: BLE001 - every failure is recorded, none hidden
            steps[name] = {"ok": False, "at": now, "error": f"{type(e).__name__}: {e}",
                           "trace": traceback.format_exc(limit=3)}
            log(f"{name} failed: {e}")
            return False

    if not fixtures and ingest_prices:
        run("ingest_prices", lambda: ingest_step("prices", as_of, log))
    if not fixtures and ingest_documents:
        run("ingest_documents", lambda: ingest_step("documents", as_of, log))

    ctxd: dict = {}
    def _load():
        ctxd.update(load_inputs(fixtures, as_of, log))
        if inputs_hook is not None:  # tests: change the universe or data
            ctxd.update(inputs_hook(ctxd, as_of))
        missing = [b for b in mcfg.market.benchmarks
                   if b not in set(ctxd["prices"]["symbol"])]
        if missing:
            raise RuntimeError(f"no prices for benchmarks {missing}: add them to the "
                               "universe's benchmarks so they are ingested")
        return f"{len(ctxd['prices'])} prices, {len(ctxd['docs'])} documents"
    run("load", _load)
    reg = Registry(state_dir)
    themes_ok = "load" in steps and steps["load"]["ok"] and run(
        "themes", lambda: _themes(reg, ctxd, as_of, now, mcfg, state_dir, log))
    book_box: dict = {}
    valued = steps.get("load", {}).get("ok") and run(
        "valuation", lambda: _value(reg, ctxd, mcfg, book_box))
    release = None
    if valued:
        def _export():
            nonlocal release
            meta = {"generated_at": now, "fixture": ctxd["fixture"],
                    "data_as_of": _as_of(ctxd, reg, book_box["book"]),
                    "evaluation_note": EVALUATION_NOTE,
                    "universe": coverage(ctxd, mcfg).summary(now),
                    "benchmarks": mcfg.market.benchmarks,
                    "go_live": reg.data.get("go_live"),
                    "themes_processed_through": reg.data.get("last_snapshot")}
            release = publish(site_dir, book_box["docs"], meta, mcfg)
            return release
        run("export", _export)
    status = _status(site_dir, steps, now, ctxd, reg, book_box, mcfg, themes_ok)
    write_status(site_dir, status)
    return status


def _themes(reg: Registry, d: dict, as_of: dt.date, now: str, mcfg: MonitorConfig,
            state_dir: Path, log) -> str:
    cfg, u = d["cfg"], d["universe"]
    ends = [t for t in _month_ends(cfg.periods.start, as_of)
            if t <= _cut(as_of)]
    last = reg.data.get("last_snapshot")
    todo = [t for t in ends if last is None or t > pd.Timestamp(last)]
    if last is None:
        todo = todo[-mcfg.bootstrap_snapshots:]
    if not todo:
        return "no new month-end to process"
    ctx = P.build(d["prices"], d["docs"], cfg, u, d["embedder"],
                  d.get("cache", state_dir / "cache"), "forward", log=log, end=todo[-1].date())
    if ctx.vectors is None:
        raise RuntimeError("no documents: the topic layer has nothing to read")
    reader = SnapshotReader(ctx, mcfg)
    cov = coverage(d, mcfg)
    roles = {i.symbol: i.role for i in u.issuers}
    counts = []
    for t in todo:
        if t not in set(ctx.snapshots):
            continue
        counts.append({"t": str(t.date()), **reg.process(
            t, reader.clusters(t), mcfg, cov, roles, now, reader.window_by_issuer(t))})
    reg.save()
    return counts


def coverage(d: dict, mcfg: MonitorConfig) -> Coverage:
    docs = d["docs"]
    first = docs.groupby("symbol")["available_at"].min().to_dict() if len(docs) else {}
    return Coverage(d["universe"], first, mcfg.coverage.min_history_months)


def _value(reg: Registry, d: dict, mcfg: MonitorConfig, box: dict) -> str:
    book = price_book(d["prices"], d["universe"], mcfg)
    snaps = reg.data.get("snapshots", [])
    docs = []
    for th in reg.published():
        perf = theme_performance(th, book, mcfg.market.benchmarks, reg.data.get("go_live"))
        docs.append(theme_document(th, perf, snaps, mcfg))
    box.update(book=book, docs=docs)
    return f"{len(docs)} themes valued through {price_date(book)}"


def _as_of(d: dict, reg: Registry, book) -> dict:
    docs = d["docs"]
    return {"prices": price_date(book),
            "documents": str(pd.Timestamp(docs["available_at"].max()).date())
            if len(docs) else None,
            "themes": reg.data.get("last_snapshot")}


def _status(site: Path, steps: dict, now: str, d: dict, reg: Registry, box: dict,
            mcfg: MonitorConfig, themes_ok: bool) -> dict:
    import json
    path = site / "data" / "status.json"
    prev = json.loads(path.read_text()) if path.exists() else {}
    last_ok = prev.get("last_success", {})
    for k, v in steps.items():
        if v["ok"]:
            last_ok[k] = v["at"]
    book = box.get("book")
    stale = {}
    ref = pd.Timestamp(now)
    if book is not None and book.last_date is not None:
        behind = pd.bdate_range(book.last_date.tz_convert(None).normalize()
                                + pd.Timedelta(days=1), ref.tz_convert(None).normalize())
        stale["prices"] = len(behind) > mcfg.freshness.price_stale_sessions
    if len(d.get("docs", [])):
        newest = pd.Timestamp(d["docs"]["available_at"].max())
        stale["documents"] = (ref - newest).days > mcfg.freshness.documents_stale_days
    ok = all(v["ok"] for v in steps.values())
    return {"schema_version": 1, "checked_at": now, "ok": ok,
            "fixture": d.get("fixture"),
            "last_attempt": {k: {kk: vv for kk, vv in v.items() if kk != "trace"}
                             for k, v in steps.items()},
            "errors": {k: v.get("error") for k, v in steps.items() if not v["ok"]},
            "last_success": last_ok,
            "published_release": steps.get("export", {}).get("detail")
            or prev.get("published_release"),
            "data_as_of": _as_of(d, reg, book) if book is not None and "docs" in d
            else prev.get("data_as_of"),
            "stale": stale, "evaluation_note": EVALUATION_NOTE}
