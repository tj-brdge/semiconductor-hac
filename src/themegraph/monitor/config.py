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
    benchmarks: list[str] = ["SPY", "SOXX"]   # compared with every theme
    trend_threshold_pp: float = 5.0
    # Optional extra comparison per theme, fixed when its first basket is decided:
    # the benchmark of the sector holding at least `sector_benchmark_min_share` of
    # the basket's companies (none if no sector does). Chosen from composition
    # only, never from returns.
    sector_benchmarks: dict[str, str] = {}
    sector_benchmark_min_share: float = 0.5
    # What each benchmark represents, shown next to it on the dashboard.
    benchmark_labels: dict[str, str] = {}


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


class Tone(_S):
    """Headwind/tailwind direction from business language (monitor/tone.py).
    Declared 2026-10-29 from the text alone, before any return was looked at."""
    min_passages: int = 20     # company passages needed to judge a theme's tone
    min_share: float = 0.2     # share of its company passages that are negative
    min_ratio: float = 2.0     # times the genre-matched rate in the same window


class Families(_S):
    """Display grouping of overlapping themes (monitor/families.py)."""
    # Same bar as identity matching after a gap (match.min_centroid_cosine): themes
    # this close in meaning would be treated as one theme if they were not both
    # present at once.
    min_cosine: float = 0.85


class Comovement(_S):
    """Trading together (monitor/comovement.py): single-index shrinkage on `index`."""
    window_sessions: int = 126
    min_observations_share: float = 0.9
    null_draws: int = 1000
    index: str = "SPY"


class MonitorConfig(_S):
    bootstrap_snapshots: int = 120
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
    comovement: Comovement = Comovement()
    tone: Tone = Tone()
    families: Families = Families()


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) \
            else v
    return out


def monitor_raw(path: Path) -> dict:
    """A monitor file with `extends: <base file>` resolved by a deep merge."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    base = raw.pop("extends", None)
    return _merge(monitor_raw(Path(path).parent / base), raw) if base else raw


def monitor_sources(path: Path) -> list[Path]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    base = raw.get("extends")
    return (monitor_sources(Path(path).parent / base) if base else []) + [Path(path).resolve()]


def load_monitor(path: Path | None = None) -> MonitorConfig:
    path = path or CONFIG_DIR / "monitor.yaml"
    return MonitorConfig.model_validate(monitor_raw(Path(path)))
