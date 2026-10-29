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
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

from .. import pipeline as P
from .. import usage
from ..config import file_sha256, load_pipeline, load_universe
from ..market.returns import price_panel
from .baskets import PriceBook, theme_performance
from .clusters import SnapshotReader
from .comovement import trading_together
from .config import MonitorConfig, load_monitor
from .coverage import Coverage
from .export import price_date, publish, theme_document, theme_overview, write_status
from .families import families
from .profiles import DEFAULT, Profile, effective, load_profile
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


def load_inputs(fixtures: bool, as_of: dt.date, log=print,
                profile: Profile | None = None) -> dict:
    """Data, configuration and universe for one refresh of `profile`."""
    profile = profile or load_profile(DEFAULT)
    if fixtures:
        from .. import synthetic as S
        from ..text.embed import HashingEmbedder
        cfg = S.demo_config(load_pipeline(profile.pipeline))
        u = S.synthetic_universe(tech=profile.fixture == "tech",
                                 expansion=profile.fork_from is not None)
        prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
        cut = _cut(as_of)
        prices = prices.loc[prices["available_at"] <= cut]
        docs = docs.loc[docs["available_at"] <= cut]
        return {"cfg": cfg, "universe": u, "prices": prices, "docs": docs,
                "embedder": HashingEmbedder(cfg.embedding.dimensions), "fixture": True}
    from ..cli import DATA, _store
    from ..ingest import load_documents, load_prices
    from ..text.embed import make_embedder
    cfg, u = load_pipeline(profile.pipeline), load_universe(profile.universe)
    store = _store()
    # The store is shared by profiles: read only this universe's companies.
    return {"cfg": cfg, "universe": u, "prices": load_prices(store, universe=u),
            "docs": load_documents(store, universe=u), "embedder": make_embedder(cfg.embedding),
            "cache": DATA / "cache", "fixture": False}


