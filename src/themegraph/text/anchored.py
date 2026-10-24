"""Stable ("anchored") topics: discover, track, find new (DESIGN.md calibration log).

Per-window re-clustering re-cuts topics every month, so the same subject keeps
getting new identities (median life 2 windows on the 2010-2021 development run).
Here topics are discovered on a schedule and then held fixed:

1. Discover. At each refit date (every `anchor_refit_snapshots` snapshots) the
   passages available in the trailing `anchor_lookback_days` are centred within their
   genre and clustered with the same HAC rules as the window method. The resulting
   centres become the topic codebook. Only past text is used.
2. Track. At each snapshot, every passage in the window is assigned to its nearest
   topic centre if their cosine is at least `anchor_assign_similarity`; otherwise it
   is unassigned. Topic identities do not change between refits.
3. Find new. Unassigned passages in the window are clustered on their own; any
   cluster meeting the minimum size and issuer rules becomes a new topic at once,
   without waiting for the next refit. This is where emerging subjects first appear.

At a refit, each new centre takes over the identity of the existing topic it is
closest to (one-to-one, cosine >= `anchor_match_similarity`); the rest are new
topics, and old topics with no successor retire. Genre centring uses the genre means
of the refit's training passages, so vectors from different months are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from ..config import Text
from .cluster import (
    DEGENERATE_NORM,
    MIN_GENRE_PASSAGES,
    ClusterCache,
    WindowClusters,
    cluster_subset,
    window_mask,
)


@dataclass
class Codebook:
    """Topic centres plus the genre means that define their coordinate system."""
    genre_means: dict[str, np.ndarray]
    pooled_mean: np.ndarray
    ids: list[int] = field(default_factory=list)
    centres: np.ndarray | None = None  # (n_topics, dim), unit rows, centred space

    def transform(self, V: np.ndarray, genres: np.ndarray | None) -> tuple[np.ndarray,
                                                                           np.ndarray]:
        """Centre with the refit's genre means and normalise. Returns (X, valid)."""
        X = V.astype(np.float64, copy=True)
        usable = np.isfinite(X).all(axis=1) & (np.linalg.norm(np.nan_to_num(X), axis=1) > 0)
        X[~usable] = 0.0
        if genres is None:
            X[usable] -= self.pooled_mean
        else:
            for g in np.unique(genres):
                idx = (genres == g) & usable
                X[idx] -= self.genre_means.get(g, self.pooled_mean)
        norms = np.linalg.norm(X, axis=1)
        scale = norms.max() if len(norms) and norms.max() > 0 else 1.0
        valid = usable & (norms > DEGENERATE_NORM * scale)
        X[~valid] = 0.0
        X[valid] /= norms[valid, None]
        return X.astype(np.float32), valid

    def add(self, tid: int, centre: np.ndarray) -> None:
        c = (centre / (np.linalg.norm(centre) or 1.0))[None, :]
        self.ids.append(tid)
        self.centres = c if self.centres is None else np.vstack([self.centres, c])


def _fit_means(V: np.ndarray, genres: np.ndarray | None) -> tuple[dict, np.ndarray]:
    usable = np.isfinite(V).all(axis=1) & (np.linalg.norm(np.nan_to_num(V), axis=1) > 0)
    pooled = V[usable].mean(axis=0) if usable.any() else np.zeros(V.shape[1])
    means = {}
    if genres is not None:
        for g in np.unique(genres):
            idx = (genres == g) & usable
            if idx.sum() >= MIN_GENRE_PASSAGES:
                means[g] = V[idx].mean(axis=0)
    return means, pooled


