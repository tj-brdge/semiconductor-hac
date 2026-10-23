"""Window clustering: cache identity, degenerate inputs, serial/parallel equivalence."""

import numpy as np
import pandas as pd

from themegraph.config import load_pipeline
from themegraph.text.cluster import (
    ClusterCache,
    WindowClusters,
    cluster_subset,
    remove_common_directions,
)

T = pd.Timestamp("2020-01-31", tz="UTC")


def _cfg(**kw):
    base = {"min_cluster_passages": 2, "min_cluster_issuers": 1, "cluster_distance": 0.5,
            "center_by_genre": False}
    return load_pipeline().text.model_copy(update={**base, **kw})


def test_cache_round_trip_and_metadata_checks(tmp_path):
    cfg = _cfg()
    c = ClusterCache(tmp_path, "m", cfg, 1)
    V = np.array([[1.0, 0.0], [0.0, 1.0]], np.float32)
    wc = WindowClusters(T, ["a", "b"], np.array(["X", "Y"]), np.array([0, -1]),
                        {0: np.array([1.0, 0.0])})
    k = c.key(["a", "b"], ["X", "Y"], ["g", "g"], V)
    assert c.get(k, T, ["a", "b"], ["X", "Y"], 2) is None
    c.put(k, wc)
    back = c.get(k, T, ["a", "b"], ["X", "Y"], 2)
    assert back.labels.tolist() == [0, -1] and np.allclose(back.centroids[0], [1, 0])
    assert ClusterCache(tmp_path, "other-model", cfg, 1).key(["a", "b"], ["X", "Y"],
                                                             ["g", "g"], V) != k
    assert c.get(k, T, ["a", "b", "c"], ["X", "Y", "Z"], 2) is None  # wrong shape
    assert c.get(k, T, ["a", "b"], ["X", "Y"], 3) is None            # wrong dimension


def test_cache_keys_on_vector_content_issuers_and_genres(tmp_path):
    a, b = [1.0, 0, 0], [0, 1.0, 0]
    cfg = _cfg()
    ids, syms, genres = np.array([f"d#{i}" for i in range(6)]), np.array(list("ABCDEF")), \
        np.array(["g"] * 6)
    V1 = np.array([a, a, a, b, b, b], np.float32)
    V2 = np.array([a, b, a, b, a, b], np.float32)
    c = ClusterCache(tmp_path, "m", cfg, 0)
    k1 = c.key(ids, syms, genres, V1)
    c.put(k1, cluster_subset(T, ids, syms, V1, cfg))
    assert c.key(ids, syms, genres, V1) == k1                  # unchanged: reuse
    k2 = c.key(ids, syms, genres, V2)
    assert k2 != k1 and c.get(k2, T, ids, syms, 3) is None     # same IDs, new vectors
    assert c.key(ids, syms[::-1], genres, V1) != k1            # issuer attribution
    assert c.key(ids, syms, np.array(["h"] * 6), V1) != k1     # genre attribution
    assert cluster_subset(T, ids, syms, V2, cfg).labels.tolist() == [0, 1, 0, 1, 0, 1]


def test_singleton_genre_is_centred_on_window_mean():
    V = np.random.default_rng(0).normal(size=(4, 8)).astype(np.float32)
    genres = np.array(["g1", "g1", "g1", "g2"])
    X, valid = remove_common_directions(V, 0, genres=genres)
    assert valid.all() and np.isfinite(X).all()
    wc = cluster_subset(T, np.array(list("abcd")), np.array(list("ABCD")), V,
                        _cfg(center_by_genre=True), 0, genres)
    assert len(wc.labels) == 4 and wc.excluded == 0


def test_identical_vectors_are_excluded_and_counted_not_jittered():
    rng = np.random.default_rng(1)
    V = np.vstack([np.tile(rng.normal(size=8), (3, 1)), rng.normal(size=(3, 8))])
    genres = np.array(["x"] * 3 + ["y"] * 3)
    wc = cluster_subset(T, np.array(list("abcdef")), np.array(list("ABCDEF")),
                        V.astype(np.float32), _cfg(center_by_genre=True), 0, genres)
    assert wc.excluded == 3 and (wc.labels[:3] == -1).all()
    assert len(wc.labels) == 6  # alignment with passages preserved


def test_zero_embedding_rows_are_excluded():
    V = np.array([[1, 0, 0], [0.9, 0.1, 0], [0, 0, 0], [0, 1, 0], [0.1, 0.9, 0]], np.float32)
    wc = cluster_subset(T, np.array(list("abcde")), np.array(list("ABCDE")), V, _cfg(), 0)
    assert wc.excluded == 1 and wc.labels[2] == -1
