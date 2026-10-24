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
CLUSTER_VERSION = 4  # bump when the clustering procedure changes (invalidates the cache)
MIN_GENRE_PASSAGES = 3  # smaller genres are centred on the pooled window mean instead
DEGENERATE_NORM = 1e-6  # residual norms below this carry no direction: excluded


@dataclass
class WindowClusters:
    t: pd.Timestamp
    passage_ids: list[str]
    symbols: np.ndarray
    labels: np.ndarray  # -1 = unclustered (or excluded as degenerate)
    centroids: dict[int, np.ndarray] = field(default_factory=dict)
    excluded: int = 0  # passages with no usable direction after centring

    def members(self, label: int) -> set[str]:
        return {p for p, lab in zip(self.passage_ids, self.labels, strict=True) if lab == label}


def _unit(V: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(V, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return V / n


def remove_common_directions(V: np.ndarray, k: int, seed: int = 0,
                             genres: np.ndarray | None = None
                             ) -> tuple[np.ndarray, np.ndarray]:
    """Centre each genre on its own mean, then project out the top-k principal
    directions (k=0 skips that step). Returns (unit vectors, valid mask).

    Policy for degenerate inputs, applied explicitly rather than hidden by jitter:
    - A genre with fewer than MIN_GENRE_PASSAGES passages in the window is centred on
      the window mean instead of its own: a one- or two-passage "genre mean" mostly
      erases the passages themselves (a singleton becomes exactly zero).
    - Any residual whose norm falls below DEGENERATE_NORM (relative to the largest
      residual) has no direction; it is marked invalid, left unclustered (label -1)
      and counted, never assigned a random direction.
    """
    X = V.astype(np.float64, copy=True)
    usable = np.isfinite(X).all(axis=1) & (np.linalg.norm(np.nan_to_num(X), axis=1) > 0)
    X[~usable] = 0.0  # empty or non-finite embeddings: excluded, and kept out of means
    if not usable.any():
        return X.astype(np.float32), usable
    window_mean = X[usable].mean(axis=0, keepdims=True)
    if genres is None:
        X[usable] -= window_mean
    else:
        for g in np.unique(genres):
            idx = (genres == g) & usable
            mean = X[idx].mean(axis=0, keepdims=True) if idx.sum() >= MIN_GENRE_PASSAGES \
                else window_mean
            X[idx] -= mean
    if k > 0 and min(X.shape) > k:
        _, _, Vt = randomized_svd(X, n_components=k, random_state=seed)
        X = X - (X @ Vt.T) @ Vt
    norms = np.linalg.norm(X, axis=1)
    scale = norms.max() if len(norms) and norms.max() > 0 else 1.0
    valid = usable & (norms > DEGENERATE_NORM * scale)
    X[~valid] = 0.0
    X[valid] /= norms[valid, None]
    return X.astype(np.float32), valid


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
                   genres: np.ndarray | None = None,
                   precentred: bool = False) -> WindowClusters:
    """Cluster one window's passages. Pure function of its inputs (cache-safe).
    `precentred`: V is already centred and normalised (anchored topics); centring
    again on these passages' own mean would erase a subject that dominates them."""
    labels = np.full(len(passage_ids), -1)
    wc = WindowClusters(t, list(passage_ids), np.asarray(symbols), labels)
    if len(passage_ids) < cfg.min_cluster_passages:
        return wc
    if precentred:
        valid = np.linalg.norm(V, axis=1) > DEGENERATE_NORM
        X = np.where(valid[:, None], _unit(V), 0.0).astype(np.float32)
    else:
        X, valid = remove_common_directions(V, cfg.remove_top_components, seed,
                                            genres if cfg.center_by_genre else None)
    wc.excluded = int((~valid).sum())
    pos = np.flatnonzero(valid)  # cluster valid rows only; keep alignment via pos
    raw = np.zeros(len(passage_ids), dtype=int)  # 0 = not clustered
    if len(pos) >= cfg.min_cluster_passages:
        Xv = X[pos]
        if len(Xv) > MAX_HAC_POINTS:
            # Group passages into deterministic micro-clusters in a compressed space,
            # then run HAC on the micro-clusters' full-dimension centroids. A
            # micro-centroid can cancel to ~0 (members pointing opposite ways); its
            # members are then excluded rather than given an arbitrary direction.
            dims = min(MICRO_DIMS, Xv.shape[1], len(Xv) - 1)
            Xr = _unit(PCA(dims, random_state=seed).fit_transform(Xv))
            km = MiniBatchKMeans(n_clusters=MAX_HAC_POINTS // 2, random_state=seed,
                                 n_init=1, batch_size=4096).fit(Xr)
            groups = km.labels_
            present = np.unique(groups)
            means = np.vstack([Xv[groups == g].mean(axis=0) for g in present])
            ok = np.linalg.norm(means, axis=1) > DEGENERATE_NORM
            group_label = dict.fromkeys(present[~ok], 0)
            if ok.sum():
                group_label.update(zip(present[ok], _hac_labels(_unit(means[ok]),
                                                                  cfg.cluster_distance),
                                       strict=True))
            sub = np.array([group_label[g] for g in groups])
            wc.excluded += int((sub == 0).sum())
            raw[pos] = sub
        else:
            raw[pos] = _hac_labels(Xv, cfg.cluster_distance)
    next_id = 0
    for lab in np.unique(raw[raw > 0]):
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
    """Per-window results keyed by everything that determines them: the passages'
    IDs, issuers, genres and embedding vectors (by content hash), the embedder, the
    text settings, the seed and the procedure version. A result is reused only when
    all of these match, and stored metadata is re-checked on load."""

    def __init__(self, root: Path, embedder_id: str, cfg: Text, seed: int):
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)
        # Topic-mode settings act outside a clustering call (anchored inputs are
        # covered by the vectors in the key), so they do not invalidate entries.
        settings = cfg.model_dump(exclude={"topic_mode", "anchor_refit_snapshots",
                                           "anchor_lookback_days", "anchor_assign_similarity",
                                           "anchor_match_similarity",
                                           "anchor_lineage_share"})
        self.salt = json.dumps([CLUSTER_VERSION, MIN_GENRE_PASSAGES, DEGENERATE_NORM,
                                MAX_HAC_POINTS, MICRO_DIMS, embedder_id, settings,
                                seed], sort_keys=True, default=str)

    def key(self, passage_ids, symbols, genres, V: np.ndarray) -> str:
        h = hashlib.sha256(self.salt.encode())
        for col in (passage_ids, symbols, genres if genres is not None else []):
            h.update(b"\x1e")
            for x in col:
                h.update(str(x).encode())
                h.update(b"\0")
        A = np.ascontiguousarray(V, dtype=np.float32)
        h.update(str(A.shape).encode())
        h.update(A.tobytes())
        return h.hexdigest()

    def get(self, key: str, t, passage_ids, symbols, dim: int) -> WindowClusters | None:
        path = self.dir / f"{key}.npz"
        if not path.exists():
            return None
        z = np.load(path)
        labels, cents = z["labels"], z["cents"]
        ok = (str(z["key"]) == key and len(labels) == len(passage_ids)
              and (len(cents) == 0 or cents.shape[1] == dim)
              and set(np.unique(labels[labels >= 0])) == set(z["cent_ids"].tolist()))
        if not ok:  # corrupt or mismatched entry: recompute rather than trust it
            return None
        wc = WindowClusters(t, list(passage_ids), np.asarray(symbols), labels,
                            {int(i): c for i, c in zip(z["cent_ids"], cents, strict=True)})
        wc.excluded = int(z["excluded"])
        return wc

    def put(self, key: str, wc: WindowClusters) -> None:
        ids = np.array(sorted(wc.centroids), dtype=int)
        cents = (np.vstack([wc.centroids[i] for i in ids]) if len(ids)
                 else np.zeros((0, 0), np.float32))
        tmp = self.dir / f"{key}.tmp.npz"
        np.savez(tmp, labels=wc.labels, cent_ids=ids, cents=cents, key=np.array(key),
                 excluded=np.array(wc.excluded))
        tmp.rename(self.dir / f"{key}.npz")
