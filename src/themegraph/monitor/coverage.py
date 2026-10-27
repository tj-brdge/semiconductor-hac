"""Which companies count when comparing a theme over time (docs/MONITOR.md,
"Universe changes").

A company added to the monitored universe arrives with back-filled documents. If
it counted immediately, themes it discusses would appear to accelerate (more
text, more companies) only because coverage grew. So emergence comparisons and
the routes into the inventory use comparable companies only. A company is
comparable at month-end t, against a baseline month-end b, when:
- it was in coverage at the baseline: `covered_from` is None or on or before b;
- it is still covered at t: `covered_until` is None or after t; and
- its document history is long enough: its first document is at least
  `min_history_months` before t.

Other companies still appear in a theme's company list, marked as recently
added, and are bought into new basket versions from their own entry. Earlier
versions and earlier measures are never rewritten.
"""

from __future__ import annotations

import pandas as pd

from ..config import UniverseConfig


class Coverage:
    def __init__(self, universe: UniverseConfig, first_doc: dict[str, pd.Timestamp],
                 min_history_months: int):
        self.u = universe
        self.issuers = {i.symbol: i for i in universe.issuers}
        self.first_doc = first_doc
        self.months = min_history_months
        self.sector = {i.symbol: universe.sectors.get(i.segment, i.segment)
                       for i in universe.issuers}

    @staticmethod
    def _day(t) -> pd.Timestamp:
        return pd.Timestamp(t).tz_localize(None).normalize() if pd.Timestamp(t).tzinfo \
            else pd.Timestamp(t).normalize()

    def covered(self, sym: str, t) -> bool:
        i = self.issuers.get(sym)
        if i is None:
            return False
        d = self._day(t)
        return (i.covered_from is None or pd.Timestamp(i.covered_from) <= d) and \
            (i.covered_until is None or pd.Timestamp(i.covered_until) > d)

    def comparable(self, sym: str, t, base) -> bool:
        first = self.first_doc.get(sym)
        if first is None or not self.covered(sym, t) or not self.covered(sym, base):
            return False
        return self._day(first) <= self._day(t) - pd.DateOffset(months=self.months)

    def summary(self, at) -> dict:
        cur = [i for i in self.u.issuers if self.covered(i.symbol, at)]
        sectors: dict[str, int] = {}
        for i in cur:
            sectors[self.sector[i.symbol]] = sectors.get(self.sector[i.symbol], 0) + 1
        base = self._day(at) - pd.DateOffset(months=3)
        recent = sorted(i.symbol for i in cur if not self.comparable(i.symbol, at, base))
        return {"name": self.u.name, "description": self.u.description,
                "version": self.u.version, "n_companies": len(cur),
                "sectors": dict(sorted(sectors.items(), key=lambda kv: -kv[1])),
                "benchmarks": list(self.u.benchmarks),
                "recently_added": recent,
                "changes": [c.model_dump(mode="json") for c in self.u.changes]}
