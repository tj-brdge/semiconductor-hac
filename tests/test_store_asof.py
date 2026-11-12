"""As-of reads across vintages: refresh replay, partial batches, revisions, and
order-independent input fingerprints."""

import pandas as pd

from themegraph import pipeline as P
from themegraph.data.store import Store

D = pd.Timestamp("2019-06-03", tz="UTC")


def _prices(rows):
    return pd.DataFrame([{"symbol": s, "date": d, "close": c, "published_at": d,
                          "available_at": d + pd.Timedelta(hours=22),
                          "first_seen_at": pd.Timestamp.now(tz="UTC")}
                         for s, d, c in rows])


def _docs(rows):
    return pd.DataFrame([{"symbol": s, "doc_id": i, "doc_type": "8-K", "text": txt,
                          "published_at": D, "available_at": D,
                          "first_seen_at": pd.Timestamp.now(tz="UTC")}
                         for s, i, txt in rows])


def test_refresh_keeps_first_seen_and_replay_sees_what_was_available(tmp_path):
    st = Store(tmp_path)
    st.write("prices", _prices([("A", D, 10.0), ("B", D, 20.0)]), "20190610T000000Z")
    # Refresh a month later: A unchanged, B revised, C new.
    st.write("prices", _prices([("A", D, 10.0), ("B", D, 21.0), ("C", D, 5.0)]),
             "20190710T000000Z")
    now = st.read("prices").set_index("symbol")
    assert now.loc["A", "first_seen_at"] == pd.Timestamp("2019-06-10", tz="UTC")  # kept
    assert now.loc["B", "close"] == 21.0
    assert now.loc["B", "first_seen_at"] == pd.Timestamp("2019-07-10", tz="UTC")  # not backdated
    assert now.loc["C", "first_seen_at"] == pd.Timestamp("2019-07-10", tz="UTC")
    # Replaying a June cutoff after the refresh: June's data, June's values.
    june = st.read("prices", upto="20190630T235959Z").set_index("symbol")
    assert set(june.index) == {"A", "B"} and june.loc["B", "close"] == 20.0
    assert st.used["prices"] == ["20190610T000000Z"]


def test_partial_batch_does_not_hide_other_records(tmp_path):
    st = Store(tmp_path)
    st.write("documents_edgar", _docs([("A", "a1", "x"), ("B", "b1", "y")]),
             "20190610T000000Z")
    st.write("documents_edgar", _docs([("A", "a2", "z")]), "20190710T000000Z")  # A only
    assert set(st.read("documents_edgar")["doc_id"]) == {"a1", "a2", "b1"}


def test_input_fingerprint_is_order_independent_and_covers_metadata():
    docs = _docs([("A", "a1", "x"), ("B", "b1", "y")])
    h = P.input_bindings(docs.iloc[:0], docs, observed=False)["documents"]
    assert P.input_bindings(docs.iloc[:0], docs.iloc[::-1], observed=False)["documents"] == h
    moved = docs.assign(symbol=["B", "A"])  # same text, attributed to other issuers
    assert P.input_bindings(docs.iloc[:0], moved, observed=False)["documents"] != h
    later = docs.assign(available_at=docs["available_at"] + pd.Timedelta(days=1))
    assert P.input_bindings(docs.iloc[:0], later, observed=False)["documents"] != h
    # first_seen_at matters only on the observed (forward) basis
    seen = docs.assign(first_seen_at=docs["first_seen_at"] + pd.Timedelta(days=1))
    assert P.input_bindings(docs.iloc[:0], seen, observed=False)["documents"] == h
    assert P.input_bindings(docs.iloc[:0], seen, observed=True)["documents"] != \
        P.input_bindings(docs.iloc[:0], docs, observed=True)["documents"]


def _reference_as_of(df, key):
    """The previous implementation: every vintage loaded and concatenated."""
    from themegraph.data.store import NOT_CONTENT, vintage_time
    content = sorted(c for c in df.columns if c not in NOT_CONTENT)
    h = pd.util.hash_pandas_object(df[content].astype(str), index=False)
    seen = df["vintage"].map(vintage_time)
    first = seen.groupby([df[k] for k in key] + [h]).transform("min")
    out = df.assign(first_seen_at=first, _seen=seen).sort_values("_seen", kind="stable")
    out = out.drop_duplicates(key, keep="last").drop(columns="_seen")
    return out.sort_index().reset_index(drop=True)


def test_index_based_read_matches_loading_every_vintage(tmp_path):
    import numpy as np
    rng = np.random.default_rng(0)
    st = Store(tmp_path)
    vs = [f"2019{m:02d}10T000000Z" for m in range(6, 12)]
    for n, v in enumerate(vs):  # overlapping batches: unchanged, revised and new documents
        ids = rng.choice(40, size=25, replace=False)
        st.write("documents_edgar", _docs([(f"S{i % 4}", f"d{i}", f"text {i} rev "
                                            f"{int(rng.integers(0, 2)) * n}") for i in ids]), v)
    got = st.read("documents_edgar")
    raw = pd.concat([pd.read_parquet(tmp_path / "documents_edgar" / f"{v}.parquet")
                     .assign(vintage=v) for v in vs], ignore_index=True)
    want = _reference_as_of(raw, ["doc_id"])
    cols = ["doc_id", "symbol", "text", "vintage", "first_seen_at"]
    pd.testing.assert_frame_equal(got[cols].sort_values("doc_id").reset_index(drop=True),
                                  want[cols].sort_values("doc_id").reset_index(drop=True))
    # A second read uses the stored indexes and gives the same answer; a symbol
    # filter gives exactly that symbol's rows.
    pd.testing.assert_frame_equal(st.read("documents_edgar"), got)
    one = st.read("documents_edgar", symbols={"S1"})
    assert set(one["doc_id"]) == set(got.loc[got["symbol"] == "S1", "doc_id"])
    upto = st.read("documents_edgar", upto=vs[2])
    assert set(upto["vintage"]) <= set(vs[:3])


def test_a_symbols_price_history_is_never_stitched_from_two_downloads(tmp_path):
    """Before: a later download that starts a day later kept day 1 from the older
    one, on a different adjustment basis (a fake halving here)."""
    st = Store(tmp_path)
    d = [D + pd.Timedelta(days=i) for i in range(3)]
    st.write("prices", _prices([("A", d[0], 100.0), ("A", d[1], 101.0), ("A", d[2], 102.0),
                                ("B", d[0], 7.0)]), "20190610T000000Z")
    # After a 2:1 split the provider restates A; this download starts one day later.
    st.write("prices", _prices([("A", d[1], 50.5), ("A", d[2], 51.0)]), "20190710T000000Z")
    now = st.read("prices")
    a = now.loc[now["symbol"] == "A"].sort_values("date")
    assert a["close"].tolist() == [50.5, 51.0] and set(a["vintage"]) == {"20190710T000000Z"}
    assert now.loc[now["symbol"] == "B", "close"].tolist() == [7.0]  # B: still there
