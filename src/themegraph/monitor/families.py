"""Families of overlapping themes (docs/MONITOR.md, "Theme families").

Topics are re-clustered every month and broad subjects often come out as several
themes (the same AI build-out seen from memory, networking and power). The
registry keeps each as its own theme: IDs, baskets and history never change here.
For display only, themes that mean nearly the same thing are grouped:

- similarity: cosine of the themes' latest meaning vectors (genre-centred member
  centroids, the vectors identity matching uses after a gap);
- grouping: average-linkage clustering of active published themes, cut at
  `families.min_cosine` (by default the identity bar: themes this close would be
  matched as one theme if they were not present at the same time). Average linkage
  does not chain: a family is not formed by A~B and B~C alone;
- lead: the most persistent theme in the family, then the broadest, then the oldest.

Text only; returns play no part. Recomputed every refresh from the latest
vectors, so a family can change as the themes change.
"""

from __future__ import annotations

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

ACTIVE = ("new", "stable", "strengthening", "cooling")


def _latest(th: dict) -> dict:
    return next((r for r in reversed(th["measures"]) if r.get("present")), {})


def _lead_key(th: dict):
    m = _latest(th)
    return (-(th["measures"][-1].get("persistence_12") or 0) if th["measures"] else 0,
            -(m.get("n_issuers") or 0), th["first_seen"], th["id"])


def similarity(themes: list[dict], vectors: dict[str, np.ndarray]) -> tuple[list, np.ndarray]:
    ids = [t["id"] for t in themes if t["id"] in vectors]
    if not ids:
        return [], np.zeros((0, 0))
    V = np.stack([np.asarray(vectors[i], dtype=float) for i in ids])
    V = V / np.clip(np.linalg.norm(V, axis=1, keepdims=True), 1e-12, None)
    return ids, np.clip(V @ V.T, -1.0, 1.0)


def families(themes: list[dict], vectors: dict[str, np.ndarray],
             min_cosine: float) -> dict[str, dict]:
    """theme id -> {"family", "lead", "members", "related": [{id, cosine,
    shared_companies}]} for active published themes."""
    active = [t for t in themes if t["published"] and t["status"] in ACTIVE
              and not t.get("merged_into")]
    by = {t["id"]: t for t in active}
    ids, S = similarity(active, vectors)
    if not ids:
        return {}
    if len(ids) == 1:
        labels = np.array([1])
    else:
        D = squareform(1.0 - S, checks=False)
        labels = fcluster(linkage(D, method="average"), t=1.0 - min_cosine,
                          criterion="distance")
    pos = {i: k for k, i in enumerate(ids)}
    out = {}
    for lab in np.unique(labels):
        members = sorted((ids[k] for k in np.flatnonzero(labels == lab)),
                         key=lambda i: _lead_key(by[i]))
        lead = members[0]
        for i in members:
            mine = set(_latest(by[i]).get("issuers", []))
            out[i] = {"family": lead, "lead": i == lead, "members": members,
                      "related": [{"id": j, "cosine": round(float(S[pos[i], pos[j]]), 3),
                                   "shared_companies": sorted(
                                       mine & set(_latest(by[j]).get("issuers", [])))}
                                  for j in members if j != i]}
    return out


def overlap_pairs(themes: list[dict], vectors: dict[str, np.ndarray],
                  floor: float = 0.5) -> list[dict]:
    """Every pair of active published themes at or above `floor` cosine, most similar
    first, with shared companies and keywords: what the family cut is set against."""
    active = [t for t in themes if t["published"] and t["status"] in ACTIVE
              and not t.get("merged_into")]
    by = {t["id"]: t for t in active}
    ids, S = similarity(active, vectors)
    rows = []
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            if S[a, b] < floor:
                continue
            ta, tb = by[ids[a]], by[ids[b]]
            ia, ib = set(_latest(ta).get("issuers", [])), set(_latest(tb).get("issuers", []))
            rows.append({"a": ids[a], "b": ids[b], "cosine": round(float(S[a, b]), 3),
                         "company_jaccard": round(len(ia & ib) / len(ia | ib), 3)
                         if ia | ib else None,
                         "shared_keywords": sorted(set(ta["keywords"][:8])
                                                   & set(tb["keywords"][:8]))})
    return sorted(rows, key=lambda r: -r["cosine"])
