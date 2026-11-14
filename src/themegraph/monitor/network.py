"""The company network for the dashboard (docs/MONITOR.md, "Network").

Nodes are the covered companies. Two kinds of link, kept separate:
- **price**: the pair's correlation beyond the index over the last six months is
  above the market-cluster cut (monitor/market_clusters.py), i.e. stronger than the
  strongest pair that shuffled returns produce;
- **discussion**: both companies discuss the same active theme this month; the
  weight is the number of themes they share.

Positions are computed here, once per release, with a small deterministic
force-directed layout (fixed seed), so the page needs no layout code and the same
release always draws the same picture. A description of the latest window only:
it selects nothing and feeds no measure.
"""

from __future__ import annotations

import numpy as np

MAX_DISCUSSION_LINKS = 900


def _layout(n: int, edges: list[tuple[int, int, float]], iterations: int = 300,
            seed: int = 7) -> np.ndarray:
    """Fruchterman-Reingold in the unit square: linked companies pull together,
    every pair pushes apart, and a weak pull to the centre keeps unlinked
    companies in view. Deterministic for a given input."""
    rng = np.random.default_rng(seed)
    pos = rng.uniform(-0.5, 0.5, (n, 2))
    if n < 2:
        return pos + 0.5
    k = 1.0 / np.sqrt(n)
    a = np.array([e[0] for e in edges], dtype=int)
    b = np.array([e[1] for e in edges], dtype=int)
    w = np.array([e[2] for e in edges], dtype=float)
    # A company in a dense group has many links: divide each link's pull by the
    # ends' link counts so a group spreads out instead of collapsing to a point.
    deg = np.bincount(np.concatenate([a, b]), minlength=n).astype(float)
    crowd = np.sqrt(np.maximum(deg[a], 1) * np.maximum(deg[b], 1))
    for it in range(iterations):
        d = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((d ** 2).sum(-1)) + 1e-9
        force = (d / dist[..., None]) * (k * k / dist)[..., None]
        disp = force.sum(1)
        if len(edges):
            dv = pos[a] - pos[b]
            dl = np.sqrt((dv ** 2).sum(-1)) + 1e-9
            pull = (dv / dl[:, None]) * (dl * dl / k * w / crowd)[:, None]
            np.add.at(disp, a, -pull)
            np.add.at(disp, b, pull)
        disp -= pos * 0.6  # gravity
        t = 0.1 * (1 - it / iterations) + 0.002
        length = np.sqrt((disp ** 2).sum(-1)) + 1e-9
        pos += disp / length[:, None] * np.minimum(length, t)[:, None]
    pos -= pos.min(0)
    span = pos.max(0)
    span[span == 0] = 1.0
    pos = pos / span
    # Keep dots apart so each can be read and clicked: nudge pairs closer than
    # `gap` away from each other, then fit the square again.
    gap = min(0.07, 0.75 / np.sqrt(n))
    for _ in range(200):
        d = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((d ** 2).sum(-1))
        np.fill_diagonal(dist, 1.0)
        close = dist < gap
        if not close.any():
            break
        same = dist < 1e-9  # coincident points: separate along a fixed direction
        d = np.where(same[..., None], rng.normal(0, 1, d.shape), d)
        dist = np.where(same, 1.0, dist)
        push = (d / dist[..., None]) * ((gap - dist) * close)[..., None] * 0.5
        pos += push.sum(1)
    pos -= pos.min(0)
    span = pos.max(0)
    span[span == 0] = 1.0
    return pos / span


def network(companies: list[dict], docs: list[dict], clusters: dict | None) -> dict:
    """`companies`: export.company_index rows; `docs`: theme documents;
    `clusters`: market_clusters output (or None / unavailable)."""
    syms = [c["symbol"] for c in companies]
    ix = {s: i for i, s in enumerate(syms)}
    price = [(a, b, c) for a, b, c in (clusters or {}).get("links", [])
             if a in ix and b in ix]
    shared: dict[tuple[str, str], list[str]] = {}
    for d in docs:
        if d["status"] in ("inactive", "merged"):
            continue
        m = next((r for r in reversed(d["measures"]) if r.get("present")), {})
        who = sorted(s for s in m.get("issuers", []) if s in ix)
        for i, a in enumerate(who):
            for b in who[i + 1:]:
                shared.setdefault((a, b), []).append(d["id"])
    disc = sorted(shared.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:MAX_DISCUSSION_LINKS]
    top = max((c for _, _, c in price), default=1.0) or 1.0
    most = max((len(v) for _, v in disc), default=1)
    edges = [(ix[a], ix[b], 0.5 + 0.5 * c / top) for a, b, c in price] + \
            [(ix[a], ix[b], 0.25 + 0.5 * len(v) / most) for (a, b), v in disc]
    xy = _layout(len(syms), edges)
    cluster_of = {lf["symbol"]: lf["cluster"] for lf in (clusters or {}).get("leaves", [])}
    degree = [0] * len(syms)
    for i, j, _ in edges:
        degree[i] += 1
        degree[j] += 1
    return {
        "nodes": [{"symbol": s, "sector": c["sector"], "x": round(float(xy[i, 0]), 4),
                   "y": round(float(xy[i, 1]), 4), "degree": degree[i],
                   "cluster": cluster_of.get(s),
                   "themes": [t["id"] for t in c["themes"]]}
                  for i, (s, c) in enumerate(zip(syms, companies, strict=True))],
        "price_links": [{"a": a, "b": b, "corr": c} for a, b, c in price],
        "discussion_links": [{"a": a, "b": b, "themes": v} for (a, b), v in disc],
        "price_cut": ((clusters or {}).get("cut") or {}).get("corr"),
        "window": {"start": (clusters or {}).get("start"), "end": (clusters or {}).get("end"),
                   "index": (clusters or {}).get("index")},
    }
