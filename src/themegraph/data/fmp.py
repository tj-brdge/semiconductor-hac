"""Financial Modeling Prep client (stable API) and normalisers.

Endpoint names follow FMP's /stable documentation; verify them against the live
API at first use (M1), since they could not be exercised from the build environment.
The API key is read from FMP_API_KEY and never written to the cache, logs or errors.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pandas as pd

from ..config import Availability
from .cache import RawCache
from .http import CachedFetcher
from .pit import macro_available_at, price_available_at, transcript_available_at

BASE = "https://financialmodelingprep.com/stable"

# Macro factor -> (kind, FMP identifier). Verify identifiers at M1.
MACRO_SERIES = {
    "us2y": ("treasury", "year2"),
    "us10y": ("treasury", "year10"),
    "cpi": ("economic", "CPI"),
    "wti": ("price", "CLUSD"),
    "dxy": ("price", "DXUSD"),
}
FX_PAIRS = {"KRW": "USDKRW", "TWD": "USDTWD", "JPY": "USDJPY"}


class FMPClient:
    def __init__(self, cache_dir: Path, api_key: str | None = None, offline: bool = False,
                 fetcher: CachedFetcher | None = None):
        self.api_key = api_key or os.environ.get("FMP_API_KEY")
        if not self.api_key and not offline and fetcher is None:
            raise RuntimeError("FMP_API_KEY is not set (add it as an environment secret)")
        self.f = fetcher or CachedFetcher("fmp", RawCache(cache_dir), min_interval=0.2,
                                          offline=offline)

    def _get(self, endpoint: str, **params) -> list[dict]:
        p = {k: str(v) for k, v in params.items() if v is not None}
        if self.api_key:
            p["apikey"] = self.api_key
        resp = self.f.fetch(f"{BASE}/{endpoint}", params=p, endpoint=endpoint)
        data = json.loads(resp.body or b"[]")
        if isinstance(data, dict) and "Error Message" in data:
            raise RuntimeError(f"FMP {endpoint}: {data['Error Message']}")
        return data if isinstance(data, list) else [data]

    # --- prices, chunked by calendar year so no response hits a row cap
    def prices(self, symbol: str, start: dt.date, end: dt.date) -> list[dict]:
        rows: list[dict] = []
        for year in range(start.year, end.year + 1):
            lo = max(start, dt.date(year, 1, 1))
            hi = min(end, dt.date(year, 12, 31))
            rows += self._get("historical-price-eod/dividend-adjusted",
                              symbol=symbol, **{"from": lo, "to": hi})
        return rows

    def treasury(self, start: dt.date, end: dt.date) -> list[dict]:
        rows, lo = [], start
        while lo <= end:  # the endpoint serves at most ~90 days per request
            hi = min(end, lo + dt.timedelta(days=89))
            rows += self._get("treasury-rates", **{"from": lo, "to": hi})
            lo = hi + dt.timedelta(days=1)
        return rows

    def economic(self, name: str, start: dt.date, end: dt.date) -> list[dict]:
        rows: list[dict] = []
        for year in range(start.year, end.year + 1):
            rows += self._get("economic-indicators", name=name,
                              **{"from": dt.date(year, 1, 1), "to": dt.date(year, 12, 31)})
        return rows

    def transcript_dates(self, symbol: str) -> list[dict]:
        return self._get("earning-call-transcript-dates", symbol=symbol)

    def transcript(self, symbol: str, year: int, quarter: int) -> list[dict]:
        return self._get("earning-call-transcript", symbol=symbol, year=year, quarter=quarter)

    def profile(self, symbol: str) -> list[dict]:
        return self._get("profile", symbol=symbol)

    def delisted(self, page: int = 0) -> list[dict]:
        return self._get("delisted-companies", page=page, limit=100)


# ---------------------------------------------------------------- normalisers

def normalise_prices(rows: list[dict], symbol: str, rules: Availability) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["symbol", "date", "close", "period", "published_at",
                                     "available_at"])
    close_col = "adjClose" if "adjClose" in df else "close"
    out = pd.DataFrame({
        "symbol": symbol,
        "date": pd.to_datetime(df["date"]).dt.normalize(),
        "close": pd.to_numeric(df[close_col], errors="coerce"),
    }).dropna().drop_duplicates("date").sort_values("date")
    out["period"] = out["date"].dt.date.astype(str)
    out["available_at"] = price_available_at(out["date"], rules)
    out["published_at"] = out["available_at"]
    return out.reset_index(drop=True)


def to_usd(prices: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    """Divide local-currency closes by USDxxx (local units per USD), aligned on the
    most recent FX close no more than 7 calendar days old; otherwise missing."""
    p = prices.sort_values("date")
    f = fx[["date", "close"]].rename(columns={"close": "fx"}).sort_values("date")
    m = pd.merge_asof(p, f, on="date", direction="backward", tolerance=pd.Timedelta(days=7))
    m["close"] = m["close"] / m["fx"]
    return m.drop(columns="fx").dropna(subset=["close"])


def normalise_treasury(rows: list[dict], column: str, rules: Availability) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.normalize(),
        "value": pd.to_numeric(df[column], errors="coerce"),
    }).dropna().drop_duplicates("date").sort_values("date")
    out["period"] = out["date"].dt.date.astype(str)
    out["available_at"] = price_available_at(out["date"], rules)
    out["published_at"] = out["available_at"]
    return out.reset_index(drop=True)


def normalise_monthly_economic(rows: list[dict], rules: Availability) -> pd.DataFrame:
    """Monthly series keyed by reference month. Gaps stay missing (never zero-filled)."""
    df = pd.DataFrame(rows)
    ref = pd.to_datetime(df["date"]).dt.to_period("M").dt.to_timestamp()
    out = pd.DataFrame({"date": ref, "value": pd.to_numeric(df["value"], errors="coerce")})
    out = out.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    out["period"] = out["date"].dt.strftime("%Y-%m")
    out["available_at"] = macro_available_at(out["date"], rules)
    out["published_at"] = out["available_at"]
    return out


def normalise_transcripts(rows: list[dict], symbol: str, rules: Availability) -> pd.DataFrame:
    recs = []
    for r in rows:
        content = r.get("content") or ""
        if not content.strip():
            continue
        recs.append({
            "symbol": symbol,
            "doc_id": f"fmp:transcript:{symbol}:{r.get('year')}Q{r.get('period', r.get('quarter'))}",
            "doc_type": "transcript",
            "period": f"{r.get('year')}{r.get('period', '')}",
            "published_at": pd.Timestamp(r["date"], tz="UTC"),
            "text": content,
        })
    df = pd.DataFrame(recs)
    if not df.empty:
        df["available_at"] = transcript_available_at(df["published_at"], rules)
    return df
