"""What the topic layer found at one month-end, in the form the registry needs.

Reads a pipeline RunContext (the research pipeline's default topic method, window
mode) and, for one snapshot, returns each topic that is broad enough to track:
its member passages, companies and their exposure, a meaning vector, coherence,
point-in-time keywords, quoted evidence and the mix of evidence roles.

Evidence roles (transcripts are split into speaker turns):
- management_remarks: prepared remarks
- management_answer: a Q&A turn by someone who also gave prepared remarks
- analyst_question: a Q&A turn by someone who gave no prepared remarks on that call
  (a heuristic: an executive who only speaks in Q&A is counted here)
- filing: a section of a 10-K, 10-Q, 8-K, 20-F or 6-K
Analyst questions are kept as evidence of investor attention; they are labelled so
they are never presented as company statements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..text.keywords import ctfidf_keywords
from . import tone as T
from .config import MonitorConfig

QUOTE_CHARS = 500
ROLES = ("management_remarks", "management_answer", "analyst_question", "filing")


@dataclass
class Cluster:
    t: pd.Timestamp
    track_id: int
    members: list[str]
    exposure: dict[str, float]          # symbol -> exposure (constituent candidates)
    passages_by_issuer: dict[str, int]  # every company with text in the topic
    n_passages: int
    share: float                        # of all passages in the window
    centroid: np.ndarray
    coherence: float                    # mean member cosine to the centroid
    keywords: list[str] = field(default_factory=list)
    quotes: list[dict] = field(default_factory=list)
    roles: dict[str, float] = field(default_factory=dict)
    n_documents: int = 0
    flagged: bool = False               # flagged by the research emergence rule at t
    tone: dict = field(default_factory=dict)  # headwind/tailwind (monitor/tone.py)


def passage_roles(p: pd.DataFrame) -> pd.Series:
    genre = p["genre"].astype(str)
    unit = p["unit"].astype(str) if "unit" in p else pd.Series("", index=p.index)
    speaker = unit.str.rsplit("|", n=1).str[0]
    prep = genre == "transcript:prepared"
    gave_remarks = set(zip(p.loc[prep, "doc_id"], speaker[prep], strict=True))
    in_prep = pd.Series([(d, s) in gave_remarks for d, s in zip(p["doc_id"], speaker,
                                                                  strict=True)], index=p.index)
    role = pd.Series("filing", index=p.index)
    role[prep] = "management_remarks"
    qa = genre == "transcript:qa"
    role[qa & in_prep] = "management_answer"
    role[qa & ~in_prep] = "analyst_question"
    return role


class SnapshotReader:
    """Precomputes per-run lookups once; `clusters(t)` is then cheap per snapshot."""

    def __init__(self, ctx, mcfg: MonitorConfig):
        self.ctx, self.m = ctx, mcfg
        p = ctx.passages.reset_index(drop=True)
        self.p = p
        self.pos = pd.Series(np.arange(len(p)), index=p["passage_id"].to_numpy())
        self.role = passage_roles(p).to_numpy()
        V = ctx.vectors.astype(np.float32, copy=False)
        genres = p["genre"].astype(str).to_numpy()
        self.genres = genres
        # Run-wide genre means: one representation for every month, so meaning
        # vectors from different months can be compared (as in eval/diagnostics).
        self.genre_mean = {g: V[genres == g].mean(axis=0) for g in np.unique(genres)}
        self.V = V
        led = ctx.ledger if ctx.ledger is not None else pd.DataFrame()
        self.flagged = set(zip(led["track_id"], led["t"], strict=True)) if len(led) else set()
        self.totals = dict(zip(ctx.windows["t"], ctx.windows["n_passages"], strict=True))
        self.first_doc = p.groupby("symbol")["available_at"].min().to_dict()
        self.text = p["text"].astype(str).to_numpy()
        self.sign, _ = T.score(self.text)
        self.company = self.role != "analyst_question"

    def _window(self, t: pd.Timestamp) -> np.ndarray:
        p = self.p
        return ((p["available_at"] > t - pd.Timedelta(days=self.ctx.cfg.text.window_days))
                & (p["available_at"] <= t)).to_numpy()

    def tone_rates(self, t: pd.Timestamp) -> dict[str, tuple[float, float]]:
        """Each genre's share of negative and positive company passages in the window
        ending at t: the rates a theme's tone is compared with."""
        w = self._window(t) & self.company
        out = {}
        for g in np.unique(self.genres[w]):
            s = self.sign[w & (self.genres == g)]
            out[str(g)] = (float(np.mean(s < 0)), float(np.mean(s > 0)))
        return out

    def window_by_issuer(self, t: pd.Timestamp) -> dict[str, int]:
        """Passages per company in the window ending at t (the share denominator)."""
        p = self.p
        win = (p["available_at"] > t - pd.Timedelta(days=self.ctx.cfg.text.window_days)) \
            & (p["available_at"] <= t)
        return {k: int(v) for k, v in p.loc[win, "symbol"].value_counts().items()}

    def _unit(self, idx: np.ndarray) -> np.ndarray:
        X = self.V[idx] - np.stack([self.genre_mean[g] for g in self.genres[idx]])
        n = np.linalg.norm(X, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return X / n

    def clusters(self, t: pd.Timestamp) -> list[Cluster]:
        ctx, m = self.ctx, self.m
        a = ctx.assignments.loc[ctx.assignments["t"] == t]
        ex = ctx.exposure.loc[ctx.exposure["t"] == t]
        total = self.totals.get(t, 0)
        rates = self.tone_rates(t)
        out = []
        for tid, g in a.groupby("track_id", sort=True):
            e = ex.loc[ex["track_id"] == tid]
            keep = e.loc[e["exposure"] >= m.basket.min_exposure]
            if keep["symbol"].nunique() < m.pool.min_issuers:
                continue
            if e["n_passages"].sum() and \
                    e["n_passages"].max() / e["n_passages"].sum() > m.pool.max_issuer_share:
                continue
            members = g["passage_id"].tolist()
            idx = self.pos.reindex(members).dropna().astype(int).to_numpy()
            if not len(idx):
                continue
            X = self._unit(idx)
            c = X.mean(axis=0)
            c = c / (np.linalg.norm(c) or 1.0)
            sims = X @ c
            roles = pd.Series(self.role[idx]).value_counts(normalize=True)
            out.append(Cluster(
                t=t, track_id=int(tid), members=members,
                exposure={r.symbol: round(float(r.exposure), 4) for r in keep.itertuples()},
                passages_by_issuer={r.symbol: int(r.n_passages) for r in e.itertuples()},
                n_passages=len(members), share=len(members) / total if total else 0.0,
                centroid=c.astype(np.float32), coherence=float(sims.mean()),
                roles={r: round(float(roles.get(r, 0.0)), 3) for r in ROLES},
                n_documents=int(self.p["doc_id"].iloc[idx].nunique()),
                flagged=(tid, t) in self.flagged))
            co = idx[self.company[idx]]
            out[-1].tone = T.theme_tone(self.sign[co], self.genres[co], rates, m.tone,
                                        texts=self.text[co])
            out[-1]._order = idx[np.argsort(-sims, kind="stable")]
        self._describe(t, out)
        return out

    def _describe(self, t: pd.Timestamp, cl: list[Cluster]) -> None:
        """Point-in-time keywords (against the rest of the window) and quotes."""
        if not cl:
            return
        p, text = self.p, self.p["text"].to_numpy()
        win = ((p["available_at"] > t - pd.Timedelta(days=self.ctx.cfg.text.window_days))
               & (p["available_at"] <= t)).to_numpy()
        used = np.concatenate([c._order for c in cl])
        bg = np.setdiff1d(np.flatnonzero(win), used)[:400]
        docs = {i: " ".join(text[c._order[:200]]) for i, c in enumerate(cl)}
        if len(bg):
            docs[-1] = " ".join(text[bg])
        kws = ctfidf_keywords(docs, 8)
        for i, c in enumerate(cl):
            c.keywords = list(kws.get(i, []))
            picked, seen = [], set()
            for j in c._order:  # closest to the centre, one per company first
                s = p["symbol"].iat[j]
                if s not in seen:
                    picked.append(j)
                    seen.add(s)
                if len(picked) == self.m.evidence.quotes:
                    break
            c.quotes = [self._quote(j) for j in picked]

    def _quote(self, j: int) -> dict:
        r = self.p.iloc[j]
        return {"passage_id": r["passage_id"], "symbol": r["symbol"],
                "date": str(pd.Timestamp(r["available_at"]).date()),
                "document": r.get("doc_id"), "doc_type": r.get("doc_type"),
                "genre": r.get("genre"), "role": str(self.role[j]),
                "url": r.get("url") if isinstance(r.get("url"), str) else None,
                "text": " ".join(str(r["text"]).split())[:QUOTE_CHARS]}
