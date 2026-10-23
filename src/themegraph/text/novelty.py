"""Drop passages that nearly repeat the same issuer's earlier text (boilerplate)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def novelty_mask(passages: pd.DataFrame, vectors: np.ndarray, max_similarity: float,
                 lookback_days: int = 730) -> np.ndarray:
    """True where a passage is novel: its cosine similarity to every earlier-available
    passage of the same issuer within `lookback_days` is at most `max_similarity`.
    Only strictly earlier passages are compared, so the rule is point-in-time."""
    keep = np.ones(len(passages), dtype=bool)
    avail = passages["available_at"].to_numpy()
    lookback = np.timedelta64(lookback_days, "D")
    for _, idx in passages.groupby("symbol").indices.items():
        idx = idx[np.argsort(avail[idx], kind="stable")]
        V = vectors[idx]
        sims = V @ V.T
        a = avail[idx]
        for j in range(1, len(idx)):
            prior = (a[:j] < a[j]) & (a[:j] >= a[j] - lookback)
            if prior.any() and sims[j, :j][prior].max() > max_similarity:
                keep[idx[j]] = False
    return keep
