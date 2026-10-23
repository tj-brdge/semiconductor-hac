"""Windowed passage clustering (HAC on cosine distance, fixed threshold)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from sklearn.cluster import MiniBatchKMeans

from ..config import Text

MAX_HAC_POINTS = 5000


@dataclass
class WindowClusters:
    t: pd.Timestamp
    passage_ids: list[str]
    symbols: np.ndarray
    labels: np.ndarray  # -1 = unclustered
    centroids: dict[int, np.ndarray] = field(default_factory=dict)


def _hac_labels(V: np.ndarray, threshold: float, weights: np.ndarray | None = None) -> np.ndarray:
    if len(V) == 1:
        return np.array([1])
    Z = linkage(V, method="average", metric="cosine")
    return fcluster(Z, t=threshold, criterion="distance")


def cluster_window(passages: pd.DataFrame, vectors: np.ndarray, t: pd.Timestamp, cfg: Text,
                   seed: int = 0) -> WindowClusters:
    lo = t - pd.Timedelta(days=cfg.window_days)
    m = ((passages["available_at"] > lo) & (passages["available_at"] <= t)).to_numpy()
    sub = passages.loc[m]
    V = vectors[m]
    labels = np.full(len(sub), -1)
    wc = WindowClusters(t, sub["passage_id"].tolist(), sub["symbol"].to_numpy(), labels)
    if len(sub) < cfg.min_cluster_passages:
        return wc
    if len(sub) > MAX_HAC_POINTS:
        # Pre-aggregate into deterministic micro-clusters, then HAC on their centroids.
        km = MiniBatchKMeans(n_clusters=MAX_HAC_POINTS // 2, random_state=seed, n_init=1,
                             batch_size=4096).fit(V)
        cent = km.cluster_centers_
        cent /= np.linalg.norm(cent, axis=1, keepdims=True).clip(1e-12)
        raw = _hac_labels(cent, cfg.cluster_distance)[km.labels_]
    else:
        raw = _hac_labels(V, cfg.cluster_distance)
    syms = sub["symbol"].to_numpy()
    next_id = 0
    for lab in np.unique(raw):
        idx = np.flatnonzero(raw == lab)
        if len(idx) < cfg.min_cluster_passages:
            continue
        if len(np.unique(syms[idx])) < cfg.min_cluster_issuers:
            continue
        labels[idx] = next_id
        c = V[idx].mean(axis=0)
        wc.centroids[next_id] = c / (np.linalg.norm(c) or 1.0)
        next_id += 1
    wc.labels = labels
    return wc
