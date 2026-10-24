"""Anchored topics: stable identities across months and refits, new topics from
unclaimed passages, and no use of text published after a snapshot."""

import numpy as np
import pandas as pd

from themegraph.config import load_pipeline
from themegraph.text.anchored import anchored_windows

SNAPS = list(pd.date_range("2018-01-31", periods=30, freq="ME", tz="UTC"))
DIM = 24


def _cfg(**kw):
    base = {"min_cluster_passages": 4, "min_cluster_issuers": 2, "cluster_distance": 0.5,
            "center_by_genre": False, "window_days": 92, "anchor_refit_snapshots": 12,
            "anchor_lookback_days": 365, "anchor_assign_similarity": 0.5,
            "anchor_match_similarity": 0.7}
    return load_pipeline().text.model_copy(update={**base, **kw})


def _corpus(new_from: int = 10, seed: int = 3):
    """Three subjects every month; a fourth appears at snapshot `new_from`."""
    rng = np.random.default_rng(seed)
    rows, vecs = [], []
    for i, t in enumerate(SNAPS):
        for subj in range(4):
            if subj == 3 and i < new_from:
                continue
            for k in range(6):
                v = np.zeros(DIM)
                v[subj] = 1.0
                v += rng.normal(0, 0.08, DIM)
                rows.append({"passage_id": f"p{i}-{subj}-{k}", "symbol": f"S{subj}{k % 3}",
                             "genre": "transcript:qa", "subject": subj,
                             "available_at": t - pd.Timedelta(days=5)})
                vecs.append(v)
    return pd.DataFrame(rows), np.array(vecs, np.float32)


def _ids_by_subject(windows, p):
    subj = dict(zip(p["passage_id"], p["subject"], strict=True))
    out: dict[int, set[int]] = {s: set() for s in range(4)}
    for wc in windows:
        for pid, lab in zip(wc.passage_ids, wc.labels, strict=True):
            if lab >= 0:
                out[subj[pid]].add(int(lab))
    return out


def test_topics_keep_their_identity_across_months_and_refits():
    p, V = _corpus()
    windows, model = anchored_windows(p, V, SNAPS, _cfg())
    ids = _ids_by_subject(windows, p)
    assert all(len(ids[s]) == 1 for s in range(4)), ids  # one identity each, 30 months
    assert len({next(iter(ids[s])) for s in range(4)}) == 4  # and distinct
    stats = model.stats(window_span=3)
    assert stats["tracks"] == 4 and stats["median_track_life_windows"] >= 20


def test_new_subject_found_between_refits_and_kept_after():
    p, V = _corpus(new_from=15)  # appears after the refit at snapshot 12
    windows, model = anchored_windows(p, V, SNAPS, _cfg())
    (tid,) = _ids_by_subject(windows, p)[3]
    births = model.events_frame().query("event == 'birth' and track_id == @tid")
    assert births["t"].iloc[0] == SNAPS[15]  # found the month it appeared
    assert tid in windows[-1].centroids  # same identity after the refit at 24


def test_no_text_after_the_snapshot_is_used():
    p, V = _corpus()
    cut = 18
    early = p["available_at"] <= SNAPS[cut]
    full, _ = anchored_windows(p, V, SNAPS[:cut + 1], _cfg())
    trunc, _ = anchored_windows(p.loc[early].reset_index(drop=True), V[early.to_numpy()],
                                SNAPS[:cut + 1], _cfg())
    for a, b in zip(full, trunc, strict=True):
        assert a.passage_ids == b.passage_ids and a.labels.tolist() == b.labels.tolist()
