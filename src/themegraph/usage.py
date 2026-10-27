"""Process-wide counters of provider requests and cache reuse, reported by
`themegraph monitor refresh` in status.json (docs/MONITOR.md, "Run metrics").
Counts only; no URLs, keys or parameters are recorded."""

from __future__ import annotations

from collections import Counter

USAGE: Counter = Counter()


def snapshot() -> dict[str, int]:
    return dict(sorted(USAGE.items()))
