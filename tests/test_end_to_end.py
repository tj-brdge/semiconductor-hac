"""The pipeline must recover planted themes and stay quiet on planted noise."""

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
