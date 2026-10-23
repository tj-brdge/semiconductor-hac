"""Windowed passage clustering (HAC on cosine distance, fixed threshold).

Embeddings encode how a passage sounds (Q&A small talk, risk-factor or accounting
phrasing) as well as what it is about. Before clustering, each window's vectors are
centred within their genre (call remarks, call Q&A, each filing section), which
removes the register each genre shares without removing subject directions.
Optionally the window's strongest shared directions are also removed
("all-but-the-top"). Cluster centroids for tracking come from the original vectors,
which keeps them comparable across windows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from sklearn.cluster import MiniBatchKMeans
from sklearn.decomposition import PCA
from sklearn.utils.extmath import randomized_svd

from ..config import Text

MAX_HAC_POINTS = 5000
MICRO_DIMS = 128
CLUSTER_VERSION = 3  # bump when the clustering procedure changes (invalidates the cache)


@dataclass
class WindowClusters:
    t: pd.Timestamp
    passage_ids: list[str]
    symbols: np.ndarray
    labels: np.ndarray  # -1 = unclustered
    centroids: dict[int, np.ndarray] = field(default_factory=dict)

    def members(self, label: int) -> set[str]:
        return {p for p, lab in zip(self.passage_ids, self.labels, strict=True) if lab == label}


def _unit(V: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(V, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return V / n


def remove_common_directions(V: np.ndarray, k: int, seed: int = 0,
                             genres: np.ndarray | None = None) -> np.ndarray:
    """Centre each genre on its own mean, then project out the top-k principal
    directions of the result (k=0 skips that step)."""
    X = V.astype(np.float64, copy=True)
    if genres is None:
        X -= X.mean(axis=0, keepdims=True)
    else:
        for g in np.unique(genres):
            idx = genres == g
            X[idx] -= X[idx].mean(axis=0, keepdims=True)
    if k > 0 and min(X.shape) > k:
        _, _, Vt = randomized_svd(X, n_components=k, random_state=seed)
        X = X - (X @ Vt.T) @ Vt
    return _unit(X).astype(np.float32)


def _hac_labels(V: np.ndarray, threshold: float) -> np.ndarray:
    if len(V) == 1:
        return np.array([1])
    Z = linkage(V, method="average", metric="cosine")
    return fcluster(Z, t=threshold, criterion="distance")


def window_mask(passages: pd.DataFrame, t: pd.Timestamp, window_days: int) -> np.ndarray:
    lo = t - pd.Timedelta(days=window_days)
    return ((passages["available_at"] > lo) & (passages["available_at"] <= t)).to_numpy()


def cluster_window(passages: pd.DataFrame, vectors: np.ndarray, t: pd.Timestamp, cfg: Text,
                   seed: int = 0) -> WindowClusters:
    m = window_mask(passages, t, cfg.window_days)
    genres = passages["genre"].to_numpy()[m] if "genre" in passages else None
    return cluster_subset(t, passages["passage_id"].to_numpy()[m],
                          passages["symbol"].to_numpy()[m], vectors[m], cfg, seed, genres)


def cluster_subset(t: pd.Timestamp, passage_ids: np.ndarray, symbols: np.ndarray,
                   V: np.ndarray, cfg: Text, seed: int = 0,
                   genres: np.ndarray | None = None) -> WindowClusters:
    """Cluster one window's passages. Pure function of its inputs (cache-safe)."""
    labels = np.full(len(passage_ids), -1)
    wc = WindowClusters(t, list(passage_ids), np.asarray(symbols), labels)
    if len(passage_ids) < cfg.min_cluster_passages:
        return wc
    X = remove_common_directions(V, cfg.remove_top_components, seed,
                                 genres if cfg.center_by_genre else None)
    if len(X) > MAX_HAC_POINTS:
        # Group passages into deterministic micro-clusters in a compressed space, then
        # run HAC on the micro-clusters' full-dimension centroids.
        dims = min(MICRO_DIMS, X.shape[1], len(X) - 1)
        Xr = _unit(PCA(dims, random_state=seed).fit_transform(X))
        km = MiniBatchKMeans(n_clusters=MAX_HAC_POINTS // 2, random_state=seed, n_init=1,
                             batch_size=4096).fit(Xr)
        groups = km.labels_
        present = np.unique(groups)
        cent = _unit(np.vstack([X[groups == g].mean(axis=0) for g in present]))
        raw_by_group = dict(zip(present, _hac_labels(cent, cfg.cluster_distance),
                                strict=True))
        raw = np.array([raw_by_group[g] for g in groups])
    else:
        raw = _hac_labels(X, cfg.cluster_distance)
    next_id = 0
    for lab in np.unique(raw):
        idx = np.flatnonzero(raw == lab)
        if len(idx) < cfg.min_cluster_passages:
            continue
        if len(np.unique(symbols[idx])) < cfg.min_cluster_issuers:
            continue
        labels[idx] = next_id
        c = V[idx].mean(axis=0)  # original space: comparable across windows
        wc.centroids[next_id] = c / (np.linalg.norm(c) or 1.0)
        next_id += 1
    wc.labels = labels
    return wc


class ClusterCache:
    """Per-window results keyed by the window's passages, settings and embedder."""

    def __init__(self, root: Path, embedder_id: str, cfg: Text, seed: int):
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.salt = json.dumps([CLUSTER_VERSION, embedder_id, cfg.model_dump(), seed],
                               sort_keys=True, default=str)

    def key(self, passage_ids) -> str:
        h = hashlib.sha256(self.salt.encode())
        for p in passage_ids:
            h.update(p.encode())
            h.update(b"\0")
        return h.hexdigest()

    def get(self, key: str, t, passage_ids, symbols) -> WindowClusters | None:
        path = self.dir / f"{key}.npz"
        if not path.exists():
            return None
        z = np.load(path)
        cents = {int(i): c for i, c in zip(z["cent_ids"], z["cents"], strict=True)}
        return WindowClusters(t, list(passage_ids), np.asarray(symbols), z["labels"], cents)

    def put(self, key: str, wc: WindowClusters) -> None:
        ids = np.array(sorted(wc.centroids), dtype=int)
        cents = (np.vstack([wc.centroids[i] for i in ids]) if len(ids)
                 else np.zeros((0, 0), np.float32))
        tmp = self.dir / f"{key}.tmp.npz"
        np.savez(tmp, labels=wc.labels, cent_ids=ids, cents=cents)
        tmp.rename(self.dir / f"{key}.npz")