def ingest_step(kind: str, as_of: dt.date, log=print, profile: Profile | None = None) -> str:
    from .. import ingest as I
    from ..cli import DATA, _store
    from ..data.fmp import FMPClient
    profile = profile or load_profile(DEFAULT)
    cfg, u = load_pipeline(profile.pipeline), load_universe(profile.universe)
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
            inputs_hook=None, namer=None, profile: Profile | None = None,
            fork_state: Path | None = None) -> dict:
    """One refresh of `profile` (default: the semiconductor pilot). `fork_state`:
    where the registry to fork from lives, when the profile forks and has no
    registry yet (default: the source profile's state directory)."""
    profile = profile or load_profile(DEFAULT)
    as_of = as_of or dt.date.today()
    now = now or (f"{as_of}T23:59:59+00:00" if fixtures else
                  dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))
    state_dir, site_dir = Path(state_dir), Path(site_dir)
    mcfg = load_monitor(profile.monitor)
    steps: dict[str, dict] = {}
    usage.USAGE.clear()
    started = time.perf_counter()

    def run(name, fn):
        clock = time.perf_counter()
        try:
            detail = fn()
            steps[name] = {"ok": True, "at": now, "detail": detail}
            return True
        except Exception as e:  # noqa: BLE001 - every failure is recorded, none hidden
            steps[name] = {"ok": False, "at": now, "error": f"{type(e).__name__}: {e}",
                           "trace": traceback.format_exc(limit=3)}
            log(f"{name} failed: {e}")
            return False
        finally:
            steps[name]["seconds"] = round(time.perf_counter() - clock, 2)

    if not fixtures and ingest_prices:
        run("ingest_prices", lambda: ingest_step("prices", as_of, log, profile))
    if not fixtures and ingest_documents:
        run("ingest_documents", lambda: ingest_step("documents", as_of, log, profile))

    ctxd: dict = {}
    def _load():
        ctxd.update(load_inputs(fixtures, as_of, log, profile))
        if inputs_hook is not None:  # tests: change the universe or data
            ctxd.update(inputs_hook(ctxd, as_of))
        need = set(mcfg.market.benchmarks) | set(mcfg.market.sector_benchmarks.values())
        missing = sorted(need - set(ctxd["prices"]["symbol"]))
        if missing:
            raise RuntimeError(f"no prices for benchmarks {missing}: add them to the "
                               "universe's benchmarks so they are ingested")
        return f"{len(ctxd['prices'])} prices, {len(ctxd['docs'])} documents"

    # Every stage is required: a release is published only when ingestion (when
    # asked for), loading, theme processing and valuation all succeeded. Otherwise
    # the previous release stays current and status.json says what failed. A stage
    # after a failure is skipped, not run on partial results: a theme-processing
    # failure discards the in-memory registry (it is saved only at the end).
    skipped: dict[str, str] = {}
    warnings: dict[str, str] = {}

    def gate(name, fn):
        bad = [k for k, v in steps.items() if not v["ok"]]
        if bad:
            skipped[name] = f"not run: {', '.join(bad)} failed"
            return False
        return run(name, fn)

    gate("load", _load)
    if profile.fork_from and not (state_dir / "registry.json").exists():
        gate("fork", lambda: fork_registry(
            Path(fork_state) if fork_state else load_profile(profile.fork_from).state,
            state_dir, profile, now))
    reg = Registry(state_dir, id_prefix=profile.id_prefix)
    themes_ok = gate("themes", lambda: _themes(reg, ctxd, as_of, now, mcfg, state_dir, log))
    if themes_ok:  # optional: a failure keeps earlier or automatic labels
        warnings.update(_labels(reg, ctxd, mcfg, now, namer, log))
    book_box: dict = {}
    valued = gate("valuation", lambda: _value(reg, ctxd, mcfg, book_box))
    release = None
    run_info = {"effective": effective(profile), "fork": reg.data.get("fork")}
    if valued:
        def _export():
            nonlocal release
            meta = {"generated_at": now, "fixture": ctxd["fixture"],
                    "data_as_of": _as_of(ctxd, reg, book_box["book"]),
                    "evaluation_note": EVALUATION_NOTE,
                    "universe": coverage(ctxd, mcfg).summary(now),
                    "benchmarks": mcfg.market.benchmarks,
                    "benchmark_rule": {"sector_benchmarks": mcfg.market.sector_benchmarks,
                                       "min_share": mcfg.market.sector_benchmark_min_share},
                    "benchmark_labels": mcfg.market.benchmark_labels,
                    "families": mcfg.families.model_dump(), "tone": mcfg.tone.model_dump(),
                    "go_live": reg.data.get("go_live"),
                    "themes_processed_through": reg.data.get("last_snapshot"), **run_info}
            release = publish(site_dir, book_box["docs"], meta, mcfg,
                              reg.data.get("snapshots", []))
            return release
        run("export", _export)
    else:
        skipped.setdefault("export", "not run: an earlier stage failed")
    status = _status(site_dir, steps, now, ctxd, reg, book_box, mcfg, themes_ok)
    status["skipped"] = skipped
    status["warnings"] = warnings
    status["ok"] = status["ok"] and not skipped
    status.update(run_info)
    status["metrics"] = {"seconds": round(time.perf_counter() - started, 1),
                         "peak_rss_mb": peak_rss_mb(), "usage": usage.snapshot()}
    if "docs" in ctxd and "prices" in ctxd:
        _coverage_report(state_dir, ctxd, now)
    write_status(site_dir, status)
    return status


def peak_rss_mb() -> float | None:
    """Peak resident memory of this process so far (whole process, not per step)."""
    try:
        import resource
    except ImportError:  # Windows
        return None
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)


def fork_registry(src: Path, dst: Path, profile: Profile, now: str) -> str:
    """Start `dst` as a copy of the registry in `src`: theme IDs, basket versions,
    measures and events are kept as they were; the copy records where it came from.
    Afterwards the two registries are independent."""
    src = Path(src)
    reg = src / "registry.json"
    if not reg.exists():
        raise RuntimeError(f"cannot fork: no registry in {src}; run the "
                           f"{profile.fork_from} profile first")
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("registry.json", "vectors.npz", "events.jsonl"):
        if (src / name).exists():
            shutil.copyfile(src / name, dst / name)
    data = json.loads((dst / "registry.json").read_text())
    data["fork"] = {"from_profile": profile.fork_from, "at_snapshot": data.get("last_snapshot"),
                    "source_registry_sha256": file_sha256(reg), "forked_at": now,
                    "inherited_themes": sorted(k for k, t in data["themes"].items()
                                               if t["published"])}
    data["id_prefix"] = profile.id_prefix
    (dst / "registry.json").write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
    return f"forked {len(data['fork']['inherited_themes'])} published themes from " \
        f"{profile.fork_from} at {data['fork']['at_snapshot']}"


