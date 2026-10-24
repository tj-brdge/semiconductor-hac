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
    closes: pd.DataFrame | None = None       # sessions x symbols, for outcome returns
    rets_known: pd.DataFrame | None = None   # when each return became available
    affinity: dict[int, dict[pd.Timestamp, pd.DataFrame | None]] = field(default_factory=dict)
    exposure: pd.DataFrame | None = None
    events: pd.DataFrame | None = None
    topics: pd.DataFrame | None = None
    edges: pd.DataFrame | None = None
    ledger: pd.DataFrame | None = None
    ledger_sha256: str | None = None
    passages: pd.DataFrame | None = None
    track_stats: dict = field(default_factory=dict)
    windows: pd.DataFrame | None = None
    assignments: pd.DataFrame | None = None
    explanations: pd.DataFrame | None = None
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
                                   self.cfg.market.residualize_against, known=self.rets_known)
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
          factors: pd.DataFrame | None = None, log=print,
          end: dt.date | None = None) -> RunContext:
    """Compute every layer from the start date through the end of `period`.
    For the holdout period the history before it is used to warm up windows and
    tracks; candidates are evaluated only inside the period."""
    if end is None:
        _, end = period_bounds(cfg, period)
    end_ts = snapshot_ts(end)
    prices = prices.loc[prices["available_at"] <= end_ts]
    docs = docs.loc[docs["available_at"] <= end_ts] if len(docs) else docs
    snaps = [snapshot_ts(d) for d in pd.date_range(cfg.periods.start, end,
                                                   freq=cfg.periods.snapshot_frequency)]
    if not snaps or snaps[-1] < end_ts:  # an as-of run also snapshots its cutoff day
        snaps.append(end_ts)
    panel = price_panel(prices)
    panel.index = pd.DatetimeIndex(panel.index).tz_localize(None).normalize()
    rets = log_returns(panel)
    rets.index = rets.index.tz_localize("UTC") + pd.Timedelta(hours=22)
    # When each return became known: the later of its two closes' availability.
    known = prices.pivot_table(index="date", columns="symbol", values="available_at",
                               aggfunc="max")
    known.index = pd.DatetimeIndex(known.index).tz_localize(None).normalize()
    known = known.reindex(index=panel.index, columns=panel.columns)
    known = known.apply(lambda c: pd.to_datetime(c, utc=True))
    rets_known = np.maximum(known, known.shift(1)).iloc[1:]
    rets_known.index = rets.index
    closes = panel.copy()
    closes.index = closes.index.tz_localize("UTC") + pd.Timedelta(hours=22)
    ctx = RunContext(cfg, universe, period, end_ts, snaps, rets, closes=closes,
                     rets_known=rets_known)

    log(f"market layer: {len(snaps)} snapshots, windows {cfg.market.windows}")
    for w in cfg.market.windows:
        ctx.affinity[w] = {}
        for t in _bar(snaps, f"Market layer ({w}d)"):
            snap = market_snapshot(rets, t, w, cfg.market, symbols_as_of(universe, t),
                                   cfg.market.residualize_against, known=rets_known)
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
    exposures, totals, active, best, assigned, win_rows = [], {}, {}, {}, [], []
    if len(docs):
        p, V = prepare_passages(docs, cfg, embedder, cache_dir, log)
        ctx.passages = p
        windows = cluster_windows(p, V, snaps, cfg, embedder.model_id,
                                  cache_dir / "clusters", log)
        tracker = Tracker(cfg.text.track_link_similarity,
                          link_overlap=cfg.text.track_link_overlap,
                          link_min_similarity=cfg.text.track_link_min_similarity)
        for wc in windows:
            t = wc.t
            assign = tracker.step(wc)
            totals[t] = len(wc.labels)
            active[t] = len(np.unique(wc.symbols))
            win_rows.append({"t": t, "n_passages": len(wc.labels),
                             "n_clustered": int((wc.labels >= 0).sum()),
                             "n_excluded_degenerate": wc.excluded,
                             "n_issuers_with_text": active[t], "n_topics": len(assign)})
            ex = window_exposure(wc, assign)
            exposures.append(ex)
            for c, tid in assign.items():
                idx = np.flatnonzero(wc.labels == c)
                assigned.append(pd.DataFrame({"t": t, "track_id": tid,
                                              "passage_id": [wc.passage_ids[i] for i in idx]}))
                if len(idx) > best.get(tid, (0,))[0]:
                    best[tid] = (len(idx), t, [wc.passage_ids[i] for i in idx])
            edges.append(matrix_edges(t, "text", text_affinity(ex)))
        ctx.events = track_events(tracker)
        step = (snaps[1] - snaps[0]).days if len(snaps) > 1 else cfg.text.window_days
        ctx.track_stats = {**tracker.stats(int(np.ceil(cfg.text.window_days / max(step, 1)))),
                           "passages_excluded_degenerate": int(sum(w.excluded for w in windows))}
        log(f"  topic tracking: {ctx.track_stats}")
    ctx.exposure = (pd.concat(exposures, ignore_index=True) if exposures else
                    pd.DataFrame(columns=["t", "track_id", "symbol", "n_passages", "exposure"]))
    usable = [e for e in edges if len(e)]
    ctx.edges = pd.concat(usable, ignore_index=True) if usable else \
        pd.DataFrame(columns=["t", "layer", "a", "b", "weight"])

    log("theme candidates")
    first_doc = docs["available_at"].min() if len(docs) else None
    warmup = first_doc + pd.Timedelta(days=cfg.text.window_days) if first_doc is not None \
        else None
    ctx.ledger = detect(ctx.exposure, snaps, cfg.candidates, segments(universe),
                        ctx.affinity.get(main_w, {}), cfg.evaluation.seed,
                        min(cfg.evaluation.null_draws, 200), pd.Series(totals),
                        warmup, pd.Series(active), split_parents(ctx.events)) \
        if len(ctx.exposure) else pd.DataFrame()
    ctx.windows = pd.DataFrame(win_rows)
    ctx.assignments = (pd.concat(assigned, ignore_index=True) if assigned else
                       pd.DataFrame(columns=["t", "track_id", "passage_id"]))
    ctx.topics = topic_labels(ctx, best, cfg.text.keywords_per_topic)
    ctx.explanations = asof_explanations(ctx, V if len(docs) else None, cfg)
    return ctx


