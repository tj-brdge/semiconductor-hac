"""Forward log: observed-basis inputs, append-only idempotent predictions, and
outcomes that never rewrite predictions."""

import datetime as dt
import hashlib

import pandas as pd
import pytest

from themegraph import forward as Fw
from themegraph import synthetic as S
from themegraph.text.embed import HashingEmbedder

SPEC = {"code": "c", "config": "k", "environment": "e"}


def _data():
    u = S.synthetic_universe(per_segment=3)
    prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
    return u, prices.assign(first_seen_at=prices["available_at"]), \
        docs.assign(first_seen_at=docs["available_at"])


def test_observed_basis_excludes_rows_downloaded_after_cutoff():
    cut = pd.Timestamp("2019-06-30 23:59:59", tz="UTC")
    df = pd.DataFrame({"available_at": [cut - pd.Timedelta(days=5)] * 2,
                       "first_seen_at": [cut - pd.Timedelta(days=1), cut + pd.Timedelta(days=9)]})
    kept, basis = Fw.observed(df, cut)
    assert len(kept) == 1 and basis == "observed"
    assert Fw.observed(df.drop(columns="first_seen_at"), cut)[1] == "reconstructed"


def test_predictions_are_append_only_idempotent_and_immutable(cfg, tmp_path):
    cfg = S.demo_config(cfg)
    u, prices, docs = _data()
    cut = dt.date(2019, 9, 30)
    args = (cfg, u, HashingEmbedder(256), tmp_path / "c", tmp_path / "fw", cut)
    first = Fw.predict(prices, docs, *args, spec=SPEC, log=lambda *x: None)
    log = tmp_path / "fw" / Fw.PREDICTIONS
    before = log.read_bytes()
    again = Fw.predict(prices, docs, *args, spec=SPEC, log=lambda *x: None)
    assert [r["prediction_id"] for r in again] == [r["prediction_id"] for r in first]
    assert log.read_bytes() == before  # nothing appended
    changed = docs.iloc[5:]            # drops pre-cutoff documents: different inputs
    with pytest.raises(Fw.ForwardLogError, match="immutable"):
        Fw.predict(prices, changed, *args, spec=SPEC, log=lambda *x: None)
    assert all(r["snapshot"].startswith("2019-09-30") for r in first)
    assert all(r["availability_basis"] == {"prices": "observed", "documents": "observed"}
               for r in first)

    # Outcomes only for elapsed horizons; predictions never rewritten.
    closes = prices.pivot_table(index="date", columns="symbol", values="close")
    closes.index = pd.DatetimeIndex(closes.index).tz_localize("UTC") + pd.Timedelta(hours=22)
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    early = Fw.evaluate_outcomes(tmp_path / "fw", closes, "SOXX", [63, 2000],
                                 closes.index.max())
    assert early and {o["h"] for o in early} == {63}  # 2000 sessions not yet elapsed
    assert Fw.evaluate_outcomes(tmp_path / "fw", closes, "SOXX", [63, 2000],
                                closes.index.max()) == []  # idempotent
    assert hashlib.sha256(log.read_bytes()).hexdigest() == digest