def _coverage_report(state_dir: Path, d: dict, now: str) -> None:
    """Per-company document and price coverage of this run (for comparisons)."""
    docs, prices, u = d["docs"], d["prices"], d["universe"]
    rows = []
    for i in u.issuers:
        dd = docs.loc[docs["symbol"] == i.symbol] if len(docs) else docs
        pp = prices.loc[prices["symbol"] == i.symbol] if len(prices) else prices
        types = dd["doc_type"].value_counts().to_dict() if len(dd) else {}
        rows.append({"symbol": i.symbol, "segment": i.segment,
                     "covered_from": str(i.covered_from) if i.covered_from else None,
                     "documents": len(dd), "by_type": {k: int(v) for k, v in types.items()},
                     "first_document": str(pd.Timestamp(dd["available_at"].min()).date())
                     if len(dd) else None,
                     "last_document": str(pd.Timestamp(dd["available_at"].max()).date())
                     if len(dd) else None,
                     "price_sessions": len(pp),
                     "first_price": str(pd.Timestamp(pp["date"].min()).date()) if len(pp) else None,
                     "last_price": str(pd.Timestamp(pp["date"].max()).date()) if len(pp) else None})
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "coverage_report.json").write_text(
        json.dumps({"at": now, "companies": rows}, indent=1))


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
    seen: dict = {}

    def history(t: pd.Timestamp):  # earlier month-ends' clusters, for the newly-covered route
        if t not in seen:
            seen[t] = reader.clusters(t) if t in set(ctx.snapshots) else []
        return seen[t]
    roles = {i.symbol: i.role for i in u.issuers}
    counts = []
    for t in todo:
        if t not in set(ctx.snapshots):
            continue
        counts.append({"t": str(t.date()), **reg.process(
            t, reader.clusters(t), mcfg, cov, roles, now, reader.window_by_issuer(t),
            history=history)})
    reg.save()
    return counts


def _labels(reg: Registry, d: dict, mcfg: MonitorConfig, now: str, namer, log) -> dict:
    """Write readable labels (monitor/naming.py). Never blocks a release: problems
    are returned as warnings and shown in status.json."""
    from . import naming
    out = {}
    if namer is None and not d.get("fixture") and mcfg.labels.writer != "keywords":
        try:
            namer = naming.ClaudeNamer(mcfg.labels.model)
        except Exception as e:  # noqa: BLE001 - no SDK or no credentials
            if mcfg.labels.writer == "claude":
                out["labels"] = f"Claude labels unavailable ({type(e).__name__}: {e})"
    try:
        sectors = coverage(d, mcfg).sector
        c = naming.apply_labels(reg.published(), sectors, namer, now=now,
                                limit=mcfg.labels.max_per_refresh)
        reg.save()
    except Exception as e:  # noqa: BLE001
        return {"labels": f"{type(e).__name__}: {e}"}
    if c["errors"]:
        out["labels"] = f"{len(c['errors'])} labels failed: " + "; ".join(
            f"{k}: {v}" for k, v in list(c["errors"].items())[:3])
    log(f"labels: {({k: v for k, v in c.items() if k != 'errors'})}")
    return out


def coverage(d: dict, mcfg: MonitorConfig) -> Coverage:
    docs = d["docs"]
    first = docs.groupby("symbol")["available_at"].min().to_dict() if len(docs) else {}
    return Coverage(d["universe"], first, mcfg.coverage.min_history_months)


def _value(reg: Registry, d: dict, mcfg: MonitorConfig, box: dict) -> str:
    book = price_book(d["prices"], d["universe"], mcfg)
    reg.annotate_observed(coverage(d, mcfg))  # rows from older registries
    snaps = reg.data.get("snapshots", [])
    docs = []
    perfs = {}
    for th in reg.published():
        sb = (th.get("sector_benchmark") or {}).get("symbol")
        bench = mcfg.market.benchmarks + ([sb] if sb and sb not in mcfg.market.benchmarks else [])
        perfs[th["id"]] = theme_performance(th, book, bench, reg.data.get("go_live"))
    c = mcfg.comovement
    together = trading_together(
        book, d["universe"],
        {k: [x["symbol"] for x in p["constituents"]] for k, p in perfs.items()
         if p.get("status") == "ok"},
        c.window_sessions, c.min_observations_share, c.null_draws, c.index)
    fam = families(reg.published(), reg.vectors, mcfg.families.min_cosine)
    for th in reg.published():
        doc = theme_document(th, perfs[th["id"]], snaps, mcfg)
        doc["family"] = fam.get(th["id"])
        doc["trading_together"] = together.get(th["id"])
        doc["overview"] = theme_overview(doc)
        docs.append(doc)
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
