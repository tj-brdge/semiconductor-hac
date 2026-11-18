"""FRED (Federal Reserve Bank of St. Louis) public CSV download: a fallback source
for monthly US series when the price provider's history is short.

No key and no account: the request carries only the series id and a date range.
Values are the current (revised) figures, not first prints; the caller applies
the same publication lag as for the provider's series.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

from .cache import RawCache
from .http import CachedFetcher

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def parse_csv(body: bytes) -> list[dict]:
    """Rows of {"date", "value"} from FRED's two-column CSV. A month with no figure
    (shown as "." or left empty) is skipped, so it stays missing."""
    rows = []
    for rec in csv.reader(io.StringIO(body.decode("utf-8-sig", errors="replace"))):
        if len(rec) < 2:
            continue
        try:
            day = dt.date.fromisoformat(rec[0].strip())
            value = float(rec[1])
        except ValueError:
            continue  # the header, or a month without a value
        rows.append({"date": day.isoformat(), "value": value})
    return rows


class FredClient:
    def __init__(self, cache_dir: Path, fetcher: CachedFetcher | None = None,
                 vintage: str | None = None, offline: bool = False):
        self.vintage = vintage or dt.datetime.now(dt.UTC).date().isoformat()
        import httpx
        self.f = fetcher or CachedFetcher(
            "fred", RawCache(cache_dir), min_interval=0.5, offline=offline,
            client=httpx.Client(follow_redirects=True, timeout=60.0,
                                headers={"User-Agent": "themegraph/0.1",
                                         "Accept": "text/csv,*/*"}))

    def series(self, series_id: str, start: dt.date, end: dt.date) -> list[dict]:
        resp = self.f.fetch(URL, params={"id": series_id, "cosd": start.isoformat(),
                                         "coed": end.isoformat()},
                            endpoint=f"fredgraph/{series_id}", vintage=self.vintage)
        rows = parse_csv(resp.body)
        if not rows:
            raise RuntimeError(f"FRED returned no rows for {series_id}")
        return rows
