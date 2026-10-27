"""Persistent theme registry (docs/MONITOR.md).

State lives in one directory:
- registry.json  every tracked theme: identity, names, measures per month-end,
                 basket versions, evidence, lineage, status (atomic writes; the
                 previous file is kept as registry.prev.json)
- vectors.npz    each theme's latest meaning vector (for matching after a gap)
- events.jsonl   append-only log of everything the registry did

Identity. Each month-end's topics are matched to registry themes by shared member
passages (consecutive 180-day windows overlap by most of their text) or, after a
gap, by meaning (cosine of genre-centred member centroids). Matching is one-to-one;
a second topic that shares passages with a matched theme is recorded as a split, a
second theme that shares passages with a matched topic as a merge. Nothing is
deleted once published; unpublished clusters are pruned after a while.

Routes into the published inventory (either is enough):
- emergence: the research pipeline's emergence rule flagged the topic;
- established: present in at least `present` of the last `of_last` month-ends,
  with enough companies and at least median coherence.

Three separate groups of measures are kept per month-end (no composite score):
- emergence: share of window text, change over 3 months, companies now vs 3
  months ago, whether the emergence rule flagged it;
- theme strength: coherence, breadth (companies, segments), persistence (months
  present of the last 12), evidence mix (management / filings / analyst questions);
- market strength is computed from basket prices in baskets.py.

History is append-only: a month-end already processed is never processed again.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from .clusters import Cluster
from .config import MonitorConfig

SCHEMA = 1


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / "registry.json"
        self.data = json.loads(p.read_text()) if p.exists() else {
            "schema": SCHEMA, "created_at": None, "go_live": None, "last_snapshot": None,
            "next_id": 1, "themes": {}}
        v = self.root / "vectors.npz"
        self.vectors = dict(np.load(v)) if v.exists() else {}
        self._events: list[dict] = []

    # ---- persistence ----
    @property
    def themes(self) -> dict[str, dict]:
        return self.data["themes"]

    def published(self) -> list[dict]:
        return [t for t in self.themes.values() if t["published"]]

    def event(self, t, theme_id: str, kind: str, **kw) -> None:
        self._events.append({"t": str(t), "theme": theme_id, "event": kind, **kw,
                             "recorded_at": _now()})

    def save(self) -> None:
        reg = self.root / "registry.json"
        if reg.exists():
            os.replace(reg, self.root / "registry.prev.json")
        _atomic_write(reg, json.dumps(self.data, indent=1, sort_keys=True, default=str))
        tmp = self.root / "vectors.tmp.npz"
        np.savez(tmp, **self.vectors)
        os.replace(tmp, self.root / "vectors.npz")
        if self._events:
            with open(self.root / "events.jsonl", "a") as f:
                for e in self._events:
                    f.write(json.dumps(e, default=str) + "\n")
            self._events = []

    # ---- update ----
    def process(self, t: pd.Timestamp, clusters: list[Cluster], m: MonitorConfig,
                segments: dict[str, str], roles: dict[str, str], now: str) -> dict:
        """Apply one month-end. Returns counts of what changed."""
        last = self.data["last_snapshot"]
        if last is not None and pd.Timestamp(last) >= t:
            return {"skipped": True}
        if self.data["created_at"] is None:
            # Prices after the first refresh are observed live; earlier ones replay.
            self.data["created_at"] = self.data["go_live"] = now
        ts = str(t)
        snaps = self.data.setdefault("snapshots", [])
        snaps.append(ts)
        recent = set(snaps[-(m.match.lookback_snapshots + 1):])
        cands = [k for k, th in self.themes.items()
                 if th["last_seen"] in recent and th.get("merged_into") is None]
        match, splits, merges = self._match(cands, clusters, m)
        counts = {"clusters": len(clusters), "matched": len(match), "new": 0,
                  "published": 0, "splits": len(splits), "merges": len(merges)}
        present = {}
        for j, c in enumerate(clusters):
            if j in match:
                tid = match[j]
            else:
                tid = self._new(c, ts)
                counts["new"] += 1
                if j in splits:
                    self.themes[tid]["lineage"].append({"t": ts, "event": "split_from",
                                                         "other": splits[j]})
                    self.themes[splits[j]]["lineage"].append({"t": ts, "event": "split_into",
                                                               "other": tid})
                    self.event(ts, tid, "split", parent=splits[j])
            present[tid] = c
        for old, into in merges.items():
            th = self.themes[old]
            th["merged_into"] = into
            th["lineage"].append({"t": ts, "event": "merged_into", "other": into})
            self.themes[into]["lineage"].append({"t": ts, "event": "absorbed", "other": old})
            self.event(ts, old, "merge", into=into)

        median_coh = float(np.median([c.coherence for c in clusters])) if clusters else 0.0
        for tid, c in present.items():
            th = self.themes[tid]
            th["last_seen"] = ts
            th["last_members"] = c.members
            self.vectors[tid] = c.centroid
            self._rename(th, c, ts)
            th["evidence"]["latest"] = c.quotes
            th["evidence"]["latest_t"] = ts
            th["seen"].append(ts)
            if not th["evidence"]["first"]:
                th["evidence"]["first"] = c.quotes
                th["evidence"]["first_t"] = ts
        for tid, th in self.themes.items():
            c = present.get(tid)
            th["measures"].append(self._measures(th, c, ts, snaps, segments))
            if not th["published"] and c is not None:
                route = self._route(th, c, snaps, median_coh, m)
                if route:
                    self._publish(th, c, ts, route, now, m, roles)
                    counts["published"] += 1
            if th["published"]:
                self._status(th, ts, snaps, m)
                if c is not None:
                    self._version(th, c, ts, now, m, roles)
        self._prune(snaps, m)
        self.data["last_snapshot"] = ts
        return counts

    def _match(self, cands: list[str], clusters: list[Cluster], m: MonitorConfig):
        if not cands or not clusters:
            return {}, {}, {}
        S = np.zeros((len(clusters), len(cands)))
        J = np.zeros_like(S)
        for i, k in enumerate(cands):
            old = set(self.themes[k].get("last_members", []))
            v = self.vectors.get(k)
            for j, c in enumerate(clusters):
                jac = len(old & set(c.members)) / len(old | set(c.members)) if old else 0.0
                cos = float(v @ c.centroid) if v is not None else 0.0
                J[j, i] = jac
                if jac >= m.match.min_jaccard or cos >= m.match.min_centroid_cosine:
                    S[j, i] = 1.0 + jac + cos  # any valid pair beats none
        r, k = linear_sum_assignment(-S)
        match = {int(j): cands[i] for j, i in zip(r, k, strict=True) if S[j, i] > 0}
        taken = set(match.values())
        splits, merges = {}, {}
        for j in range(len(clusters)):  # unmatched topic sharing text with a matched theme
            if j in match:
                continue
            best = int(np.argmax(J[j])) if J.shape[1] else -1
            if best >= 0 and J[j, best] >= m.match.min_jaccard and cands[best] in taken:
                splits[j] = cands[best]
        for i, k in enumerate(cands):  # unmatched theme whose text went to a matched topic
            if k in taken:
                continue
            j = int(np.argmax(J[:, i]))
            if J[j, i] >= m.match.min_jaccard and j in match:
                merges[k] = match[j]
        return match, splits, merges

    def _new(self, c: Cluster, ts: str) -> str:
        tid = f"th-{self.data['next_id']:05d}"
        self.data["next_id"] += 1
        self.themes[tid] = {
            "id": tid, "name": " · ".join(c.keywords[:3]) or tid,
            "name_history": [{"t": ts, "name": " · ".join(c.keywords[:3]) or tid,
                              "keywords": c.keywords}],
            "keywords": c.keywords, "first_seen": ts, "last_seen": ts, "seen": [],
            "published": False, "published_snapshot": None, "published_at": None,
            "route": None, "basis": None, "status": "new", "status_history": [],
            "lineage": [], "merged_into": None, "measures": [], "versions": [],
            "pending": None, "evidence": {"first": [], "first_t": None, "latest": [],
                                          "latest_t": None}}
        self.event(ts, tid, "tracked")
        return tid

    @staticmethod
    def _rename(th: dict, c: Cluster, ts: str) -> None:
        th["keywords"] = c.keywords
        new = c.keywords[:3]
        cur = set(th["name"].split(" · "))
        # Rename only when the name no longer describes the topic: fewer than two of
        # its words are still among the current top-8 keywords (not on reordering).
        if new and len(cur & set(c.keywords[:8])) < 2:
            th["name"] = " · ".join(new)
            th["name_history"].append({"t": ts, "name": th["name"], "keywords": c.keywords})

    @staticmethod
    def _measures(th: dict, c: Cluster | None, ts: str, snaps: list[str],
                  segments: dict[str, str]) -> dict:
        prev = {r["t"]: r for r in th["measures"]}
        back = snaps[-4] if len(snaps) >= 4 else None
        old = prev.get(back) if back else None
        last12 = set(snaps[-12:])
        persistence = sum(s in last12 for s in th["seen"])
        if c is None:
            return {"t": ts, "present": False, "share": 0.0, "persistence_12": persistence}
        old_share = old["share"] if old else None
        old_issuers = set(old.get("issuers", [])) if old else set()
        issuers = sorted(c.exposure)
        return {
            "t": ts, "present": True,
            # emergence
            "share": round(c.share, 5), "n_passages": c.n_passages,
            "share_3m_ago": old_share,
            "growth_3m": round(c.share / old_share, 3) if old_share else None,
            "issuers": issuers, "n_issuers": len(issuers),
            "new_issuers_3m": sorted(set(issuers) - old_issuers) if old else None,
            "flagged": c.flagged,
            # theme strength
            "coherence": round(c.coherence, 4),
            "n_segments": len({segments.get(s, "?") for s in issuers}),
            "persistence_12": persistence, "n_documents": c.n_documents,
            "evidence_roles": c.roles,
        }

    @staticmethod
    def _route(th: dict, c: Cluster, snaps: list[str], median_coh: float,
               m: MonitorConfig) -> str | None:
        if c.flagged:
            return "emergence"
        e = m.established
        window = set(snaps[-e.of_last:])
        if sum(s in window for s in th["seen"]) >= e.present and \
                len(c.exposure) >= e.min_issuers and c.coherence >= median_coh:
            return "established"
        return None

    def _publish(self, th: dict, c: Cluster, ts: str, route: str, now: str,
                 m: MonitorConfig, roles: dict[str, str]) -> None:
        live = (pd.Timestamp(now) - pd.Timestamp(ts)).days <= m.live_window_days
        th.update(published=True, published_snapshot=ts, published_at=now, route=route,
                  basis="live" if live else "reconstructed", status="new")
        self._add_version(th, c, ts, now, live, roles, reason="published")
        self.event(ts, th["id"], "published", route=route, basis=th["basis"])

    def _add_version(self, th, c, ts, now, live, roles, reason):
        th["versions"].append({
            "version": len(th["versions"]) + 1, "snapshot": ts, "reason": reason,
            "created_at": now, "basis": "live" if live else "reconstructed",
            # Executable entry: the first benchmark session after this moment.
            "entry_after": now if live else ts,
            "constituents": [{"symbol": s, "exposure": x, "role": roles.get(s, "unknown")}
                             for s, x in sorted(c.exposure.items())]})
        th["pending"] = None

    def _version(self, th: dict, c: Cluster, ts: str, now: str, m: MonitorConfig,
                 roles: dict[str, str]) -> None:
        cur = {x["symbol"] for x in th["versions"][-1]["constituents"]}
        new = set(c.exposure)
        if new == cur:
            th["pending"] = None
            return
        p = th["pending"] or {"since": ts, "count": 0}
        p["count"] += 1
        th["pending"] = p
        if p["count"] >= m.basket.version_confirm_snapshots:
            live = (pd.Timestamp(now) - pd.Timestamp(ts)).days <= m.live_window_days
            self._add_version(th, c, ts, now, live, roles, reason="membership changed")
            self.event(ts, th["id"], "basket_version", version=len(th["versions"]),
                       added=sorted(new - cur), removed=sorted(cur - new))

    @staticmethod
    def _status(th: dict, ts: str, snaps: list[str], m: MonitorConfig) -> None:
        s = m.status
        rows = th["measures"]
        absent = 0
        for r in reversed(rows):
            if r["present"]:
                break
            absent += 1
        if th.get("merged_into"):
            status = "merged"
        elif absent >= s.inactive_after_snapshots:
            status = "inactive"
        else:
            k = s.compare_snapshots
            recent = [r["share"] for r in rows[-k:]]
            before = [r["share"] for r in rows[-2 * k:-k]]
            if len(before) < k or not np.mean(before):
                status = "new" if th["status"] == "new" else "stable"
            else:
                ratio = np.mean(recent) / np.mean(before)
                status = ("strengthening" if ratio >= s.strengthening_ratio else
                          "cooling" if ratio <= s.cooling_ratio else "stable")
        if status != th["status"]:
            th["status_history"].append({"t": ts, "from": th["status"], "to": status})
            th["status"] = status

    def _prune(self, snaps: list[str], m: MonitorConfig) -> None:
        keep = set(snaps[-m.pool.prune_after_snapshots:])
        for tid in [k for k, th in self.themes.items()
                    if not th["published"] and th["last_seen"] not in keep]:
            del self.themes[tid]
            self.vectors.pop(tid, None)
