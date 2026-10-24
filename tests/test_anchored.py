"""Anchored topics: stable identities across months and refits, new topics from
unclaimed passages, and no use of text published after a snapshot."""

import numpy as np
import pandas as pd

from themegraph.config import load_pipeline
from themegraph.graph.candidates import detect
from themegraph.text.anchored import anchored_windows, match_identities
from themegraph.text.track import window_exposure

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


# -- refit identity matching -----------------------------------------------------


def test_matching_never_trades_a_valid_continuation_for_invalid_pairs():
    S = np.array([[0.75, 0.65], [0.69, 0.00]])
    assert match_identities(S, 0.70) == [(0, 0)]


def test_matching_prefers_more_continuations_then_similarity():
    # One 0.95 match versus two 0.90 matches: keep both identities.
    assert sorted(match_identities(np.array([[0.95, 0.90], [0.90, 0.0]]), 0.7)) == \
        [(0, 1), (1, 0)]
    # Unequal counts: three new topics, two old; the best valid pairs win.
    S = np.array([[0.72, 0.10], [0.95, 0.80], [0.10, 0.20]])
    assert sorted(match_identities(S, 0.7)) == [(0, 0), (1, 1)]
    assert match_identities(np.array([[0.2, 0.6], [0.5, 0.1]]), 0.7) == []
    assert match_identities(np.zeros((0, 3)), 0.7) == []


# -- lineage and recurring fragments ----------------------------------------------

LONG = list(pd.date_range("2016-01-31", periods=48, freq="ME", tz="UTC"))


def _unit(*pairs):
    v = np.zeros(DIM)
    for i, w in pairs:
        v[i] = w
    return v / np.linalg.norm(v)


def _scenario(subjects, seed=5):
    """subjects: name -> fn(month) -> (count, n_issuers, direction) or None."""
    rng = np.random.default_rng(seed)
    rows, vecs = [], []
    for i, t in enumerate(LONG):
        for name, fn in subjects.items():
            spec = fn(i)
            if spec is None:
                continue
            n, k, d = spec
            for j in range(n):
                rows.append({"passage_id": f"{name}-{i}-{j}", "symbol": f"{name}{j % k}",
                             "genre": "transcript:qa", "subject": name,
                             "available_at": t - pd.Timedelta(days=5)})
                vecs.append(d + rng.normal(0, 0.05, DIM))
    return pd.DataFrame(rows), np.array(vecs, np.float32)


def _long_cfg():
    return _cfg(anchor_refit_snapshots=36, anchor_lookback_days=92)


BACKGROUND = {"B": lambda i: (8, 4, _unit((1, 1))), "C": lambda i: (8, 4, _unit((2, 1)))}


def test_refit_split_records_parent_and_fragment_is_not_a_new_theme(cfg):
    # Subject A for 32 months, then the same volume splits into two sub-subjects.
    split = {**BACKGROUND,
             "A": lambda i: (8, 4, _unit((0, 1))) if i < 32 else None,
             "P": lambda i: (4, 4, _unit((0, 1), (5, 0.8))) if i >= 32 else None,
             "Q": lambda i: (4, 4, _unit((0, 1), (5, -0.8))) if i >= 32 else None}
    p, V = _scenario(split)
    windows, model = anchored_windows(p, V, LONG, _long_cfg(), history_snapshots=32,
                                      min_exposure=cfg.candidates.min_exposure)
    ev = model.events_frame()
    a_id = _ids_by_subject_named(windows, p)["A"]
    splits = ev.loc[ev["event"] == "split"]
    assert len(splits) == 1 and splits["other"].iloc[0] in a_id  # parent is A's topic
    fragment = int(splits["track_id"].iloc[0])
    r = model.refits_frame().iloc[-1]
    assert r["continued"] >= 3 and r["splits"] == 1

    ex = pd.concat([window_exposure(wc, {c: c for c in wc.centroids}) for wc in windows])
    totals = pd.Series({wc.t: len(wc.labels) for wc in windows})
    naive = detect(ex, LONG, cfg.candidates, {}, {}, window_totals=totals)
    assert fragment in set(naive["track_id"])  # without history it looks brand new
    led = detect(ex, LONG, cfg.candidates, {}, {}, window_totals=totals,
                 history=model.history_frame())
    assert fragment not in set(led.get("track_id", []))


def test_genuinely_new_subject_stays_discoverable_with_history(cfg):
    # N appears at month 34 and grows in volume and companies.
    grow = {**BACKGROUND,
            "N": lambda i: (2 + 2 * (i - 34), min(8, 3 + (i - 34) // 2), _unit((3, 1)))
            if i >= 34 else None}
    p, V = _scenario(grow)
    windows, model = anchored_windows(p, V, LONG, _long_cfg(), history_snapshots=32,
                                      min_exposure=cfg.candidates.min_exposure)
    (n_id,) = _ids_by_subject_named(windows, p)["N"]
    ex = pd.concat([window_exposure(wc, {c: c for c in wc.centroids}) for wc in windows])
    totals = pd.Series({wc.t: len(wc.labels) for wc in windows})
    led = detect(ex, LONG, cfg.candidates, {}, {}, window_totals=totals,
                 history=model.history_frame())
    assert n_id in set(led["track_id"])


def test_refit_merge_is_recorded():
    # Two sub-subjects for 32 months, then one subject between them.
    merge = {**BACKGROUND,
             "P": lambda i: (4, 4, _unit((0, 1), (5, 0.8))) if i < 32 else None,
             "Q": lambda i: (4, 4, _unit((0, 1), (5, -0.8))) if i < 32 else None,
             "A": lambda i: (8, 4, _unit((0, 1))) if i >= 32 else None}
    p, V = _scenario(merge)
    _, model = anchored_windows(p, V, LONG, _long_cfg())
    ev = model.events_frame()
    merged = ev.loc[ev["event"] == "merge"]
    assert len(merged) == 1
    r = model.refits_frame().iloc[-1]
    assert r["merged"] == 1 and r["births"] == 0


def _ids_by_subject_named(windows, p):
    subj = dict(zip(p["passage_id"], p["subject"], strict=True))
    out: dict[str, set[int]] = {}
    for wc in windows:
        for pid, lab in zip(wc.passage_ids, wc.labels, strict=True):
            if lab >= 0:
                out.setdefault(subj[pid], set()).add(int(lab))
    return out
