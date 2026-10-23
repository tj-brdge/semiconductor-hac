"""End-to-end run: layers -> candidate ledger (hashed) -> evaluation -> outputs.

The run works on in-memory inputs so the same code serves real store data and the
synthetic demo. Inputs are clipped to the run's end date by `available_at` before
anything is computed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from tqdm.auto import tqdm

from . import __version__
from .config import CONFIG_DIR, PipelineConfig, UniverseConfig, file_sha256
from .data.pit import snapshot_ts
from .eval.comovement import comovement_test, summarise_percentiles
from .eval.detection import detection_metrics, match_candidates
from .eval.labels import LabelFile
from .eval.returns import returns_test
from .graph.candidates import detect, write_ledger
from .graph.layers import matrix_edges, text_affinity
from .market.hac import market_snapshot
from .market.macro import exposure_similarity, macro_exposures, monthly_returns
from .market.returns import log_returns, price_panel
from .text.cluster import ClusterCache, cluster_subset, cluster_window, window_mask
from .text.embed import Embedder, EmbeddingCache
from .text.keywords import ctfidf_keywords
from .text.novelty import novelty_mask
from .text.passages import drop_exact_duplicates, passages_from_documents, substantive_mask
from .text.track import Tracker, track_events, window_exposure
from .universe.membership import roles, segments, symbols_as_of


def _bar(items, desc: str, total: int | None = None):
    return tqdm(items, desc=desc, unit="snapshot", total=total, disable=None,
                dynamic_ncols=True)


@dataclass
class RunContext:
    cfg: PipelineConfig
    universe: UniverseConfig
    period: str
    end: pd.Timestamp
    snapshots: list[pd.Timestamp]
    rets: pd.DataFrame
    affinity: dict[int, dict[pd.Timestamp, pd.DataFrame | None]] = field(default_factory=dict)
    exposure: pd.DataFrame | None = None
    events: pd.DataFrame | None = None
    topics: pd.DataFrame | None = None
    edges: pd.DataFrame | None = None
    ledger: pd.DataFrame | None = None
    ledger_sha256: str | None = None
    passages: pd.DataFrame | None = None
    track_stats: dict = field(default_factory=dict)
    embedder_id: str = ""
    _aff_cache: dict = field(default_factory=dict)

    def affinity_after(self, t: pd.Timestamp, h: int) -> pd.DataFrame | None:
        """Market affinity estimated over the window ending h sessions after t,
        never using sessions after the run's end date."""
        idx = self.rets.index[self.rets.index <= t]
        if not len(idx):
            return None
        pos = self.rets.index.get_loc(idx[-1]) + h
        if pos >= len(self.rets.index):
            return None
        t1 = self.rets.index[pos]
        if t1 > self.end:
            return None
        key = (t1, h)
        if key not in self._aff_cache:
            w = self.cfg.candidates.market_alignment_window
            snap = market_snapshot(self.rets, t1, w, self.cfg.market,
                                   symbols_as_of(self.universe, t1),
                                   self.cfg.market.residualize_against)
            self._aff_cache[key] = snap["affinity"]
        return self._aff_cache[key]


