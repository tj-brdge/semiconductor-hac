"""The pipeline must recover planted themes and stay quiet on planted noise."""

import pandas as pd

from themegraph import pipeline as P
from themegraph import synthetic as S
from themegraph.report.build import build_report
from themegraph.text.embed import HashingEmbedder


def test_synthetic_dev_and_holdout(cfg, tmp_path):
    cfg = S.demo_config(cfg)
    u = S.synthetic_universe()
    prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
    labels = S.synthetic_labels()

    dev = P.build(prices, docs, cfg, u, HashingEmbedder(1024), tmp_path / "c", "dev",
                  log=lambda *a: None)
    res = P.evaluate(dev, labels)
    m = res["matches"].set_index("theme")
    assert m.loc["planted_ai_buildout", "detected"]
    assert m.loc["planted_ai_buildout", "lead_days"] > 180
    assert res["q1"]["unmatched_candidates"] <= 1
    como = res["comovement"].set_index(["candidate_id", "h"])
    planted = m.loc["planted_ai_buildout", "candidate_id"]
    assert como.loc[(planted, 126), "null_pct"] > 0.9

    manifest = P.save(dev, res, tmp_path / "dev", {"synthetic": True})
    assert manifest["research_embedder"] is False and manifest["results_sha256"]
    html = build_report(tmp_path / "dev", u, "test").read_text()
    assert "planted_ai_buildout" in html

    hold = P.build(prices, docs, cfg, u, HashingEmbedder(1024), tmp_path / "c", "holdout",
                   log=lambda *a: None)
    m2 = P.evaluate(hold, labels)["matches"].set_index("theme")
    assert m2.loc["planted_power_semis", "detected"]

    # As-of explanations use only what was available at the flag: two more years of
    # documents (the holdout build) cannot change a development-period explanation.
    e_dev = dev.explanations.set_index("candidate_id")
    e_hold = hold.explanations.set_index("candidate_id")
    assert len(e_dev) and set(e_dev.index) <= set(e_hold.index)
    for cid, row in e_dev.iterrows():
        other = e_hold.loc[cid]
        assert row["t"] == other["t"] and list(row["keywords"]) == list(other["keywords"])
        assert [q["passage_id"] for q in row["quotes"]] == \
            [q["passage_id"] for q in other["quotes"]]
        assert all(pd.Timestamp(q["available_at"]) <= row["t"] for q in row["quotes"])
    # The retrospective label is marked as such.
    assert set(dev.topics["basis"]) == {"retrospective_peak_window"}


def test_build_ignores_data_after_period_end(cfg, tmp_path):
    cfg = S.demo_config(cfg)
    u = S.synthetic_universe(per_segment=3)
    prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
    a = P.build(prices, docs, cfg, u, HashingEmbedder(256), tmp_path / "a", "dev",
                log=lambda *x: None)
    end = a.end
    b = P.build(prices.loc[prices["available_at"] <= end], docs.loc[docs["available_at"] <= end],
                cfg, u, HashingEmbedder(256), tmp_path / "b", "dev", log=lambda *x: None)
    assert a.ledger.equals(b.ledger)
    assert a.exposure.equals(b.exposure)


def test_second_build_reuses_window_clusters(cfg, tmp_path):
    cfg = S.demo_config(cfg)
    u = S.synthetic_universe(per_segment=3)
    prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
    logs = []
    a = P.build(prices, docs, cfg, u, HashingEmbedder(256), tmp_path, "dev", log=logs.append)
    b = P.build(prices, docs, cfg, u, HashingEmbedder(256), tmp_path, "dev", log=logs.append)
    clustering = [m for m in logs if "clustering" in m]
    assert clustering[0].startswith("  clustering 42 windows")
    assert clustering[1].startswith("  clustering 0 windows (42 cached)")
    assert a.ledger.equals(b.ledger) and a.exposure.equals(b.exposure)


def test_serial_and_parallel_clustering_agree(cfg, tmp_path):
    cfg = S.demo_config(cfg)
    u = S.synthetic_universe(per_segment=3)
    prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
    out = {}
    for jobs in (1, 2):
        c = cfg.model_copy(update={"text": cfg.text.model_copy(update={"parallel_jobs": jobs})})
        out[jobs] = P.build(prices, docs, c, u, HashingEmbedder(256), tmp_path / str(jobs),
                            "dev", log=lambda *a: None)
    assert out[1].ledger.equals(out[2].ledger)
    assert out[1].assignments.equals(out[2].assignments)


def test_sparse_inputs_are_unavailable_evidence_not_negative_findings(cfg, tmp_path):
    cfg = S.demo_config(cfg)
    u = S.synthetic_universe(per_segment=1)
    prices = S.synthetic_prices(u)
    prices = prices[prices.symbol.isin(["SPY", "SOXX", "MEM1"])]  # no market edges possible
    docs = S.synthetic_documents(u)
    docs = docs[docs.symbol == "MEM1"].head(3)                     # no text clusters possible
    ctx = P.build(prices, docs, cfg, u, HashingEmbedder(64), tmp_path, "dev",
                  log=lambda *a: None)
    assert ctx.ledger.empty and ctx.events.empty and ctx.edges.empty
    q1 = P.evaluate(ctx, S.synthetic_labels())["q1"]
    assert q1["themes_evaluable"] == 0
    assert q1["not_evaluable_reasons"] == {"no_text_evidence": 2}
    assert q1["detection_recall"] != q1["detection_recall"]  # NaN, not 0.0


def test_synthetic_anchored_topics(cfg, tmp_path):
    """Stable topics find the planted theme early, with persistent identities."""
    cfg = S.demo_config(cfg)
    cfg = cfg.model_copy(update={"text": cfg.text.model_copy(update={"topic_mode": "anchored"})})
    u = S.synthetic_universe()
    dev = P.build(S.synthetic_prices(u), S.synthetic_documents(u), cfg, u,
                  HashingEmbedder(1024), tmp_path / "c", "dev", log=lambda *a: None)
    res = P.evaluate(dev, S.synthetic_labels())
    m = res["matches"].set_index("theme")
    assert m.loc["planted_ai_buildout", "detected"]
    assert m.loc["planted_ai_buildout", "lead_days"] > 180
    assert res["q1"]["unmatched_candidates"] <= 1
    assert dev.track_stats["topic_mode"] == "anchored"
    assert dev.track_stats["share_tracks_persisting_beyond_span"] > 0.5
