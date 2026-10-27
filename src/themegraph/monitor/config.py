"""Monitor settings (config/monitor.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from ..config import CONFIG_DIR


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pool(_S):
    min_issuers: int = 3
    max_issuer_share: float = 0.5
    prune_after_snapshots: int = 6


class Match(_S):
    min_jaccard: float = 0.15
    min_centroid_cosine: float = 0.85
    lookback_snapshots: int = 6


class Established(_S):
    present: int = 6
    of_last: int = 9
    min_issuers: int = 4
    min_coherence_quantile: float = 0.5


class Status(_S):
    inactive_after_snapshots: int = 3
    compare_snapshots: int = 3
    strengthening_ratio: float = 1.25
    cooling_ratio: float = 0.8


class Basket(_S):
    min_exposure: float = 0.05
    version_confirm_snapshots: int = 2
    stale_sessions: int = 5


class Market(_S):
    benchmarks: list[str] = ["SPY", "SOXX"]
    trend_threshold_pp: float = 5.0


class CoverageRules(_S):
    min_history_months: int = 12   # document history before a company counts in growth
    min_growth_ratio: float = 1.25  # emergence route: comparable growth over 3 months


class Evidence(_S):
    quotes: int = 5


class Freshness(_S):
    price_stale_sessions: int = 3
    documents_stale_days: int = 21


class Labels(_S):
    # auto: Claude when the `anthropic` package and credentials are available on the
    # machine running the refresh, otherwise automatic keyword labels.
    writer: Literal["auto", "claude", "keywords"] = "auto"
    model: str = "claude-opus-5-5"
    max_per_refresh: int = 40


class MonitorConfig(_S):
    bootstrap_snapshots: int = 24
    live_window_days: int = 35
    pool: Pool = Pool()
    match: Match = Match()
    established: Established = Established()
    status: Status = Status()
    basket: Basket = Basket()
    market: Market = Market()
    coverage: CoverageRules = CoverageRules()
    evidence: Evidence = Evidence()
    freshness: Freshness = Freshness()
    labels: Labels = Labels()


def load_monitor(path: Path | None = None) -> MonitorConfig:
    path = path or CONFIG_DIR / "monitor.yaml"
    return MonitorConfig.model_validate(yaml.safe_load(Path(path).read_text()) or {})
