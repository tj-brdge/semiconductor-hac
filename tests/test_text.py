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


def test_embedding_cache_keeps_progress_when_a_later_chunk_fails(tmp_path):
    class FailsSecondCall(HashingEmbedder):
        calls = 0

        def embed(self, texts):
            FailsSecondCall.calls += 1
            if FailsSecondCall.calls == 2:
                raise RuntimeError("quota")
            return super().embed(texts)

    cache = EmbeddingCache(tmp_path, FailsSecondCall(32))
    cache.save_every = 2
    hashes, texts = ["a", "b", "c", "d"], ["w1 x", "w2 y", "w3 z", "w4 q"]
    try:
        cache.get(hashes, texts)
    except RuntimeError:
        pass
    reloaded = EmbeddingCache(tmp_path, HashingEmbedder(32))
    assert set(reloaded._mem) == {"a", "b"}


def test_voyage_error_includes_api_detail_not_key():
    import httpx

    from themegraph.text.embed import VoyageEmbedder

    def handler(request):
        return httpx.Response(403, json={"detail": "Model not available for this plan"})

    client = httpx.Client(transport=httpx.MockTransport(handler),
                          headers={"Authorization": "Bearer sk-secret"})
    emb = VoyageEmbedder("voyage-finance-2", client=client)
    try:
        emb.embed(["hello"])
        raise AssertionError("expected failure")
    except RuntimeError as e:
        msg = str(e)
    assert "Model not available for this plan" in msg and "403" in msg
    assert "sk-secret" not in msg and "payment method" in msg


def test_voyage_endpoint_choice_and_wrong_key_hint():
    import httpx

    from themegraph.text.embed import VoyageEmbedder

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(403, json={"detail": "This API key cannot access this endpoint."})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    emb = VoyageEmbedder("voyage-finance-2", client=client, endpoint="voyage")
    try:
        emb.embed(["hello"])
        raise AssertionError("expected failure")
    except RuntimeError as e:
        assert "endpoint: mongodb" in str(e)
    assert VoyageEmbedder("m", client=client, endpoint="mongodb").url.startswith(
        "https://ai.mongodb.com/")
    assert seen[0].startswith("https://api.voyageai.com/")


def test_substantive_mask_drops_tables_and_statement_boilerplate():
    from themegraph.text.passages import substantive_mask

    p = pd.DataFrame({"text": [
        "demand for high bandwidth memory from ai servers keeps rising across customers",
        "revenue 1,234 2,345 (12) 3.4% 5,678 9,012 11.2% 345 678 901 234 567 890",
        "for the three months ended march non-gaap net income per diluted share was",
    ]})
    assert substantive_mask(p, 0.35, True).tolist() == [True, False, False]
    guidance = pd.DataFrame({"text": [
        "We expect revenue of $4.2 billion to $4.4 billion, gross margin of 45% to 47%, "
        "and HBM bit shipments up about 50% sequentially as AI server demand stays strong"]})
    assert substantive_mask(guidance, 0.35, True).all()  # operational evidence is kept


def test_genre_centering_removes_register_not_subject():
    from themegraph.text.cluster import remove_common_directions

    rng = np.random.default_rng(0)
    subject = np.repeat(np.eye(4)[:2], 10, axis=0)          # two subjects
    register = np.tile(np.array([[0, 0, 5, 0], [0, 0, 0, 5]]), (10, 1))  # two genres
    genres = np.tile(np.array(["qa", "prepared"]), 10)
    X, valid = remove_common_directions(subject + register + 0.01 * rng.normal(size=(20, 4)),
                                        0, genres=genres)
    assert valid.all()
    same_subject = X[0] @ X[2]   # same subject, same genre
    same_genre_other_subject = X[0] @ X[10]
    assert same_subject > 0.9 and same_genre_other_subject < 0


def test_overlap_alone_cannot_chain_unrelated_subjects():
    tr = Tracker(link_similarity=0.8, link_overlap=0.3, link_min_similarity=0.5)
    ids = [f"p{i}" for i in range(6)]
    a1 = tr.step(WindowClusters(pd.Timestamp("2020-01-31", tz="UTC"), ids,
                                np.array(list("AABBCC")), np.zeros(6, int),
                                {0: np.array([1.0, 0, 0])}))
    a2 = tr.step(WindowClusters(pd.Timestamp("2020-02-29", tz="UTC"), ids[:3],
                                np.array(list("AAB")), np.zeros(3, int),
                                {0: np.array([0.0, 1.0, 0])}))  # shares passages, orthogonal
    assert a2[0] != a1[0]
    stats = tr.stats(window_span=6)
    assert stats["median_life_beyond_window_span"] == 0


def test_tracker_links_by_shared_passages_when_centroid_drifts():
    tr = Tracker(link_similarity=0.99, link_overlap=0.3)
    ids = [f"p{i}" for i in range(6)]
    w1 = WindowClusters(pd.Timestamp("2020-01-31", tz="UTC"), ids, np.array(list("AABBCC")),
                        np.zeros(6, int), {0: np.array([1.0, 0, 0])})
    a1 = tr.step(w1)
    w2 = WindowClusters(pd.Timestamp("2020-02-29", tz="UTC"), ids[2:] + ["p9"],
                        np.array(list("BBCCD")), np.zeros(5, int),
                        {0: np.array([0.6, 0.8, 0])})  # drifted well below 0.99
    a2 = tr.step(w2)
    assert a2[0] == a1[0]
    assert tr.stats()["tracks"] == 1
