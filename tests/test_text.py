import numpy as np
import pandas as pd

from themegraph.text.cluster import WindowClusters
from themegraph.text.embed import EmbeddingCache, HashingEmbedder
from themegraph.text.keywords import ctfidf_keywords
from themegraph.text.novelty import novelty_mask
from themegraph.text.passages import (
    chunk_words,
    drop_exact_duplicates,
    passages_from_documents,
    transcript_turns,
)
from themegraph.text.track import Tracker, window_exposure


def test_transcript_turns_and_qa():
    text = ("Operator: Good day.\nJane Doe: Revenue rose on data center demand.\n"
            "Operator: We will now begin the question-and-answer session.\n"
            "Analyst One: What about HBM?\nJane Doe: Supply is tight.")
    turns = transcript_turns(text)
    assert [t[1] for t in turns] == ["prepared", "prepared", "prepared", "qa", "qa"]
    assert turns[3][0] == "Analyst One"


def test_chunking_merges_short_tail():
    chunks = chunk_words(" ".join(["w"] * 750), 350, 60)
    assert [len(c.split()) for c in chunks] == [350, 400]
    assert chunk_words("too short", 350, 40) == []


def _docs():
    t = pd.Timestamp("2020-01-01", tz="UTC")
    return pd.DataFrame({
        "symbol": ["A", "A"], "doc_id": ["d1", "d2"], "doc_type": ["transcript"] * 2,
        "published_at": [t, t + pd.Timedelta(days=90)],
        "available_at": [t, t + pd.Timedelta(days=90)],
        "text": ["CEO: " + "hbm capacity sold out " * 20, "CEO: " + "hbm capacity sold out " * 20],
    })


def test_exact_duplicates_keep_first():
    p = drop_exact_duplicates(passages_from_documents(_docs(), 350, 10))
    assert p["doc_id"].tolist() == ["d1"]


def test_novelty_only_compares_earlier_text():
    p = passages_from_documents(_docs(), 350, 10)
    V = HashingEmbedder(256).embed(p["text"].tolist())
    keep = novelty_mask(p, V, 0.95)
    assert keep.tolist() == [True, False]


def test_embedding_cache_reuses_vectors(tmp_path):
    class Counting(HashingEmbedder):
        calls = 0

        def embed(self, texts):
            Counting.calls += len(texts)
            return super().embed(texts)

    c = EmbeddingCache(tmp_path, Counting(64))
    c.get(["h1", "h2"], ["a b", "c d"])
    c2 = EmbeddingCache(tmp_path, Counting(64))
    v = c2.get(["h2", "h1", "h3"], ["c d", "a b", "e f"])
    assert Counting.calls == 3 and v.shape == (3, 64)


def _wc(t, centroids):
    return WindowClusters(pd.Timestamp(t, tz="UTC"), [], np.array([]), np.array([]),
                          {i: np.asarray(c, float) / np.linalg.norm(c)
                           for i, c in enumerate(centroids)})


def test_tracker_continue_split_merge_birth():
    tr = Tracker(0.8)
    a1 = tr.step(_wc("2020-01-31", [[1, 0, 0], [0, 1, 0]]))
    a2 = tr.step(_wc("2020-02-29", [[1, 0.05, 0], [0.97, 0, 0.2], [0, 0, 1]]))
    assert a2[0] == a1[0]                       # continued
    assert tr.tracks[a2[1]].parent == a1[0]     # split from track 0
    assert a2[2] not in a1.values()             # birth
    a3 = tr.step(_wc("2020-03-31", [[1, 0, 0.1]]))  # close to both 0 and its split
    pair = {a1[0], a2[1]}
    assert a3[0] in pair                            # the closer one continues...
    other = (pair - {a3[0]}).pop()
    assert tr.tracks[other].merged_into == a3[0]    # ...and absorbs the other
    assert tr.tracks[a2[2]].ended is None           # unseen once: within the gap
    tr.step(_wc("2020-04-30", [[1, 0, 0.1]]))
    assert tr.tracks[a2[2]].ended is not None       # unseen twice: ended


def test_window_exposure_shares():
    wc = WindowClusters(pd.Timestamp("2020-01-31", tz="UTC"), ["p1", "p2", "p3", "p4"],
                        np.array(["A", "A", "B", "B"]), np.array([0, -1, 0, 0]), {0: None})
    ex = window_exposure(wc, {0: 7}).set_index("symbol")
    assert ex.loc["A", "exposure"] == 0.5 and ex.loc["B", "exposure"] == 1.0
    assert set(ex["track_id"]) == {7}


def test_ctfidf_distinguishes_topics():
    kw = ctfidf_keywords({1: "hbm hbm dram memory", 2: "inverter silicon carbide ev"}, 2)
    assert "hbm" in kw[1] and "hbm" not in kw[2]
