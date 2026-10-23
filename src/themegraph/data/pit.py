"""Point-in-time rules.

Every normalised record carries `published_at` and `available_at` (UTC). The
pipeline reads data only through `as_of`, so nothing published after a snapshot
can influence that snapshot.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..config import Availability

PIT_COLUMNS = ["period", "published_at", "first_seen_at", "available_at", "vintage"]

# Prices for session d are treated as known at 22:00 UTC on d (after the US close).
PRICE_KNOWN_HOUR_UTC = 22


def snapshot_ts(date: dt.date | pd.Timestamp | str) -> pd.Timestamp:
    """A snapshot dated d sees everything available by the end of d (UTC)."""
    return pd.Timestamp(date).tz_localize(None).normalize().tz_localize("UTC") + pd.Timedelta(
        hours=23, minutes=59, seconds=59
    )


def to_utc(values) -> pd.Series | pd.Timestamp:
    out = pd.to_datetime(values, utc=True)
    return out


def price_available_at(dates: pd.Series, rules: Availability) -> pd.Series:
    d = pd.to_datetime(dates).dt.tz_localize(None).dt.normalize().dt.tz_localize("UTC")
    return d + pd.Timedelta(hours=PRICE_KNOWN_HOUR_UTC) + pd.Timedelta(days=rules.price_lag_days)


def transcript_available_at(published: pd.Series, rules: Availability) -> pd.Series:
    return to_utc(published) + pd.Timedelta(days=rules.transcript_lag_days)


def filing_available_at(accepted: pd.Series, rules: Availability) -> pd.Series:
    return to_utc(accepted) + pd.Timedelta(days=rules.filing_lag_days)


def macro_available_at(reference_month: pd.Series, rules: Availability) -> pd.Series:
    """A monthly value for reference month M is usable from day 1 of M + lag."""
    m = pd.to_datetime(reference_month).dt.to_period("M")
    return (m + rules.macro_release_lag_months).dt.to_timestamp().dt.tz_localize("UTC")


def as_of(df: pd.DataFrame, t, column: str = "available_at") -> pd.DataFrame:
    """Rows usable at snapshot `t` (a date or timestamp)."""
    ts = t if isinstance(t, pd.Timestamp) and t.tzinfo is not None else snapshot_ts(t)
    return df.loc[df[column] <= ts]


def assert_pit(df: pd.DataFrame) -> None:
    missing = [c for c in ("published_at", "available_at") if c not in df.columns]
    if missing:
        raise ValueError(f"table lacks point-in-time columns: {missing}")
    if (df["available_at"] < df["published_at"]).any():
        raise ValueError("available_at earlier than published_at")
