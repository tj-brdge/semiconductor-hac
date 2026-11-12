"""Drop passages that nearly repeat the same issuer's earlier text (boilerplate)."""

from __future__ import annotations

import numpy as np
import pandas as pd

LOOKBACK_DAYS = 730


def novelty_mask(passages: pd.DataFrame, vectors: np.ndarray, max_similarity: float,
                 lookback_days: int = LOOKBACK_DAYS) -> np.ndarray:
    """True where a passage is novel: its cosine similarity to every earlier-available
    passage of the same issuer within `lookback_days` is at most `max_similarity`.
    Only strictly earlier passages are compared, so the rule is point-in-time."""
    keep = np.ones(len(passages), dtype=bool)
    # Times as integers (ns): a tz-aware column gives Python objects, far slower.
    avail = pd.to_datetime(passages["available_at"], utc=True).dt.as_unit("ns").astype(
        "int64").to_numpy()
    lookback = int(pd.Timedelta(days=lookback_days).value)
    for _, idx in passages.groupby("symbol").indices.items():
        idx = idx[np.argsort(avail[idx], kind="stable")]
        V = vectors[idx]
        a = avail[idx]
        # Sorted by time, so the earlier passages inside the lookback are one slice.
        lo = np.searchsorted(a, a - lookback, side="left")
        hi = np.searchsorted(a, a, side="left")
        for j in range(1, len(idx)):
            if hi[j] > lo[j] and float((V[lo[j]:hi[j]] @ V[j]).max()) > max_similarity:
                keep[idx[j]] = False
    return keep
