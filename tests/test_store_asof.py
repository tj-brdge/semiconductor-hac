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
