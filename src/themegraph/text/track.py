"""Link window clusters into topic tracks with persistent IDs and lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .cluster import WindowClusters


@dataclass
class Track:
    track_id: int
    born: pd.Timestamp
    last_seen: pd.Timestamp
    centroid: np.ndarray
    parent: int | None = None
    ended: pd.Timestamp | None = None
    merged_into: int | None = None
    events: list[tuple[pd.Timestamp, str, int | None]] = field(default_factory=list)


class Tracker:
    """Greedy one-to-one continuation with splits, merges and births.

    A window cluster continues a live track when it shares enough passages with the
    track's last cluster (consecutive windows overlap, so a persisting topic keeps
    most of its passages) or when their centroids are close. Ranking uses the sum of
    the two. A track survives up to `max_gap` windows without a match."""

    def __init__(self, link_similarity: float, max_gap: int = 1, link_overlap: float = 0.3):
        self.thr = link_similarity
        self.overlap_thr = link_overlap
        self.max_gap = max_gap
        self.tracks: dict[int, Track] = {}
        self.members: dict[int, set[str]] = {}
        self.next_id = 0
        self.windows: list[pd.Timestamp] = []

    def _new(self, t, centroid, parent=None, event="birth") -> int:
        tid = self.next_id
        self.next_id += 1
        tr = Track(tid, t, t, centroid, parent=parent)
        tr.events.append((t, event, parent))
        self.tracks[tid] = tr
        return tid

    def step(self, wc: WindowClusters) -> dict[int, int]:
        """Assign track IDs to this window's clusters; returns cluster -> track."""
        t = wc.t
        self.windows.append(t)
        horizon = self.windows[-(self.max_gap + 2)] if len(self.windows) > self.max_gap + 1 \
            else self.windows[0]
        live = [tr for tr in self.tracks.values()
                if tr.ended is None and tr.last_seen >= horizon and tr.last_seen < t]
        clusters = sorted(wc.centroids)
        sets: dict[int, set[str]] = {c: set() for c in clusters}
        for pid, lab in zip(wc.passage_ids, wc.labels, strict=True):
            if lab >= 0:
                sets[int(lab)].add(pid)
        assign: dict[int, int] = {}
        if live and clusters:
            C = np.vstack([wc.centroids[c] for c in clusters])
            P = np.vstack([tr.centroid for tr in live])
            S = C @ P.T
            Ov = np.zeros_like(S)
            for i, c in enumerate(clusters):
                for j, tr in enumerate(live):
                    prev = self.members.get(tr.track_id, set())
                    if prev and sets[c]:
                        Ov[i, j] = len(sets[c] & prev) / min(len(sets[c]), len(prev))
            E = (S >= self.thr) | (Ov >= self.overlap_thr)
            score = S + Ov
            pairs = sorted(((score[i, j], i, j) for i in range(len(clusters))
                            for j in range(len(live)) if E[i, j]), reverse=True)
            used_c, used_p = set(), set()
            for _, i, j in pairs:
                if i in used_c or j in used_p:
                    continue
                used_c.add(i)
                used_p.add(j)
                tr = live[j]
                assign[clusters[i]] = tr.track_id
                tr.last_seen, tr.centroid = t, wc.centroids[clusters[i]]
                tr.events.append((t, "continue", None))
            # unmatched clusters linked to a taken predecessor -> split
            for i, c in enumerate(clusters):
                if c in assign:
                    continue
                js = np.flatnonzero(E[i])
                if len(js):
                    parent = live[int(js[np.argmax(score[i, js])])].track_id
                    assign[c] = self._new(t, wc.centroids[c], parent, "split")
            # unmatched live tracks linked to a continued cluster -> merged
            for j, tr in enumerate(live):
                if j in used_p:
                    continue
                is_ = np.flatnonzero(E[:, j])
                if len(is_):
                    target = assign[clusters[int(is_[np.argmax(score[is_, j])])]]
                    tr.ended, tr.merged_into = t, target
                    tr.events.append((t, "merge", target))
                    self.tracks[target].events.append((t, "absorb", tr.track_id))
        for c in clusters:
            if c not in assign:
                assign[c] = self._new(t, wc.centroids[c])
        for c, tid in assign.items():
            self.members[tid] = sets[c]
        # Expire tracks that have now missed more than `max_gap` consecutive windows.
        expire_before = self.windows[-(self.max_gap + 1)] \
            if len(self.windows) > self.max_gap else None
        for tr in self.tracks.values():
            if tr.ended is None and expire_before is not None and tr.last_seen < expire_before:
                tr.ended = t
                tr.events.append((t, "end", None))
                self.members.pop(tr.track_id, None)
        return assign

    def stats(self) -> dict:
        """Churn diagnostics: a healthy tracker has long-lived topics, few births."""
        if not self.tracks:
            return {"tracks": 0}
        n_win = max(len(self.windows), 1)
        life = [len({e[0] for e in tr.events if e[1] in ("birth", "split", "continue")})
                for tr in self.tracks.values()]
        return {
            "tracks": len(self.tracks),
            "windows": n_win,
            "births_per_window": round(len(self.tracks) / n_win, 2),
            "median_track_life_windows": float(np.median(life)),
            "share_tracks_living_6_plus_windows": round(float(np.mean(np.array(life) >= 6)), 3),
        }


def window_exposure(wc: WindowClusters, assign: dict[int, int]) -> pd.DataFrame:
    """Per (track, issuer): passages in the topic and share of the issuer's passages."""
    if not len(wc.labels):
        return pd.DataFrame(columns=["t", "track_id", "symbol", "n_passages", "exposure"])
    df = pd.DataFrame({"symbol": wc.symbols, "label": wc.labels})
    totals = df.groupby("symbol").size()
    df = df.loc[df["label"] >= 0]
    if df.empty:
        return pd.DataFrame(columns=["t", "track_id", "symbol", "n_passages", "exposure"])
    g = df.groupby(["label", "symbol"]).size().rename("n_passages").reset_index()
    g["track_id"] = g["label"].map(assign)
    g["exposure"] = g["n_passages"] / g["symbol"].map(totals)
    g["t"] = wc.t
    return g[["t", "track_id", "symbol", "n_passages", "exposure"]]


def track_events(tracker: Tracker) -> pd.DataFrame:
    rows = [{"track_id": tr.track_id, "t": t, "event": ev, "other": other}
            for tr in tracker.tracks.values() for t, ev, other in tr.events]
    return pd.DataFrame(rows).sort_values(["t", "track_id"]).reset_index(drop=True)