def prepare_passages(docs: pd.DataFrame, cfg: PipelineConfig, embedder: Embedder,
                     cache_dir: Path, log=print) -> tuple[pd.DataFrame, np.ndarray]:
    """Documents -> filtered, embedded, novel passages (shared by runs and diagnostics)."""
    p = passages_from_documents(docs, cfg.text.chunk_words, cfg.text.min_chunk_words)
    if cfg.text.exact_dup_drop:
        p = drop_exact_duplicates(p)
    sub = substantive_mask(p, cfg.text.max_numeric_share, cfg.text.drop_financial_statements,
                           cfg.text.drop_corporate_housekeeping)
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
        key = cache.key(ids[idx], syms[idx], genres[idx], V[idx])
        hit = cache.get(key, t, ids[idx], syms[idx], V.shape[1])
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
    """RETROSPECTIVE labels: keywords once per topic, from the window where it was
    largest over the whole run, which can be after a candidate's flag. Display
    metadata only (never used by detection); historical explanations of a flag use
    `asof_explanations`. Labelled: every topic in the candidate ledger plus the
    `extra` largest others."""
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
                          "n_passages": best[tid][0], "basis": "retrospective_peak_window"}
                         for tid in chosen])


QUOTE_CHARS = 500


def asof_explanations(ctx: RunContext, V: np.ndarray | None, cfg: PipelineConfig,
                      n_quotes: int = 3, background: int = 400) -> pd.DataFrame:
    """Why each candidate was flagged, using only what was available at its first
    flag: keywords contrasting the topic's passages in that window with the rest of
    the window, and the passages closest to the topic's centre as quotations (with
    issuer, document, genre, speaker/section and availability time)."""
    cols = ["candidate_id", "track_id", "t", "keywords", "quotes"]
    led, p = ctx.ledger, ctx.passages
    if V is None or led is None or not len(led) or p is None or not len(ctx.assignments):
        return pd.DataFrame(columns=cols)
    pos = pd.Series(np.arange(len(p)), index=p["passage_id"])
    A = ctx.assignments
    rows = []
    for r in led.loc[led["t"] == led["t_first_flag"]].itertuples():
        members = A.loc[(A["t"] == r.t) & (A["track_id"] == r.track_id), "passage_id"]
        mi = pos.loc[members].to_numpy()
        win = np.flatnonzero(window_mask(p, r.t, cfg.text.window_days))
        bg = np.setdiff1d(win, mi)[:background]
        kw = ctfidf_keywords({0: " ".join(p["text"].to_numpy()[mi[:200]]),
                              1: " ".join(p["text"].to_numpy()[bg])},
                             cfg.text.keywords_per_topic)[0] if len(mi) else []
        cent = V[mi].mean(axis=0)
        order = mi[np.argsort(-(V[mi] @ cent), kind="stable")]
        # Closest passages to the topic centre, one per issuer first, then any.
        picked, seen = [], set()
        for i in order:
            if p["symbol"].iat[i] not in seen:
                picked.append(i)
                seen.add(p["symbol"].iat[i])
        picked += [i for i in order if i not in set(picked)]
        quotes = []
        for i in picked[:n_quotes]:
            row = p.iloc[i]
            quotes.append({"passage_id": row["passage_id"], "symbol": row["symbol"],
                           "doc_id": row["doc_id"], "genre": row.get("genre"),
                           "context": row.get("unit"), "url": row.get("url"),
                           "available_at": pd.Timestamp(row["available_at"]).isoformat(),
                           "text": row["text"][:QUOTE_CHARS]})
        rows.append({"candidate_id": r.candidate_id, "track_id": int(r.track_id), "t": r.t,
                     "keywords": kw, "quotes": quotes})
    return pd.DataFrame(rows, columns=cols)


