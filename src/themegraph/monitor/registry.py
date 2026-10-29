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
  months ago, whether the emergence rule flagged it; growth, new companies and
  both routes count only comparable companies (monitor/coverage.py), so adding
  companies to the universe never reads as acceleration;
- theme strength: coherence, breadth (companies, segments), persistence (months
  present of the last 12), evidence mix (management / filings / analyst questions);
- market strength is computed from basket prices in baskets.py.

History is append-only: a month-end already processed is never processed again.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from .clusters import Cluster
from .config import MonitorConfig
from .coverage import Coverage

SCHEMA = 1


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class Registry:
    def __init__(self, root: Path, id_prefix: str = "th"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / "registry.json"
        self.data = json.loads(p.read_text()) if p.exists() else {
            "schema": SCHEMA, "created_at": None, "go_live": None, "last_snapshot": None,
            "next_id": 1, "themes": {}}
        # New themes are named <prefix>-NNNNN; a forked profile has its own prefix so
        # its new themes never share an ID with the profile it came from.
        self.data.setdefault("id_prefix", id_prefix)
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
        """Write everything to temporary files first; registry.json, the record of
        what has been processed, is replaced last, so an interrupted save leaves the
        previous registry in place."""
        reg = self.root / "registry.json"
        tmp = self.root / "vectors.tmp.npz"
        np.savez(tmp, **self.vectors)
        text = json.dumps(self.data, indent=1, sort_keys=True, default=str)
        if reg.exists():
            shutil.copyfile(reg, self.root / "registry.prev.json")
        os.replace(tmp, self.root / "vectors.npz")
        _atomic_write(reg, text)
        if self._events:
            with open(self.root / "events.jsonl", "a") as f:
                for e in self._events:
                    f.write(json.dumps(e, default=str) + "\n")
            self._events = []

    # ---- update ----
    def process(self, t: pd.Timestamp, clusters: list[Cluster], m: MonitorConfig,
                cov: Coverage, roles: dict[str, str], now: str,
                window: dict[str, int], history=None) -> dict:
        """Apply one month-end. `window` is passages per company in its window (the
        share denominator). `history(t)` returns the clusters at an earlier month-end
        (used only by the newly-covered route). Returns counts of what changed."""
        last = self.data["last_snapshot"]
        if last is not None and pd.Timestamp(last) >= t:
            return {"skipped": True}
        if self.data["created_at"] is None:
            # Prices after the first refresh are observed live; earlier ones replay.
            self.data["created_at"] = self.data["go_live"] = now
        ts = str(t)
        snaps = self.data.setdefault("snapshots", [])
        snaps.append(ts)
        self.data.setdefault("snapshot_meta", {})[ts] = {
            "universe_version": cov.u.version, "window_by_issuer": window}
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
            row = self._measures(th, c, ts, snaps, cov, m)
            th["measures"].append(row)
            if not th["published"] and c is not None:
                route = self._route(th, c, row, snaps, median_coh, m) or \
                    self._newly_covered(th, c, row, snaps, median_coh, m, cov, history)
                th["route_check"] = self._route_check(th, c, row, snaps, median_coh, m)
                if route and not self._eligible(c, ts, cov):
                    # Never publish a theme with nothing to buy (basket invariant).
                    th["route_check"]["blocked"] = "no company in coverage at this month-end"
                    self.event(ts, tid, "publication_blocked", route=route,
                               reason="empty basket")
                    route = None
                if route:
                    self._publish(th, c, ts, route, now, m, roles, cov)
                    counts["published"] += 1
            if th["published"]:
                self._sector_benchmark(th, ts, m, cov)
                self._status(th, ts, snaps, m, cov)
                if c is not None:
                    self._version(th, c, ts, now, m, roles, cov)
                elif th.get("pending"):
                    # Absent this month: the proposal was not observed again, so the
                    # consecutive count restarts when the theme returns.
                    self.event(ts, th["id"], "basket_proposal_reset", reason="theme absent",
                               proposed=th["pending"].get("members"))
                    th["pending"] = None
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
        tid = f"{self.data.get('id_prefix', 'th')}-{self.data['next_id']:05d}"
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

    def _measures(self, th: dict, c: Cluster | None, ts: str, snaps: list[str],
                  cov: Coverage, m: MonitorConfig) -> dict:
        prev = {r["t"]: r for r in th["measures"]}
        base = snaps[-4] if len(snaps) >= 4 else None
        old = prev.get(base) if base else None
        last12 = set(snaps[-12:])
        persistence = sum(s in last12 for s in th["seen"])
        if c is None:
            # Not found: an observation of zero only if the theme could have been seen
            # (its companies had documents in the window); otherwise missing evidence,
            # recorded with no share at all.
            seen = self._observed(th, {"t": ts}, cov)
            return {"t": ts, "present": False, "observed": seen,
                    "share": 0.0 if seen else None, "persistence_12": persistence}
        meta = self.data["snapshot_meta"]
        window = meta[ts]["window_by_issuer"]
        issuers = sorted(c.exposure)
        ref = base or ts
        comp = {s for s in window if cov.comparable(s, ts, ref)}
        comp_issuers = sorted(s for s in issuers if s in comp)

        def share(by_issuer: dict, win: dict) -> float | None:
            den = sum(win.get(s, 0) for s in comp)
            return sum(by_issuer.get(s, 0) for s in comp) / den if den else None

        now_c = share(c.passages_by_issuer, window)
        old_c = share(old.get("passages_by_issuer", {}), meta.get(base, {}).get(
            "window_by_issuer", {})) if old and old.get("present") else None
        growth = round(now_c / old_c, 3) if now_c is not None and old_c else None
        old_issuers = set(old.get("issuers", [])) if old else set()
        return {
            "t": ts, "present": True, "observed": True,
            # emergence (growth, new companies and the routes use comparable companies)
            "share": round(c.share, 5), "n_passages": c.n_passages,
            "share_comparable": round(now_c, 5) if now_c is not None else None,
            "growth_3m": growth,
            "issuers": issuers, "n_issuers": len(issuers),
            "comparable_issuers": comp_issuers,
            "recently_added": sorted(set(issuers) - comp),
            "new_issuers_3m": sorted(set(comp_issuers) - old_issuers) if old else None,
            "flagged": c.flagged,
            "flagged_comparable": bool(
                c.flagged and len(comp_issuers) >= m.pool.min_issuers
                and (growth is None or growth >= m.coverage.min_growth_ratio)),
            # theme strength
            "coherence": round(c.coherence, 4),
            "sectors": sorted({cov.sector.get(s, "?") for s in issuers}),
            "n_segments": len({cov.issuers[s].segment for s in issuers if s in cov.issuers}),
            "persistence_12": persistence, "n_documents": c.n_documents,
            "evidence_roles": c.roles,
            "passages_by_issuer": c.passages_by_issuer,
        }

    @staticmethod
    def _route(th: dict, c: Cluster, row: dict, snaps: list[str], median_coh: float,
               m: MonitorConfig) -> str | None:
        """Both routes count comparable companies only, so a coverage addition
        cannot by itself put a topic into the inventory."""
        if row["flagged_comparable"]:
            return "emergence"
        e = m.established
        window = set(snaps[-e.of_last:])
        if sum(s in window for s in th["seen"]) >= e.present and \
                len(row["comparable_issuers"]) >= e.min_issuers and c.coherence >= median_coh:
            return "established"
        return None

    @staticmethod
    def _route_check(th: dict, c: Cluster, row: dict, snaps: list[str], median_coh: float,
                     m: MonitorConfig) -> dict:
        """Why an unpublished topic did or did not qualify this month (for review of
        what the monitor missed; changes nothing)."""
        e = m.established
        window = set(snaps[-e.of_last:])
        return {"t": row["t"], "flagged": row["flagged"],
                "flagged_comparable": row["flagged_comparable"],
                "months_present": sum(s in window for s in th["seen"]), "of_last": e.of_last,
                "comparable_issuers": len(row["comparable_issuers"]),
                "issuers": row["n_issuers"], "coherence": round(c.coherence, 4),
                "median_coherence": round(median_coh, 4),
                "newly_covered": th.get("newly_covered_check")}

    def _newly_covered(self, th: dict, c: Cluster, row: dict, snaps: list[str],
                       median_coh: float, m: MonitorConfig, cov: Coverage,
                       history) -> str | None:
        """The third route, for coverage expansions. A topic carried mostly by
        companies that were not yet covered at the 3-month comparison base cannot use
        the other two routes (they count comparable companies only), so it would stay
        invisible for months although it may be long-running. It is published as
        "newly covered", never as emerging, when it meets the established route's own
        bar on the backfilled history: present in `present` of the last `of_last`
        month-ends (followed back through earlier month-ends' clusters by the same
        passage/meaning matching as identity), at least `min_issuers` companies, and
        at least median coherence. Its basket starts when it is published; the
        backfilled presence is recorded as evidence, not as an earlier discovery."""
        if history is None:
            return None
        e = m.established
        base = snaps[-4] if len(snaps) >= 4 else None
        # Only companies in coverage at this month-end count (and can be bought): a
        # company whose `covered_from` is later has back-filled documents but is not
        # monitored yet, so it can neither qualify a theme nor enter its basket.
        issuers = [s for s in row["issuers"] if cov.covered(s, row["t"])]
        new = [s for s in issuers if base is not None and not cov.covered(s, base)]
        th["newly_covered_check"] = {"new_companies": len(new), "issuers": len(issuers),
                                     "not_yet_covered": len(row["issuers"]) - len(issuers)}
        if not issuers or len(new) * 2 < len(issuers):
            return None  # mostly already-covered companies: the usual routes decide
        if len(issuers) < e.min_issuers or c.coherence < median_coh:
            th["newly_covered_check"]["failed"] = "breadth or coherence"
            return None
        head, present, months = c, 1, []
        for s in reversed(snaps[-e.of_last:-1]):
            best, score = None, 0.0
            for h in history(pd.Timestamp(s)):
                jac = len(set(head.members) & set(h.members)) / \
                    len(set(head.members) | set(h.members))
                cos = float(head.centroid @ h.centroid)
                if (jac >= m.match.min_jaccard or cos >= m.match.min_centroid_cosine) and \
                        jac + cos > score:
                    best, score = h, jac + cos
            if best is not None:
                present += 1
                months.append(s)
                head = best
        th["newly_covered_check"]["backfill_present"] = present
        if present < e.present:
            th["newly_covered_check"]["failed"] = "backfill persistence"
            return None
        th["newly_covered"] = {"at": row["t"], "new_companies": new,
                               "backfill_present": present, "of_last": e.of_last,
                               "backfill_months": sorted(months)}
        return "newly_covered"

    def _sector_benchmark(self, th: dict, ts: str, m: MonitorConfig, cov: Coverage) -> None:
        """Fix a theme's optional sector reference once, from its first basket's
        composition (monitor config `market.sector_benchmarks`). Composition only,
        never returns; never changed afterwards."""
        rule = m.market.sector_benchmarks
        if not rule or "sector_benchmark" in th or not th["versions"]:
            return
        members = [x["symbol"] for x in th["versions"][0]["constituents"]]
        counts: dict[str, int] = {}
        for sym in members:
            sec = cov.sector.get(sym)
            counts[sec] = counts.get(sec, 0) + 1
        bench, chosen, share = None, None, 0.0
        if members:
            sec, n = max(counts.items(), key=lambda kv: kv[1])
            share = n / len(members)
            if share >= m.market.sector_benchmark_min_share and rule.get(sec):
                bench, chosen = rule[sec], sec
        th["sector_benchmark"] = {"symbol": bench, "sector": chosen,
                                  "share": round(share, 3), "decided_at": ts}

    def _live_expansion(self, th: dict, cov: Coverage) -> bool:
        """Whether a newly-covered theme comes from coverage added after the monitor
        went live (a real expansion), rather than from a coverage date inside the
        reconstructed history of a build."""
        go = self.data.get("go_live")
        if not go:
            return False
        day = pd.Timestamp(go).date()
        return any(cov.issuers[s].covered_from is not None and
                   cov.issuers[s].covered_from >= day
                   for s in (th.get("newly_covered") or {}).get("new_companies", [])
                   if s in cov.issuers)

    def _publish(self, th: dict, c: Cluster, ts: str, route: str, now: str,
                 m: MonitorConfig, roles: dict[str, str], cov: Coverage) -> None:
        live = (pd.Timestamp(now) - pd.Timestamp(ts)).days <= m.live_window_days
        if route == "newly_covered" and self._live_expansion(th, cov):
            # The route's promise: the basket starts when the theme is published.
            # Its month-end is evidence of when the backfilled discussion was seen;
            # the entry is the first close after this refresh, also when the refresh
            # ran late (live_window_days does not apply).
            live = True
            th["newly_covered"]["live_expansion"] = True
        th.update(published=True, published_snapshot=ts, published_at=now, route=route,
                  basis="live" if live else "reconstructed", status="new")
        self._add_version(th, c, ts, now, live, roles, cov, reason="published")
        self.event(ts, th["id"], "published", route=route, basis=th["basis"])

    @staticmethod
    def _eligible(c: Cluster, ts: str, cov: Coverage) -> dict[str, float]:
        """Basket candidates: companies still in coverage (a company removed from the
        universe stays in baskets already bought, never in new ones)."""
        return {s: x for s, x in c.exposure.items() if cov.covered(s, ts)}

    def _add_version(self, th, c, ts, now, live, roles, cov, reason):
        members = self._eligible(c, ts, cov)
        if not members:  # callers check first; never store a basket without companies
            raise ValueError(f"{th['id']}: basket version with no company in coverage "
                             f"at {ts}")
        entry = now if live else ts
        if th["versions"] and pd.Timestamp(entry) < pd.Timestamp(th["versions"][-1]["entry_after"]):
            raise ValueError(f"{th['id']}: basket version would be bought ({entry}) before "
                             f"the basket it replaces ({th['versions'][-1]['entry_after']})")
        th["versions"].append({
            "version": len(th["versions"]) + 1, "snapshot": ts, "reason": reason,
            "created_at": now, "basis": "live" if live else "reconstructed",
            "universe_version": cov.u.version,
            # Executable entry: the first benchmark session after this moment.
            "entry_after": entry,
            "constituents": [{"symbol": s, "exposure": x, "role": roles.get(s, "unknown"),
                              "sector": cov.sector.get(s)}
                             for s, x in sorted(members.items())]})
        th["pending"] = None

    def _version(self, th: dict, c: Cluster, ts: str, now: str, m: MonitorConfig,
                 roles: dict[str, str], cov: Coverage) -> None:
        """A membership change is adopted only when the same proposed set is observed
        at `version_confirm_snapshots` consecutive month-ends. `pending` holds the
        proposal: {"members", "since", "count"}. It resets when the observed set
        changes again (a new proposal, count 1), returns to the current basket (no
        proposal), or the theme is absent for a month (see process)."""
        cur = sorted(x["symbol"] for x in th["versions"][-1]["constituents"])
        new = sorted(self._eligible(c, ts, cov))
        p = th.get("pending")
        if not new:
            # None of the theme's companies is in coverage: there is nothing to buy,
            # so the current basket stays held (as when companies leave coverage).
            if p:
                self.event(ts, th["id"], "basket_proposal_reset",
                           reason="no company in coverage", proposed=p.get("members"))
            th["pending"] = None
            return
        if new == cur:
            if p:
                self.event(ts, th["id"], "basket_proposal_reset", reason="back to current",
                           proposed=p.get("members"))
            th["pending"] = None
            return
        if p and p.get("members") == new:
            p["count"] += 1
        else:
            if p:
                self.event(ts, th["id"], "basket_proposal_reset", reason="different set",
                           proposed=p.get("members"), observed=new)
            p = th["pending"] = {"members": new, "since": ts, "count": 1}
        if p["count"] >= m.basket.version_confirm_snapshots:
            live = (pd.Timestamp(now) - pd.Timestamp(ts)).days <= m.live_window_days
            last = th["versions"][-1]
            if pd.Timestamp(th["published_at"]) < pd.Timestamp(now) or \
                    last["entry_after"] == now:
                # The theme was already public before this refresh, or its current
                # basket is bought after this refresh (a live publication while
                # catching up): a change found on an old month-end takes effect when
                # it is decided (bought after `now`), never backdated before the
                # basket it replaces or over days after publication.
                live = True
                if last["created_at"] == now and last["entry_after"] == now:
                    # Superseded within this refresh, never held.
                    if len(th["versions"]) > 1:
                        th["versions"].pop()
                    else:  # the publication basket itself: revise it before entry
                        self.event(ts, th["id"], "basket_revised_before_entry",
                                   was=[x["symbol"] for x in last["constituents"]], now=new)
                        th["versions"].pop()
                        self._add_version(th, c, ts, now, True, roles, cov,
                                          reason="published")
                        th["versions"][0]["revised_before_entry"] = ts
                        th["pending"] = None
                        return
            self._add_version(th, c, ts, now, live, roles, cov, reason="membership changed")
            self.event(ts, th["id"], "basket_version", version=len(th["versions"]),
                       added=sorted(set(new) - set(cur)), removed=sorted(set(cur) - set(new)),
                       confirmed_by=p["count"])

    def annotate_observed(self, cov: Coverage) -> None:
        """Add `observed` to absent rows written before it was recorded (derived from
        the stored windows; nothing measured is changed except that a share of zero
        for a month without evidence becomes no share)."""
        for th in self.themes.values():
            for i, r in enumerate(th["measures"]):
                if not r.get("present") and "observed" not in r:
                    r["observed"] = self._observed(th, r, cov, before=th["measures"][:i])
                    if not r["observed"]:
                        r["share"] = None

    def _observed(self, th: dict, row: dict, cov: Coverage,
                  before: list[dict] | None = None) -> bool:
        """Whether a month-end could have seen the theme: at least half of the
        companies that last discussed it before then had text in that month's
        window. A month without their documents is missing evidence, not a decline
        (and not a zero)."""
        rows = th["measures"] if before is None else before
        last = next((r for r in reversed(rows) if r.get("present") and r["t"] < row["t"]),
                    None)
        window = self.data.get("snapshot_meta", {}).get(row["t"], {}).get("window_by_issuer")
        if last is None or window is None:
            return True  # no information to say otherwise (older registries)
        ref = [s for s in last.get("issuers", []) if cov.covered(s, row["t"])] \
            or last.get("issuers", [])
        return not ref or sum(window.get(s, 0) > 0 for s in ref) / len(ref) >= 0.5

    def _cohort_shares(self, rows: list[dict], cov: Coverage) -> tuple[list, list] | None:
        """Theme share of a fixed company cohort across all `rows`: the companies
        covered and with window text at every one of these month-ends. Adding or
        removing companies, or a company missing documents in one month, changes
        neither the numerator nor the denominator. Returns (shares, cohort), or
        None when the stored windows are not available (older registries)."""
        meta = self.data.get("snapshot_meta", {})
        wins = [meta.get(r["t"], {}).get("window_by_issuer") for r in rows]
        if any(w is None for w in wins) or \
                any(r.get("present") and "passages_by_issuer" not in r for r in rows):
            return None
        cohort = set.intersection(*[{s for s, n in w.items() if n > 0} for w in wins])
        cohort = sorted(s for s in cohort if all(cov.covered(s, r["t"]) for r in rows))
        shares = []
        for r, w in zip(rows, wins, strict=True):
            den = sum(w[s] for s in cohort)
            num = sum(r.get("passages_by_issuer", {}).get(s, 0) for s in cohort) \
                if r.get("present") else 0
            shares.append(num / den if den else None)
        return shares, cohort

    def _status(self, th: dict, ts: str, snaps: list[str], m: MonitorConfig,
                cov: Coverage) -> None:
        s = m.status
        rows = th["measures"]
        absent = 0
        for r in reversed(rows):
            if r["present"]:
                break
            if self._observed(th, r, cov):
                absent += 1  # unobserved months neither count nor break the run
        basis = None
        if th.get("merged_into"):
            status = "merged"
        elif absent >= s.inactive_after_snapshots:
            status = "inactive"
        else:
            k = s.compare_snapshots
            span = rows[-2 * k:]
            res = self._cohort_shares(span, cov) if len(span) == 2 * k else None
            if res is not None:
                shares, cohort = res
                before, recent = shares[:k], shares[k:]
                own = set(cohort) & set(next((r for r in reversed(rows) if r["present"]),
                                             {}).get("issuers", []))
                basis = {"cohort": len(cohort), "theme_companies_in_cohort": len(own)}
            else:  # older registry rows: the share of all text, as before
                recent = [r["share"] for r in rows[-k:]]
                before = [r["share"] for r in rows[-2 * k:-k]]
                basis = {"cohort": None}
            if len(before) < k or any(x is None for x in before + recent) \
                    or not np.mean(before) or (res is not None and not basis[
                        "theme_companies_in_cohort"]):
                # No comparable evidence: keep the current reading.
                status = th["status"] if th["status"] in ("new", "stable", "strengthening",
                                                          "cooling") else "stable"
            else:
                ratio = np.mean(recent) / np.mean(before)
                basis["ratio"] = round(float(ratio), 3)
                status = ("strengthening" if ratio >= s.strengthening_ratio else
                          "cooling" if ratio <= s.cooling_ratio else "stable")
        if status != th["status"]:
            th["status_history"].append({"t": ts, "from": th["status"], "to": status,
                                         **({"basis": basis} if basis else {})})
            th["status"] = status

    def _prune(self, snaps: list[str], m: MonitorConfig) -> None:
        keep = set(snaps[-m.pool.prune_after_snapshots:])
        for tid in [k for k, th in self.themes.items()
                    if not th["published"] and th["last_seen"] not in keep]:
            del self.themes[tid]
            self.vectors.pop(tid, None)
