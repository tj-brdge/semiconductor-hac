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

At a refit, new centres take over the identities of existing topics through a
one-to-one matching restricted to pairs with cosine >= `anchor_match_similarity`
(`match_identities`: most continuations first, then highest total similarity). The
rest are new topics, and old topics with no successor retire. Genre centring uses the
genre means of the refit's training passages, so vectors from different months are
comparable.

Ancestry and recurring fragments. A refit can cut an existing topic into pieces; only
one piece can keep its identity. Lineage events record where the others came from,
judged by shared passages (the same text), not by shared companies: an unmatched new
topic most of whose training passages the old topic held is a `split` of it, and a
retiring topic most of whose passages went to one continuing topic is a `merge`.
For candidate detection, every newly born topic (at a refit or from unclaimed text)
also gets a point-in-time history: at birth, the windows before it are re-assigned
with the codebook as it stands then, giving the share the new topic's subject already
had. A recurring fragment therefore carries its old level and cannot pass the
new-high test, while a genuinely new subject, which earlier windows did not contain,
starts from a low history and stays discoverable. This uses the subject's own past
rather than its parent's, so a new theme split out of a large, loud topic is not
held to the parent's level.
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


CALIBRATION_THRESHOLDS = tuple(round(x, 2) for x in np.arange(0.25, 0.551, 0.05))


