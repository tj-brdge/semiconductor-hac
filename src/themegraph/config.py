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


class Issuer(_Strict):
    symbol: str
    segment: str
    role: Literal["core", "adjacent"]
    currency: str = "USD"
    sec_filer: bool = True
    listed: dt.date | None = None
    delisted: dt.date | None = None
    cik: int | None = None


class UniverseConfig(_Strict):
    benchmarks: list[str]
    issuers: list[Issuer]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_pipeline(path: Path | None = None) -> PipelineConfig:
    path = path or CONFIG_DIR / "pipeline.yaml"
    return PipelineConfig.model_validate(yaml.safe_load(Path(path).read_text()))


def load_universe(path: Path | None = None) -> UniverseConfig:
    path = path or CONFIG_DIR / "universe.yaml"
    cfg = UniverseConfig.model_validate(yaml.safe_load(Path(path).read_text()))
    symbols = [i.symbol for i in cfg.issuers]
    dupes = {s for s in symbols if symbols.count(s) > 1}
    if dupes:
        raise ValueError(f"duplicate symbols in universe: {sorted(dupes)}")
    return cfg