def split_parents(events: pd.DataFrame | None) -> dict[int, int]:
    """track -> the track it split from."""
    if events is None or not len(events):
        return {}
    sp = events.loc[(events["event"] == "split") & events["other"].notna()]
    return {int(a): int(b) for a, b in zip(sp["track_id"], sp["other"], strict=True)}


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
    matches = _mark_text_evidence(matches, labels.for_period(ctx.period), ctx,
                                  ev.label_match_window_days,
                                  cfg.text.min_cluster_passages * cfg.candidates.min_issuers)
    q1 = detection_metrics(matches, led, years)
    firsts = led.loc[led["t"] == led["t_first_flag"]] if len(led) else led
    segs = segments(ctx.universe)
    como = comovement_test(firsts, ctx.affinity_after, ev.forward_sessions, segs,
                           ev.null_draws, ev.seed) if len(firsts) else pd.DataFrame()
    core = {s for s, r in roles(ctx.universe).items() if r == "core"}
    delisted = {i.symbol: i.delisted for i in ctx.universe.issuers if i.delisted}
    rt = returns_test(firsts, ctx.closes, core, cfg.market.sector_benchmark,
                      ev.forward_sessions, segs, ev.null_draws, ev.seed, ctx.end,
                      delisted) if len(firsts) else pd.DataFrame()
    q2 = {
        "comovement": summarise_percentiles(como, ev.null_draws, ev.seed) if len(como) else {},
        "returns": returns_summary(rt, firsts, ev.null_draws, ev.seed),
    }
    return {"q1": q1, "q2": q2, "matches": matches, "comovement": como, "returns": rt}


def _mark_text_evidence(matches: pd.DataFrame, themes, ctx: RunContext, window_days: int,
                        min_passages: int) -> pd.DataFrame:
    """A labelled theme can only be missed if its issuers had text to detect it from.
    Themes whose issuers had fewer than `min_passages` passages in the matching window
    are marked not evaluable (reason no_text_evidence) instead of counting as misses."""
    if not len(matches):
        return matches
    m = matches.copy()
    m["evidence_passages"] = np.nan
    m["reason"] = np.where(m["evaluable"], "", "no_consensus_date")
    p = ctx.passages
    by_id = {th.id: th for th in themes}
    for i, row in m.iterrows():
        if not row["evaluable"]:
            continue
        th = by_id[row["theme"]]
        c = pd.Timestamp(th.consensus_marker.date, tz="UTC")
        n = 0 if p is None or not len(p) else int(
            (p["symbol"].isin(th.issuers) & (p["available_at"] <= c)
             & (p["available_at"] > c - pd.Timedelta(days=window_days))).sum())
        m.at[i, "evidence_passages"] = n
        if n < min_passages and not row["detected"]:
            m.at[i, "evaluable"] = False
            m.at[i, "reason"] = "no_text_evidence"
    return m


def returns_summary(rt: pd.DataFrame, firsts: pd.DataFrame, draws: int, seed: int) -> dict:
    """Per horizon: counts by status, medians of usable episodes, and the grouped
    null-percentile summary (episodes sharing issuers over overlapping intervals are
    pooled before the permutation test)."""
    if not len(rt):
        return {}
    issuers = dict(zip(firsts["candidate_id"], firsts["issuers"], strict=False))
    ok = rt.loc[rt["status"] == "ok"].assign(issuers=lambda d: d["candidate_id"].map(issuers))
    grouped = summarise_percentiles(ok, draws, seed) if len(ok) else {}
    out = {}
    for h, g in rt.groupby("h"):
        okh = g.loc[g["status"] == "ok"]
        out[int(h)] = {
            "status_counts": g["status"].value_counts().to_dict(),
            "median_excess_vs_benchmark": float(okh["excess_vs_benchmark"].median())
            if len(okh) else None,
            "median_excess_vs_momentum": float(okh["excess_vs_momentum"].median())
            if len(okh) else None,
            **grouped.get(int(h), {}),
        }
    return out


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
    for name in ("exposure", "events", "topics", "edges", "windows", "assignments"):
        df = getattr(ctx, name)
        if df is not None and len(df):
            df.to_parquet(out_dir / f"{name}.parquet", index=False)
    if ctx.explanations is not None and len(ctx.explanations):
        ctx.explanations.assign(t=ctx.explanations["t"].astype(str)).to_json(
            out_dir / "explanations.json", orient="records", indent=1)
    if ctx.passages is not None and len(ctx.passages):
        meta = [c for c in ("passage_id", "doc_id", "symbol", "doc_type", "genre", "unit",
                            "published_at", "available_at", "text_hash", "url")
                if c in ctx.passages]
        ctx.passages[meta].to_parquet(out_dir / "passages.parquet", index=False)
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


def output_bindings(ctx: RunContext, results: dict, manifest: dict) -> dict:
    """Hashes of everything a holdout result consists of, for verification."""
    out = {"ledger_sha256": manifest["ledger_sha256"],
           "results_sha256": manifest.get("results_sha256")}
    for name in ("matches", "comovement", "returns"):
        df = results.get(name)
        out[f"{name}_sha256"] = frame_sha256(df.astype(str)) if df is not None and len(df) \
            else None
    return out


def frame_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes()
                          ).hexdigest()