def period_bounds(cfg: PipelineConfig, period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(cfg.periods.start)
    if period == "dev":
        return start, pd.Timestamp(cfg.periods.dev_end)
    if period == "holdout":
        return pd.Timestamp(cfg.periods.dev_end) + pd.Timedelta(days=1), pd.Timestamp(
            cfg.periods.holdout_end)
    raise ValueError(period)


def build(prices: pd.DataFrame, docs: pd.DataFrame, cfg: PipelineConfig,
          universe: UniverseConfig, embedder: Embedder, cache_dir: Path, period: str,
          factors: pd.DataFrame | None = None, log=print) -> RunContext:
    """Compute every layer from the start date through the end of `period`.
    For the holdout period the history before it is used to warm up windows and
    tracks; candidates are evaluated only inside the period."""
    _, end = period_bounds(cfg, period)
    end_ts = snapshot_ts(end)
    prices = prices.loc[prices["available_at"] <= end_ts]
    docs = docs.loc[docs["available_at"] <= end_ts] if len(docs) else docs
    snaps = [snapshot_ts(d) for d in pd.date_range(cfg.periods.start, end,
                                                   freq=cfg.periods.snapshot_frequency)]
    panel = price_panel(prices)
    panel.index = pd.DatetimeIndex(panel.index).tz_localize(None).normalize()
    rets = log_returns(panel)
    rets.index = rets.index.tz_localize("UTC") + pd.Timedelta(hours=22)
    ctx = RunContext(cfg, universe, period, end_ts, snaps, rets)

    log(f"market layer: {len(snaps)} snapshots, windows {cfg.market.windows}")
    for w in cfg.market.windows:
        ctx.affinity[w] = {}
        for t in _bar(snaps, f"Market layer ({w}d)"):
            snap = market_snapshot(rets, t, w, cfg.market, symbols_as_of(universe, t),
                                   cfg.market.residualize_against)
            ctx.affinity[w][t] = snap["affinity"]
    edges = []
    main_w = cfg.candidates.market_alignment_window
    for t in snaps:
        edges.append(matrix_edges(t, f"market_{main_w}", ctx.affinity.get(main_w, {}).get(t)))

    if factors is not None and not factors.empty:
        log("macro layer")
        mrets = monthly_returns(rets.set_axis(rets.index.tz_localize(None).normalize()))
        for t in _bar(snaps, "Macro layer"):
            tt = t.tz_localize(None).normalize()
            expo = macro_exposures(mrets, factors, cfg.market.benchmark, cfg.macro, tt)
            edges.append(matrix_edges(t, "macro", exposure_similarity(expo)))

    log("text layer")
    ctx.embedder_id = embedder.model_id
    exposures, totals, active, best = [], {}, {}, {}
    if len(docs):
        p, V = prepare_passages(docs, cfg, embedder, cache_dir, log)
        ctx.passages = p
        windows = cluster_windows(p, V, snaps, cfg, embedder.model_id,
                                  cache_dir / "clusters", log)
        tracker = Tracker(cfg.text.track_link_similarity,
                          link_overlap=cfg.text.track_link_overlap)
        for wc in windows:
            t = wc.t
            assign = tracker.step(wc)
            totals[t] = len(wc.labels)
            active[t] = len(np.unique(wc.symbols))
            ex = window_exposure(wc, assign)
            exposures.append(ex)
            for c, tid in assign.items():
                idx = np.flatnonzero(wc.labels == c)
                if len(idx) > best.get(tid, (0,))[0]:
                    best[tid] = (len(idx), t, [wc.passage_ids[i] for i in idx])
            edges.append(matrix_edges(t, "text", text_affinity(ex)))
        ctx.events = track_events(tracker)
        ctx.track_stats = tracker.stats()
        log(f"  topic tracking: {ctx.track_stats}")
    ctx.exposure = (pd.concat(exposures, ignore_index=True) if exposures else
                    pd.DataFrame(columns=["t", "track_id", "symbol", "n_passages", "exposure"]))
    ctx.edges = pd.concat([e for e in edges if len(e)], ignore_index=True) if edges else None

    log("theme candidates")
    first_doc = docs["available_at"].min() if len(docs) else None
    warmup = first_doc + pd.Timedelta(days=cfg.text.window_days) if first_doc is not None \
        else None
    ctx.ledger = detect(ctx.exposure, snaps, cfg.candidates, segments(universe),
                        ctx.affinity.get(main_w, {}), cfg.evaluation.seed,
                        min(cfg.evaluation.null_draws, 200), pd.Series(totals),
                        warmup, pd.Series(active)) if len(ctx.exposure) else pd.DataFrame()
    ctx.topics = topic_labels(ctx, best, cfg.text.keywords_per_topic)
    return ctx


def prepare_passages(docs: pd.DataFrame, cfg: PipelineConfig, embedder: Embedder,
                     cache_dir: Path, log=print) -> tuple[pd.DataFrame, np.ndarray]:
    """Documents -> filtered, embedded, novel passages (shared by runs and diagnostics)."""
    p = passages_from_documents(docs, cfg.text.chunk_words, cfg.text.min_chunk_words)
    if cfg.text.exact_dup_drop:
        p = drop_exact_duplicates(p)
    sub = substantive_mask(p, cfg.text.max_numeric_share, cfg.text.drop_financial_statements)
    p = p.loc[sub].sort_values(["available_at", "passage_id"]).reset_index(drop=True)
    cache = EmbeddingCache(cache_dir / "embeddings", embedder)
    V = cache.get(p["text_hash"].tolist(), p["text"].tolist())
    keep = novelty_mask(p, V, cfg.text.novelty_max_similarity)
    log(f"  {int(keep.sum())} passages ({int((~sub).sum())} tables/boilerplate and "
        f"{int((~keep).sum())} near-duplicates dropped)")
    return p.loc[keep].reset_index(drop=True), V[keep]


def describe_window(p: pd.DataFrame, V: np.ndarray, t: pd.Timestamp, cfg: PipelineConfig,
                    top: int = 12) -> str:
    """Readable summary of one window's clusters, for calibrating cluster_distance by
    reading topics (never by looking at returns)."""
    wc = cluster_window(p, V, t, cfg.text, seed=cfg.evaluation.seed)
    n = len(wc.labels)
    clustered = int((wc.labels >= 0).sum())
    lines = [f"{t.date()}  distance {cfg.text.cluster_distance:.2f}: {len(wc.centroids)} topics,"
             f" {clustered}/{n} passages clustered ({clustered / max(n, 1):.0%})"]
    text = p.set_index("passage_id")["text"]
    sizes = {c: int((wc.labels == c).sum()) for c in wc.centroids}
    biggest = sorted(sizes, key=lambda c: -sizes[c])[:top]
    docs = {c: " ".join(text.loc[[wc.passage_ids[i] for i in
                                  np.flatnonzero(wc.labels == c)[:200]]]) for c in biggest}
    kw = ctfidf_keywords(docs, cfg.text.keywords_per_topic) if docs else {}
    for c in biggest:
        syms = pd.Series(wc.symbols[wc.labels == c]).value_counts()
        lines.append(f"  {sizes[c]:5d} passages, {len(syms):2d} issuers | "
                     f"{', '.join(kw[c][:7])} | {', '.join(syms.index[:6])}")
    return "\n".join(lines)


def cluster_windows(p: pd.DataFrame, V: np.ndarray, snaps: list[pd.Timestamp],
                    cfg: PipelineConfig, embedder_id: str, cache_root: Path, log=print):
    """Cluster every window, reusing cached windows and running the rest in parallel.
    Windows are independent; tracking then walks them in time order."""
    cache = ClusterCache(cache_root, embedder_id, cfg.text, cfg.evaluation.seed)
    ids, syms, genres = (p["passage_id"].to_numpy(), p["symbol"].to_numpy(),
                         p["genre"].to_numpy())
    out, todo = {}, []
    for t in snaps:
        idx = np.flatnonzero(window_mask(p, t, cfg.text.window_days))
        key = cache.key(ids[idx])
        hit = cache.get(key, t, ids[idx], syms[idx])
        if hit is not None:
            out[t] = hit
        else:
            todo.append((t, idx, key))
    log(f"  clustering {len(todo)} windows ({len(snaps) - len(todo)} cached)")
    if todo:
        results = Parallel(n_jobs=cfg.text.parallel_jobs, return_as="generator")(
            delayed(_cluster_idx)(t, ids[idx], syms[idx], genres[idx], V, idx, cfg.text,
                                  cfg.evaluation.seed) for t, idx, _ in todo)
        for (t, _, key), wc in zip(todo, _bar(results, "Text clustering", total=len(todo)),
                                   strict=True):
            cache.put(key, wc)
            out[t] = wc
    return [out[t] for t in snaps]


def _cluster_idx(t, ids, syms, genres, V, idx, cfg, seed):
    return cluster_subset(t, ids, syms, V[idx], cfg, seed, genres)


def topic_labels(ctx: RunContext, best: dict, top_n: int, extra: int = 200,
                 max_passages: int = 200) -> pd.DataFrame:
    """Keywords once per topic, from the window where it was largest. Labelled: every
    topic in the candidate ledger plus the `extra` largest others."""
    if not best or ctx.passages is None:
        return pd.DataFrame(columns=["t", "track_id", "keywords", "n_passages"])
    flagged = set(ctx.ledger["track_id"]) if ctx.ledger is not None and len(ctx.ledger) \
        else set()
    largest = sorted(best, key=lambda k: -best[k][0])[:extra]
    chosen = [tid for tid in best if tid in flagged or tid in set(largest)]
    text = ctx.passages.set_index("passage_id")["text"]
    docs = {tid: " ".join(text.loc[best[tid][2][:max_passages]]) for tid in chosen}
    kw = ctfidf_keywords(docs, top_n)
    return pd.DataFrame([{"t": best[tid][1], "track_id": tid, "keywords": kw[tid],
                          "n_passages": best[tid][0]} for tid in chosen])


def evaluate(ctx: RunContext, labels: LabelFile) -> dict:
    """Q1/Q2 on candidates first flagged inside the run's period."""
    cfg, ev = ctx.cfg, ctx.cfg.evaluation
    start, end = period_bounds(cfg, ctx.period)
    led = ctx.ledger if ctx.ledger is not None else pd.DataFrame()
    if len(led):
        led = led.loc[(led["t"] >= snapshot_ts(start)) & (led["t"] <= snapshot_ts(end))]
    years = (end - start).days / 365.25
    matches = match_candidates(led, labels.for_period(ctx.period),
                               ev.label_match_min_jaccard, ev.label_match_window_days)
    q1 = detection_metrics(matches, led, years)
    firsts = led.loc[led["t"] == led["t_first_flag"]] if len(led) else led
    segs = segments(ctx.universe)
    como = comovement_test(firsts, ctx.affinity_after, ev.forward_sessions, segs,
                           ev.null_draws, ev.seed) if len(firsts) else pd.DataFrame()
    rets = ctx.rets.loc[ctx.rets.index <= ctx.end]
    core = {s for s, r in roles(ctx.universe).items() if r == "core"}
    rt = returns_test(firsts, rets, core, cfg.market.sector_benchmark, ev.forward_sessions,
                      segs, ev.null_draws, ev.seed) if len(firsts) else pd.DataFrame()
    q2 = {
        "comovement": summarise_percentiles(como, ev.null_draws, ev.seed) if len(como) else {},
        "returns": (rt.groupby("h")[["excess_vs_benchmark", "excess_vs_momentum", "null_pct"]]
                    .median().to_dict(orient="index") if len(rt) else {}),
    }
    return {"q1": q1, "q2": q2, "matches": matches, "comovement": como, "returns": rt}


def _jsonable(o):
    if isinstance(o, (pd.Timestamp, dt.date)):
        return o.isoformat()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, float) and np.isnan(o):
        return None
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def save(ctx: RunContext, results: dict | None, out_dir: Path, inputs: dict) -> dict:
    """Write outputs. The ledger is written and hashed first."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx.ledger_sha256 = write_ledger(ctx.ledger, out_dir)
    for name in ("exposure", "events", "topics", "edges"):
        df = getattr(ctx, name)
        if df is not None and len(df):
            df.to_parquet(out_dir / f"{name}.parquet", index=False)
    manifest = {
        "themegraph_version": __version__,
        "python": platform.python_version(),
        "period": ctx.period,
        "end": ctx.end.isoformat(),
        "snapshots": len(ctx.snapshots),
        "embedder": ctx.embedder_id,
        "research_embedder": ctx.embedder_id == ctx.cfg.embedding.model,
        "config_sha256": {p.name: file_sha256(p) for p in sorted(CONFIG_DIR.glob("*.yaml"))},
        "inputs": inputs,
        "ledger_sha256": ctx.ledger_sha256,
        "text_tracking": ctx.track_stats,
    }
    if results is not None:
        for name in ("matches", "comovement", "returns"):
            df = results[name]
            if len(df):
                df.to_parquet(out_dir / f"eval_{name}.parquet", index=False)
        summary = {"q1": results["q1"], "q2": results["q2"]}
        blob = json.dumps(summary, default=_jsonable, sort_keys=True)
        manifest["results_sha256"] = hashlib.sha256(blob.encode()).hexdigest()
        (out_dir / "results.json").write_text(json.dumps(summary, default=_jsonable, indent=1))
    (out_dir / "manifest.json").write_text(json.dumps(manifest, default=_jsonable, indent=1))
    return manifest


def frame_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes()
                          ).hexdigest()
