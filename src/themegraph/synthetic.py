"""Synthetic market + disclosure data with planted themes.

Used by the end-to-end test and `themegraph demo` to check that the pipeline
recovers what was planted (and does not invent signal where none was planted).
Nothing here is used for research results.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Availability, Issuer, PipelineConfig, UniverseConfig
from .data.pit import price_available_at, transcript_available_at
from .eval.labels import LabelFile, Marker, Theme

SEGMENTS = {
    "memory": ("core", "wafer bit density node yield nand dram pricing contract supply "
                       "inventory days fab utilization"),
    "compute": ("core", "processor gpu server design wins architecture software platform "
                        "accelerator roadmap socket"),
    "analog": ("core", "industrial automotive channel distributors lead times catalog "
                       "power management factory loading"),
    "equipment": ("core", "deposition etch lithography tools backlog shipments customers "
                          "installed base services wafer starts"),
    "networking": ("adjacent", "switching optics transceivers bandwidth ethernet routing "
                               "campus enterprise refresh ports"),
    "power": ("adjacent", "grid utilities transmission turbines electrification load growth "
                          "interconnection capacity ratebase"),
}
BOILERPLATE = ("forward looking statements involve risks and uncertainties actual results may "
               "differ materially please refer to our filings with the securities and exchange "
               "commission for a discussion of risk factors")
FILLER = ("quarter revenue margin guidance customers demand operating expenses cash flow "
          "growth year team execution priorities outlook").split()


@dataclass(frozen=True)
class PlantedTheme:
    id: str
    issuers: tuple[str, ...]
    vocab: str
    text_start: dt.date      # disclosures start mentioning it
    market_start: dt.date | None  # co-movement starts (None = never: hype only)
    consensus: dt.date
    period: str
    kind: str = "theme"


THEMES = [
    PlantedTheme("planted_ai_buildout", ("MEM1", "MEM2", "NET1", "NET2", "PWR1"),
                 "accelerated cluster training inference capacity liquid cooling high "
                 "bandwidth memory hyperscale buildout",
                 dt.date(2018, 7, 1), dt.date(2019, 4, 1), dt.date(2019, 12, 31), "dev"),
    PlantedTheme("planted_hype_fade", ("CMP1", "CMP2", "ANA1"),
                 "virtual worlds immersive headset avatars metaverse spatial experiences",
                 dt.date(2019, 1, 1), None, dt.date(2019, 10, 31), "dev", "negative_control"),
    PlantedTheme("planted_power_semis", ("ANA2", "ANA3", "PWR2", "PWR3", "EQP1"),
                 "silicon carbide traction inverter charging wide bandgap substrate "
                 "electric vehicle",
                 dt.date(2020, 9, 1), dt.date(2021, 3, 1), dt.date(2021, 9, 30), "holdout"),
]


def synthetic_universe(per_segment: int = 8) -> UniverseConfig:
    prefix = {"memory": "MEM", "compute": "CMP", "analog": "ANA", "equipment": "EQP",
              "networking": "NET", "power": "PWR"}
    issuers = [Issuer(symbol=f"{prefix[s]}{i}", segment=s, role=SEGMENTS[s][0])
               for s in SEGMENTS for i in range(1, per_segment + 1)]
    return UniverseConfig(benchmarks=["SPY", "SOXX"], issuers=issuers)


def demo_config(cfg: PipelineConfig) -> PipelineConfig:
    """Shorter periods and hashing embeddings so the demo runs in about a minute."""
    return cfg.model_copy(update={
        "periods": cfg.periods.model_copy(update={
            "start": dt.date(2017, 1, 1), "dev_end": dt.date(2020, 6, 30),
            "holdout_end": dt.date(2021, 12, 31)}),
        "embedding": cfg.embedding.model_copy(update={"provider": "hashing"}),
        "text": cfg.text.model_copy(update={"cluster_distance": 0.75, "window_days": 120}),
        "evaluation": cfg.evaluation.model_copy(update={"null_draws": 200}),
    })


def _ramp(dates: pd.DatetimeIndex, start: dt.date | None, days: int = 120) -> np.ndarray:
    if start is None:
        return np.zeros(len(dates))
    x = (dates - pd.Timestamp(start)).days.to_numpy() / days
    return np.clip(x, 0, 1)


def synthetic_prices(universe: UniverseConfig, start="2015-06-01", end="2021-12-31",
                     seed: int = 7, rules: Availability | None = None) -> pd.DataFrame:
    rules = rules or Availability()
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    n = len(dates)
    mkt = rng.normal(0.0003, 0.010, n)
    seg_f = {s: rng.normal(0, 0.007, n) for s in SEGMENTS}
    theme_f = {th.id: rng.normal(0, 0.014, n) * _ramp(dates, th.market_start)
               for th in THEMES}
    rets = {"SPY": mkt}
    core = []
    for iss in universe.issuers:
        r = 1.1 * mkt + seg_f[iss.segment] + rng.normal(0, 0.013, n)
        for th in THEMES:
            if iss.symbol in th.issuers:
                r = r + theme_f[th.id]
        rets[iss.symbol] = r
        if iss.role == "core":
            core.append(r)
    rets["SOXX"] = np.mean(core, axis=0)
    rows = []
    for sym, r in rets.items():
        close = 100 * np.exp(np.cumsum(r))
        rows.append(pd.DataFrame({"symbol": sym, "date": dates, "close": close}))
    df = pd.concat(rows, ignore_index=True)
    df["period"] = df["date"].dt.date.astype(str)
    df["available_at"] = price_available_at(df["date"], rules)
    df["published_at"] = df["available_at"]
    return df


def _words(rng, vocab: str, k: int) -> str:
    v = vocab.split()
    return " ".join(rng.choice(v, k))


def synthetic_documents(universe: UniverseConfig, start="2016-01-01", end="2021-12-31",
                        seed: int = 11, rules: Availability | None = None) -> pd.DataFrame:
    """Quarterly call transcripts: boilerplate, segment talk, and planted-theme talk
    whose share rises after the theme's text_start."""
    rules = rules or Availability()
    rng = np.random.default_rng(seed)
    quarters = pd.period_range(start, end, freq="Q")
    recs = []
    for iss in universe.issuers:
        seg_vocab = SEGMENTS[iss.segment][1]
        lag = int(rng.integers(20, 40))
        for q in quarters:
            date = q.end_time.normalize() + pd.Timedelta(days=lag)
            if date > pd.Timestamp(end):
                continue
            turns = [f"Operator: welcome to the {q} call. {BOILERPLATE}"]
            n_turns = 8
            for k in range(n_turns):
                spk = ["CEO", "CFO", "Analyst"][k % 3]
                theme_bits = []
                for th in THEMES:
                    if iss.symbol not in th.issuers:
                        continue
                    share = _ramp(pd.DatetimeIndex([date]), th.text_start, 360)[0]
                    if th.kind == "negative_control" and date > pd.Timestamp(th.consensus):
                        share *= 0.2  # hype fades after its peak
                    if rng.random() < share:
                        theme_bits.append(_words(rng, th.vocab, 70))
                if theme_bits:
                    body = " ".join(theme_bits) + " " + _words(rng, " ".join(FILLER), 10)
                else:
                    body = _words(rng, seg_vocab, 55) + " " + _words(rng, " ".join(FILLER), 25)
                turns.append(f"{spk}: {body}")
            recs.append({
                "symbol": iss.symbol,
                "doc_id": f"synthetic:transcript:{iss.symbol}:{q}",
                "doc_type": "transcript",
                "period": str(q),
                "published_at": date.tz_localize("UTC"),
                "text": "\n".join(turns),
            })
    df = pd.DataFrame(recs)
    df["available_at"] = transcript_available_at(df["published_at"], rules)
    return df


def synthetic_labels() -> LabelFile:
    return LabelFile(frozen=True, themes=[
        Theme(id=th.id, period=th.period, kind=th.kind, issuers=list(th.issuers),
              emergence_marker=Marker(date=th.text_start, source="synthetic"),
              consensus_marker=Marker(date=th.consensus, source="synthetic"))
        for th in THEMES
    ])