def match_identities(S: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    """One-to-one matches (row, column) using only pairs with S >= threshold.

    Objective, in priority order: (1) keep as many valid continuations as possible,
    (2) among those, the highest total similarity. Invalid pairs are never chosen,
    so an unconstrained assignment cannot trade a valid match for two invalid ones.
    Rows and columns may stay unmatched; counts may differ."""
    if S.size == 0:
        return []
    valid = S >= threshold
    if not valid.any():
        return []
    # Each valid pair is worth a constant larger than any possible gain in total
    # similarity, so cardinality dominates; similarity breaks ties.
    bonus = 2.0 * min(S.shape) + 1.0
    W = np.where(valid, bonus + S, 0.0)
    r, c = linear_sum_assignment(-W)
    return [(int(i), int(j)) for i, j in zip(r, c, strict=True) if valid[i, j]]


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
        self.label_of: dict[str, int] = {}  # passage -> topic it was last assigned to
        self.refits: list[dict] = []
        self.history: list[dict] = []  # point-in-time pre-birth share of new topics
        self.born_at: dict[int, pd.Timestamp] = {}
        self.calibration: list[dict] = []  # per window: share each threshold would claim
        self.min_exposure = 0.0
        self.history_snapshots = 0
        self._all_snaps: list[pd.Timestamp] = []

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

    def _new_id(self, t, centre, event: str, other: int | None = None) -> int:
        tid = self.next_id
        self.next_id += 1
        self.first_centre[tid] = centre
        self.born_at[tid] = t
        self.events.append({"track_id": tid, "t": t, "event": event, "other": other})
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
                for i, j in match_identities(S, self.cfg.anchor_match_similarity):
                    heirs[i] = old_ids[j]
        # Lineage from shared passages: how many of each new topic's training
        # passages each old topic held when last seen.
        held = [pd.Series([self.label_of.get(x) for x in ids[wc.labels == c]],
                          dtype="float").dropna().astype(int).value_counts()
                for c in labs]
        size = [int((wc.labels == c).sum()) for c in labs]
        share = self.cfg.anchor_lineage_share
        retired = set(self.book.ids) - set(heirs.values()) if self.book is not None else set()
        new_ids = []
        n_split = 0
        for i in range(len(new)):
            tid = heirs.get(i)
            if tid is None:
                top = held[i]
                parent = int(top.index[0]) if len(top) and top.iloc[0] >= share * size[i] \
                    else None
                tid = self._new_id(t, new[i], "split" if parent is not None else "birth",
                                   parent)
                n_split += parent is not None
                new_ids.append(tid)
            book.add(tid, new[i])
        n_merge = 0
        for tid in sorted(retired):
            # Where did its passages go? Merged if most went to one continuing topic.
            total = sum(int(h.get(tid, 0)) for h in held)
            dest = max(range(len(labs)), key=lambda i: held[i].get(tid, 0), default=None)
            if total and dest is not None and dest in heirs and \
                    held[dest].get(tid, 0) >= share * total:
                self.events.append({"track_id": tid, "t": t, "event": "merge",
                                    "other": heirs[dest]})
                n_merge += 1
            else:
                self.events.append({"track_id": tid, "t": t, "event": "end", "other": None})
        self.refits.append({"t": t, "old_topics": len(self.book.ids) if self.book else 0,
                            "new_topics": len(new), "continued": len(heirs),
                            "splits": n_split, "births": len(new_ids) - n_split,
                            "merged": n_merge, "retired": len(retired) - n_merge,
                            "training_passages": int(valid.sum())})
        self.book = book
        self._backfill(t, p, V, new_ids)

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
            # Calibration: share of the window's usable passages that existing topics
            # would claim at other thresholds (topics found in unclaimed text add more).
            top = S[np.arange(len(S)), best][valid]
            if len(top):
                self.calibration.append(
                    {"t": t, "n_valid": int(valid.sum()),
                     **{f"claimed_at_{x:.2f}": float((top >= x).mean())
                        for x in CALIBRATION_THRESHOLDS}})
        # Find new: cluster what no topic claims.
        pool = np.flatnonzero(valid & (labels < 0))
        if len(pool) >= self.cfg.min_cluster_passages:
            sub = self._cluster(t, ids[pool], syms[pool], X[pool])
            born = []
            for c in sorted(sub.centroids):
                centre = sub.centroids[c]
                tid = self._new_id(t, centre, "birth")
                self.book.add(tid, centre)
                labels[pool[sub.labels == c]] = tid
                born.append(tid)
            self._backfill(t, p, V, born)
        # Topics present must meet the same size rules as a window cluster.
        for tid in np.unique(labels[labels >= 0]):
            idx = labels == tid
            if idx.sum() < self.cfg.min_cluster_passages or \
                    len(np.unique(syms[idx])) < self.cfg.min_cluster_issuers:
                labels[idx] = -1
        wc.labels = labels
        if self.calibration and self.calibration[-1]["t"] == t and valid.any():
            row = self.calibration[-1]
            row["claimed_now"] = row.get(f"claimed_at_{self.cfg.anchor_assign_similarity:.2f}")
            row["assigned"] = float((labels[valid] >= 0).mean())
        pos = {tid: i for i, tid in enumerate(self.book.ids)}
        members: dict[int, set[str]] = {}
        for tid in np.unique(labels[labels >= 0]):
            tid = int(tid)
            wc.centroids[tid] = self.book.centres[pos[tid]]
            self.last_centre[tid] = wc.centroids[tid]
            self.seen.setdefault(tid, []).append(t)
            members[tid] = set(ids[labels == tid])
        self._last_members = members
        self.label_of.update({pid: int(lab) for pid, lab in zip(ids, labels, strict=True)
                              if lab >= 0})
        return wc

    def _backfill(self, t, p: pd.DataFrame, V: np.ndarray, born: list[int]) -> None:
        """Point-in-time history for topics born at t: re-assign the passages of the
        preceding windows with the codebook as it stands at t (only text available
        by then) and record each new topic's share and issuer count there."""
        if not born or not self.history_snapshots or self.book is None:
            return
        past = [w for w in self._all_snaps if w < t][-self.history_snapshots:]
        if not past:
            return
        genres_all = p["genre"].to_numpy() if self.cfg.center_by_genre else None
        syms_all = p["symbol"].to_numpy()
        book_ids = np.array(self.book.ids)
        want = set(born)
        for w in past:
            idx = np.flatnonzero(window_mask(p, w, self.cfg.window_days))
            if not len(idx):
                continue
            X, valid = self.book.transform(V[idx],
                                           genres_all[idx] if genres_all is not None else None)
            S = X @ self.book.centres.T
            best = S.argmax(axis=1)
            ok = valid & (S[np.arange(len(S)), best] >= self.cfg.anchor_assign_similarity)
            lab = np.where(ok, book_ids[best], -1)
            syms = syms_all[idx]
            totals = pd.Series(syms).value_counts()
            for tid in want:
                hit = lab == tid
                if not hit.any():
                    continue
                per = pd.Series(syms[hit]).value_counts()
                expo = per / totals.reindex(per.index)
                self.history.append({"track_id": tid, "t": w, "known_at": t,
                                     "n_passages": int(hit.sum()), "share": hit.sum() / len(idx),
                                     "n_issuers": int((expo >= self.min_exposure).sum())})

    def history_frame(self) -> pd.DataFrame:
        cols = ["track_id", "t", "known_at", "n_passages", "share", "n_issuers"]
        return pd.DataFrame(self.history, columns=cols)

    # -- diagnostics -------------------------------------------------------------
    def stats(self, window_span: int = 1) -> dict:
        """Churn diagnostics shared with Tracker.stats. Topic drift is measured for
        both modes from actual members in one representation (diagnostics.py), not
        from centres that may sit in different refits' coordinates."""
        if not self.seen:
            return {"tracks": 0}
        n_win = max(len(self.windows), 1)
        life = np.array([len(v) for v in self.seen.values()])
        beyond = np.maximum(life - window_span, 0)
        r = pd.DataFrame(self.refits)
        return {
            "topic_mode": "anchored",
            "tracks": len(self.seen),
            "windows": n_win,
            "births_per_window": round(len(self.seen) / n_win, 2),
            "window_span": window_span,
            "median_track_life_windows": float(np.median(life)),
            "median_life_beyond_window_span": float(np.median(beyond)),
            "share_tracks_persisting_beyond_span": round(float(np.mean(beyond > 0)), 3),
            "refits": len(r),
            "refit_continued_share": round(float(r["continued"].sum() /
                                                 max(r["old_topics"].sum(), 1)), 3)
            if len(r) else None,
            "refit_splits": int(r["splits"].sum()) if len(r) else 0,
            "refit_merges": int(r["merged"].sum()) if len(r) else 0,
            "calibration": self.calibration_summary(),
        }

    def refits_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.refits)

    def calibration_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.calibration)

    def calibration_summary(self) -> dict:
        """Median over windows: share of usable passages existing topics would claim at
        each threshold, and the share actually assigned (claimed plus topics found in
        unclaimed text). An estimate for the claimed part: a different threshold also
        changes which topics exist, so confirm a chosen value with a rerun."""
        c = self.calibration_frame()
        if not len(c):
            return {}
        out = {k: round(float(c[k].median()), 3) for k in c if k.startswith("claimed_at_")}
        if "assigned" in c:
            out["assigned_median"] = round(float(c["assigned"].median()), 3)
            if "claimed_now" in c and c["claimed_now"].notna().any():
                out["found_in_unclaimed_median"] = round(
                    float((c["assigned"] - c["claimed_now"]).median()), 3)
        return out

    def events_frame(self) -> pd.DataFrame:
        cols = ["track_id", "t", "event", "other"]
        if not self.events:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(self.events, columns=cols).sort_values(
            ["t", "track_id"]).reset_index(drop=True)


def anchored_windows(p: pd.DataFrame, V: np.ndarray, snaps: list[pd.Timestamp], cfg: Text,
                     seed: int = 0, cache: ClusterCache | None = None,
                     progress=lambda x, *_a, **_k: x, min_exposure: float = 0.0,
                     history_snapshots: int = 0) -> tuple[list[WindowClusters],
                                                          AnchoredTopics]:
    """`history_snapshots`: how many windows before a topic's birth to reconstruct
    (point in time) for candidate detection; 0 disables it."""
    model, out = AnchoredTopics(cfg, seed, cache), []
    model._all_snaps = list(snaps)
    model.min_exposure = min_exposure
    model.history_snapshots = history_snapshots
    for i, t in enumerate(progress(snaps, "Anchored topics")):
        if i % cfg.anchor_refit_snapshots == 0:
            model.refit(t, p, V)
        out.append(model.step(t, p, V))
    return out, model
