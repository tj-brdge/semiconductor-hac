"""Theme candidates (DESIGN.md §6) and the hashed candidate ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Candidates
from ..market.hac import mean_pair_affinity


def topic_series(exposure: pd.DataFrame, min_exposure: float,
                 window_totals: pd.Series | None = None) -> pd.DataFrame:
    """Per (track, t): passage count, share of all passages in that window, and
    count of issuers above min_exposure. Share (not raw count) is what must grow, so
    a window that simply holds more documents does not look like an emerging topic."""
    g = exposure.groupby(["track_id", "t"])
    out = pd.DataFrame({
        "n_passages": g["n_passages"].sum(),
        "n_issuers": g["exposure"].apply(lambda s: int((s >= min_exposure).sum())),
    }).reset_index()
    totals = window_totals if window_totals is not None else \
        exposure.groupby("t")["n_passages"].sum()
    out["share"] = out["n_passages"] / out["t"].map(totals)
    return out.sort_values(["track_id", "t"])


def detect(exposure: pd.DataFrame, snapshots: list[pd.Timestamp], cfg: Candidates,
           segments: dict[str, str], market_aff: dict[pd.Timestamp, pd.DataFrame | None],
           rng_seed: int = 0, null_draws: int = 200,
           window_totals: pd.Series | None = None,
           warmup_until: pd.Timestamp | None = None,
           active_issuers: pd.Series | None = None) -> pd.DataFrame:
    """One ledger row per (track, snapshot) where the emergence rule holds.

    Emergence means: the topic's quarter-smoothed share grows for m steps, by at
    least `min_growth_ratio`, to a level above anything it reached in the
    `new_high_lookback_snapshots` before the growth began (so recurring background
    topics do not qualify), across at least `min_issuers` issuers. A topic that was
    already discussed by more than `max_issuer_share` of the issuers with text when
    its growth began is generic (boilerplate every company repeats) and does not
    qualify. Breadth is judged at the start of growth, not at the flag: a theme that
    spreads quickly to most companies (AI in 2023) is an emerging theme, not generic.
    The cap applies only when at least 2 x min_issuers issuers have text; with fewer,
    min_issuers and the cap could not both be met.

    Only information at or before each snapshot is used: exposures from windows
    ending at earlier snapshots and market affinity estimated up to the snapshot.
    """
    m, k = cfg.min_consecutive_growth_windows, cfg.growth_step_snapshots
    series = topic_series(exposure, cfg.min_exposure, window_totals)
    snaps = sorted(snapshots)
    rows = []
    for tid, s in series.groupby("track_id"):
        s = s.set_index("t").reindex(snaps)
        # A window without the track means the topic had no passages: share 0.
        smooth = s["share"].fillna(0.0).rolling(k, min_periods=k).mean()
        for i, t in enumerate(snaps):
            if pd.isna(s.loc[t, "n_passages"]) or i < m * k + k - 1:
                continue
            if warmup_until is not None and snaps[i - m * k - k + 1] <= warmup_until:
                continue  # the earliest window used was still filling up
            steps = smooth.iloc[[i - j * k for j in range(m, -1, -1)]].to_numpy()
            if not (np.diff(steps) > 0).all():
                continue
            if steps[0] > 0 and steps[-1] / steps[0] < cfg.min_growth_ratio:
                continue
            g0 = i - m * k
            prior = smooth.iloc[max(0, g0 - cfg.new_high_lookback_snapshots):g0].dropna()
            if len(prior) and steps[-1] <= prior.max():
                continue  # back to an old level: a recurring topic, not an emerging one
            if s.loc[t, "n_issuers"] < cfg.min_issuers:
                continue
            t0 = snaps[g0]
            if active_issuers is not None and t0 in active_issuers.index and \
                    active_issuers[t0] >= 2 * cfg.min_issuers and \
                    np.nan_to_num(s.loc[t0, "n_issuers"]) > \
                    cfg.max_issuer_share * active_issuers[t0]:
                continue  # generic: already broad before it grew
            e = exposure.loc[(exposure["track_id"] == tid) & (exposure["t"] == t)]
            issuers = sorted(e.loc[e["exposure"] >= cfg.min_exposure, "symbol"])
            segs = sorted({segments.get(x, "?") for x in issuers})
            rows.append({
                "track_id": int(tid), "t": t, "issuers": issuers,
                "n_issuers": len(issuers), "n_passages": int(s.loc[t, "n_passages"]),
                "share": float(s.loc[t, "share"]),
                "segments": segs, "single_segment": len(segs) < 2,
                **_alignment(issuers, t, snaps, market_aff, cfg, segments, rng_seed,
                             null_draws),
            })
    ledger = pd.DataFrame(rows)
    if ledger.empty:
        return ledger
    first = ledger.groupby("track_id")["t"].transform("min")
    ledger["t_first_flag"] = first
    ledger["candidate_id"] = ledger["track_id"].map(lambda x: f"cand-{x:05d}")
    return ledger.sort_values(["t", "track_id"]).reset_index(drop=True)


def _alignment(issuers, t, snaps, market_aff, cfg, segments, seed, draws) -> dict:
    aff = market_aff.get(t)
    now = mean_pair_affinity(aff, issuers)
    i = snaps.index(t)
    base_ts = snaps[max(0, i - cfg.market_alignment_baseline_snapshots):i]
    base = [mean_pair_affinity(market_aff.get(b), issuers) for b in base_ts]
    base = [b for b in base if np.isfinite(b)]
    delta = now - np.mean(base) if base and np.isfinite(now) else float("nan")
    pct = float("nan")
    if aff is not None and np.isfinite(now):
        pool = [s for s in aff.index]
        k = len([s for s in issuers if s in aff.index])
        if k >= 2 and len(pool) > k:
            rng = np.random.default_rng([seed, int(pd.Timestamp(t).value // 10**9)])
            null = [mean_pair_affinity(aff, list(rng.choice(pool, k, replace=False)))
                    for _ in range(draws)]
            pct = midrank_pct(np.array(null), now)
    return {"market_affinity": now, "market_affinity_delta": delta,
            "market_affinity_null_pct": pct}


def midrank_pct(null: np.ndarray, x: float) -> float:
    """Share of the null below x, counting ties as half (0.5 = no difference)."""
    null = null[np.isfinite(null)]
    if not len(null) or not np.isfinite(x):
        return float("nan")
    return float(np.mean(null < x) + 0.5 * np.mean(null == x))


def write_ledger(ledger: pd.DataFrame, out_dir: Path) -> str:
    """Write the ledger and return its SHA-256. Written before any outcome is computed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if ledger.empty:
        ledger = pd.DataFrame(columns=["candidate_id", "track_id", "t", "t_first_flag",
                                       "issuers"])
    path = out_dir / "candidate_ledger.parquet"
    ledger.to_parquet(path, index=False)
    canon = ledger.assign(t=ledger["t"].astype(str),
                          t_first_flag=ledger["t_first_flag"].astype(str)).to_json(
        orient="records", date_format="iso")
    digest = hashlib.sha256(canon.encode()).hexdigest()
    (out_dir / "candidate_ledger.sha256.json").write_text(
        json.dumps({"sha256": digest, "rows": len(ledger)}, indent=1))
    return digest
