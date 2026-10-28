"""Typed configuration loaded from config/*.yaml."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Periods(_Strict):
    start: dt.date
    dev_end: dt.date
    holdout_end: dt.date
    snapshot_frequency: str = "ME"


class Availability(_Strict):
    price_lag_days: int = 0
    transcript_lag_days: int = 2
    filing_lag_days: int = 0
    macro_release_lag_months: int = 2


class Market(_Strict):
    benchmark: str = "SPY"
    sector_benchmark: str = "SOXX"
    residualize_against: list[str]
    windows: list[int]
    linkage: Literal["average", "complete", "single"] = "average"
    min_observations_share: float = 0.9
    # How "trading together" is estimated (DESIGN.md, "Market layer v2"):
    # - single_index: simple daily returns linked geometrically onto the benchmark
    #   calendar; covariance shrunk toward a single-index model on `index`
    #   (Ledoit & Wolf 2003); correlation of what remains beyond the index.
    # - residual_lw (v1, kept to reproduce earlier results): log returns, OLS
    #   residuals on `residualize_against`, Ledoit-Wolf shrinkage to the identity.
    estimator: Literal["single_index", "residual_lw"] = "residual_lw"
    index: str = "SPY"


class Macro(_Strict):
    factors: list[str]
    window_months: int = 36
    ridge_alpha: float = 1.0
    min_months: int = 30


class Text(_Strict):
    window_days: int = 180
    chunk_words: int = 350
    min_chunk_words: int = 40
    exact_dup_drop: bool = True
    novelty_max_similarity: float = 0.95
    max_numeric_share: float = 0.35
    drop_financial_statements: bool = True
    drop_corporate_housekeeping: bool = True
    center_by_genre: bool = True
    remove_top_components: int = 0
    cluster_distance: float = 0.6
    min_cluster_passages: int = 4
    min_cluster_issuers: int = 2
    track_link_similarity: float = 0.8
    track_link_overlap: float = 0.3
    track_link_min_similarity: float = 0.5
    keywords_per_topic: int = 8
    parallel_jobs: int = 4
    # window = re-cluster every window and link clusters into tracks; anchored =
    # discover topics on a schedule, assign passages to them, find new topics in
    # what no topic claims (text/anchored.py).
    topic_mode: Literal["window", "anchored"] = "window"
    anchor_refit_snapshots: int = 12
    anchor_lookback_days: int = 730
    anchor_assign_similarity: float = 0.45
    anchor_match_similarity: float = 0.70
    anchor_lineage_share: float = 0.5


class Embedding(_Strict):
    provider: Literal["voyage", "hashing"] = "voyage"
    endpoint: Literal["voyage", "mongodb"] = "voyage"
    model: str = "voyage-finance-2"
    batch_size: int = 64
    concurrency: int = 4
    dimensions: int = 1024


class Candidates(_Strict):
    min_consecutive_growth_windows: int = 2
    growth_step_snapshots: int = 3
    min_growth_ratio: float = 1.25
    new_high_lookback_snapshots: int = 24
    max_issuer_share: float = 0.5
    min_issuers: int = 3
    min_issuer_growth: int = 1
    min_exposure: float = 0.05
    market_alignment_window: int = 63
    market_alignment_baseline_snapshots: int = 6


class Evaluation(_Strict):
    label_match_min_jaccard: float = 0.25
    label_match_window_days: int = 730
    forward_sessions: list[int]
    null_draws: int = 500
    seed: int = 0
    primary_metrics: list[str]


class PipelineConfig(_Strict):
    periods: Periods
    availability: Availability
    market: Market
    macro: Macro
    text: Text
    embedding: Embedding
    candidates: Candidates
    evaluation: Evaluation


class Successor(_Strict):
    """What one share became when the issuer was acquired: `shares` of `symbol` plus
    `cash` (in the issuer's currency). `source`: the filing stating the terms."""
    symbol: str
    shares: float = 0.0
    cash: float = 0.0
    source: str


class Issuer(_Strict):
    symbol: str
    segment: str
    role: Literal["core", "adjacent"]
    currency: str = "USD"
    sec_filer: bool = True
    listed: dt.date | None = None
    delisted: dt.date | None = None
    successor: Successor | None = None
    cik: int | None = None
    # Monitored coverage (docs/MONITOR.md, "Universe changes"): when the company
    # was added to, or removed from, the monitored universe. None = since the start.
    # Distinct from listed/delisted, which bound when the stock traded.
    covered_from: dt.date | None = None
    covered_until: dt.date | None = None


class UniverseChange(_Strict):
    version: str
    date: dt.date
    added: list[str] = []
    removed: list[str] = []
    note: str = ""


class UniverseConfig(_Strict):
    name: str = "Unnamed universe"
    description: str = ""
    version: str = "0"
    benchmarks: list[str]
    sectors: dict[str, str] = {}   # segment -> sector shown on the dashboard
    changes: list[UniverseChange] = []
    issuers: list[Issuer]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_pipeline(path: Path | None = None) -> PipelineConfig:
    path = path or CONFIG_DIR / "pipeline.yaml"
    return PipelineConfig.model_validate(yaml.safe_load(Path(path).read_text()))


def universe_sources(path: Path) -> list[Path]:
    """The universe file and every file it extends (base first)."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    base = raw.get("extends")
    return (universe_sources(path.parent / base) if base else []) + [path.resolve()]


def _universe_raw(path: Path) -> dict:
    """A universe file, with `extends: <base file>` resolved: the base's issuers,
    sectors and change log come first and this file adds to them; other fields
    (name, description, version, benchmarks) are this file's own. A larger universe
    can so build on a smaller one without copying it."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    base = raw.pop("extends", None)
    if not base:
        return raw
    out = _universe_raw(Path(path).parent / base)
    for k, v in raw.items():
        if k == "issuers":
            out["issuers"] = out.get("issuers", []) + v
        elif k == "changes":
            out["changes"] = out.get("changes", []) + v
        elif k == "sectors":
            out["sectors"] = {**out.get("sectors", {}), **v}
        else:
            out[k] = v
    return out


def load_universe(path: Path | None = None) -> UniverseConfig:
    path = path or CONFIG_DIR / "universe.yaml"
    cfg = UniverseConfig.model_validate(_universe_raw(Path(path)))
    symbols = [i.symbol for i in cfg.issuers]
    dupes = {s for s in symbols if symbols.count(s) > 1}
    if dupes:
        raise ValueError(f"duplicate symbols in universe: {sorted(dupes)}")
    check_universe(cfg)
    return cfg


def check_universe(cfg: UniverseConfig) -> None:
    """Sectors cover every segment, and the change log agrees with the coverage
    dates on the issuers (the issuers are the source of truth)."""
    if cfg.sectors:
        missing = sorted({i.segment for i in cfg.issuers} - set(cfg.sectors))
        if missing:
            raise ValueError(f"segments without a sector: {missing}")
    by = {i.symbol: i for i in cfg.issuers}
    for c in cfg.changes:
        for s in c.added:
            if s not in by or by[s].covered_from != c.date:
                raise ValueError(f"universe change {c.version}: {s} must be an issuer with "
                                 f"covered_from: {c.date}")
        for s in c.removed:
            if s not in by or by[s].covered_until != c.date:
                raise ValueError(f"universe change {c.version}: {s} must keep its entry "
                                 f"with covered_until: {c.date}")
    if cfg.changes and cfg.changes[-1].version != cfg.version:
        raise ValueError("universe version must equal the latest change's version")