class AnchoredTopics:
    def __init__(self, cfg: Text, seed: int = 0, cache: ClusterCache | None = None):
        self.cfg, self.seed, self.cache = cfg, seed, cache
        self.book: Codebook | None = None
        self.next_id = 0
        self.events: list[dict] = []
        self.first_centre: dict[int, np.ndarray] = {}
        self.last_centre: dict[int, np.ndarray] = {}
        self.seen: dict[int, list[pd.Timestamp]] = {}
        self.windows: list[pd.Timestamp] = []
        self._last_members: dict[int, set[str]] = {}

    # -- discovery -------------------------------------------------------------
    def _cluster(self, t, ids, syms, X) -> WindowClusters:
        if self.cache is None:
            return cluster_subset(t, ids, syms, X, self.cfg, self.seed, precentred=True)
        key = self.cache.key(ids, syms, ["precentred"], X)
        hit = self.cache.get(key, t, ids, syms, X.shape[1])
        if hit is not None:
            return hit
        wc = cluster_subset(t, ids, syms, X, self.cfg, self.seed, precentred=True)
        self.cache.put(key, wc)
        return wc

    def _new_id(self, t, centre, event: str) -> int:
        tid = self.next_id
        self.next_id += 1
        self.first_centre[tid] = centre
        self.events.append({"track_id": tid, "t": t, "event": event, "other": None})
        return tid

    def refit(self, t, p: pd.DataFrame, V: np.ndarray) -> None:
        m = window_mask(p, t, self.cfg.anchor_lookback_days)
        genres = p["genre"].to_numpy()[m] if self.cfg.center_by_genre else None
        means, pooled = _fit_means(V[m], genres)
        book = Codebook(means, pooled)
        X, valid = book.transform(V[m], genres)
        ids, syms = p["passage_id"].to_numpy()[m][valid], p["symbol"].to_numpy()[m][valid]
        wc = self._cluster(t, ids, syms, X[valid])
        labs = sorted(wc.centroids)
        new = np.vstack([wc.centroids[c] for c in labs]) if labs else np.zeros((0, V.shape[1]))
        # Carry identities: re-express the old centres in the new coordinates through
        # their own members (centres from different genre means are not comparable).
        heirs: dict[int, int] = {}
        if self.book is not None and self.book.centres is not None and len(new):
            old = self._recentre_old(p, V, t, book)
            if old:
                old_ids = list(old)
                S = new @ np.vstack([old[i] for i in old_ids]).T
                r, c = linear_sum_assignment(-S)
                for i, j in zip(r, c, strict=True):
                    if S[i, j] >= self.cfg.anchor_match_similarity:
                        heirs[i] = old_ids[j]
        retired = set(self.book.ids) - set(heirs.values()) if self.book is not None else set()
        for tid in sorted(retired):
            self.events.append({"track_id": tid, "t": t, "event": "end", "other": None})
        for i in range(len(new)):
            tid = heirs.get(i)
            if tid is None:
                tid = self._new_id(t, new[i], "birth")
            book.add(tid, new[i])
        self.book = book

    def _recentre_old(self, p, V, t, book: Codebook) -> dict[int, np.ndarray]:
        """Old topic centres in the new coordinates, from the passages each topic held
        in the latest window (topics with no members are dropped)."""
        if not self._last_members:
            return {}
        pos = pd.Series(np.arange(len(p)), index=p["passage_id"].to_numpy())
        out = {}
        genres = p["genre"].to_numpy() if self.cfg.center_by_genre else None
        for tid, members in self._last_members.items():
            if tid not in self.book.ids:
                continue
            idx = pos.reindex(list(members)).dropna().astype(int).to_numpy()
            if not len(idx):
                continue
            X, valid = book.transform(V[idx], genres[idx] if genres is not None else None)
            if valid.any():
                c = X[valid].mean(axis=0)
                n = np.linalg.norm(c)
                if n > 0:
                    out[tid] = c / n
        return out

    # -- tracking and new topics ----------------------------------------------
    def step(self, t, p: pd.DataFrame, V: np.ndarray) -> WindowClusters:
        self.windows.append(t)
        m = window_mask(p, t, self.cfg.window_days)
        ids, syms = p["passage_id"].to_numpy()[m], p["symbol"].to_numpy()[m]
        genres = p["genre"].to_numpy()[m] if self.cfg.center_by_genre else None
        labels = np.full(len(ids), -1)
        wc = WindowClusters(t, list(ids), np.asarray(syms), labels)
        if self.book is None or not m.any():
            return wc
        X, valid = self.book.transform(V[m], genres)
        wc.excluded = int((~valid).sum())
        if self.book.centres is not None and len(self.book.ids):
            S = X @ self.book.centres.T
            best = S.argmax(axis=1)
            ok = valid & (S[np.arange(len(S)), best] >= self.cfg.anchor_assign_similarity)
            book_ids = np.array(self.book.ids)
            labels[ok] = book_ids[best[ok]]
        # Find new: cluster what no topic claims.
        pool = np.flatnonzero(valid & (labels < 0))
        if len(pool) >= self.cfg.min_cluster_passages:
            sub = self._cluster(t, ids[pool], syms[pool], X[pool])
            for c in sorted(sub.centroids):
                centre = sub.centroids[c]
                tid = self._new_id(t, centre, "birth")
                self.book.add(tid, centre)
                labels[pool[sub.labels == c]] = tid
        # Topics present must meet the same size rules as a window cluster.
        for tid in np.unique(labels[labels >= 0]):
            idx = labels == tid
            if idx.sum() < self.cfg.min_cluster_passages or \
                    len(np.unique(syms[idx])) < self.cfg.min_cluster_issuers:
                labels[idx] = -1
        wc.labels = labels
        pos = {tid: i for i, tid in enumerate(self.book.ids)}
        members: dict[int, set[str]] = {}
        for tid in np.unique(labels[labels >= 0]):
            tid = int(tid)
            wc.centroids[tid] = self.book.centres[pos[tid]]
            self.last_centre[tid] = wc.centroids[tid]
            self.seen.setdefault(tid, []).append(t)
            members[tid] = set(ids[labels == tid])
        self._last_members = members
        return wc

    # -- diagnostics -------------------------------------------------------------
    def stats(self, window_span: int = 1) -> dict:
        """Same churn diagnostics as Tracker.stats, for comparison."""
        if not self.seen:
            return {"tracks": 0}
        n_win = max(len(self.windows), 1)
        life = np.array([len(v) for v in self.seen.values()])
        beyond = np.maximum(life - window_span, 0)
        drift = [float(self.first_centre[t] @ self.last_centre[t])
                 for t, n in zip(self.seen, life, strict=True) if n > 2 * window_span]
        return {
            "topic_mode": "anchored",
            "tracks": len(self.seen),
            "windows": n_win,
            "births_per_window": round(len(self.seen) / n_win, 2),
            "window_span": window_span,
            "median_track_life_windows": float(np.median(life)),
            "median_life_beyond_window_span": float(np.median(beyond)),
            "share_tracks_persisting_beyond_span": round(float(np.mean(beyond > 0)), 3),
            "median_first_last_centroid_cosine": float(np.median(drift)) if drift else None,
        }

    def events_frame(self) -> pd.DataFrame:
        cols = ["track_id", "t", "event", "other"]
        if not self.events:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(self.events, columns=cols).sort_values(
            ["t", "track_id"]).reset_index(drop=True)


def anchored_windows(p: pd.DataFrame, V: np.ndarray, snaps: list[pd.Timestamp], cfg: Text,
                     seed: int = 0, cache: ClusterCache | None = None,
                     progress=lambda x, *_a, **_k: x) -> tuple[list[WindowClusters],
                                                               AnchoredTopics]:
    model, out = AnchoredTopics(cfg, seed, cache), []
    for i, t in enumerate(progress(snaps, "Anchored topics")):
        if i % cfg.anchor_refit_snapshots == 0:
            model.refit(t, p, V)
        out.append(model.step(t, p, V))
    return out, model
