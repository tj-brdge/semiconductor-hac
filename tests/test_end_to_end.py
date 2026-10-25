"""The pipeline must recover planted themes and stay quiet on planted noise."""

import json

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
    d = dev.diagnostics
    assert d["candidates"] >= 1 and d["step_cosine_median"] > 0.5
    assert 0 <= d["unassigned_share_median"] <= 1


def test_window_vs_anchored_comparison_is_checked_for_comparability(cfg, tmp_path):
    from themegraph.eval import adjudication as A
    from themegraph.eval.compare import compare, render

    base = S.demo_config(cfg)
    u = S.synthetic_universe()
    prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
    inputs = {**P.input_bindings(prices, docs, observed=False), "vintages": {},
              "input_vintage_upto": None}
    runs = {}
    for mode in ("window", "anchored"):
        c = base.model_copy(update={"text": base.text.model_copy(update={"topic_mode": mode})})
        ctx = P.build(prices, docs, c, u, HashingEmbedder(1024), tmp_path / "c", "dev",
                      log=lambda *a: None)
        res = P.evaluate(ctx, S.synthetic_labels())
        runs[mode] = tmp_path / mode
        P.save(ctx, res, runs[mode], {**inputs, "config_overrides": {"text.topic_mode": mode}})
        A.write_worksheet(runs[mode])
        samples = (runs[mode] / "coverage_samples.md").read_text()
        assert "### Assigned" in samples and "### Unassigned" in samples
        packet = (runs[mode] / A.PACKET).read_text()
        assert "Keywords:" in packet and "return" not in packet.lower()
    for mode in runs:  # the test checkout may have local edits; simulate a clean one
        m = json.loads((runs[mode] / "manifest.json").read_text())
        assert m["provenance"]["effective_config"]["text"]["topic_mode"] == mode
        # The universe object actually used (here built in code, not universe.yaml).
        assert m["provenance"]["effective_universe"] == json.loads(u.model_dump_json())
        m["provenance"]["git_dirty"] = False
        (runs[mode] / "manifest.json").write_text(json.dumps(m))
    out = compare(runs["window"], runs["anchored"])
    assert out["comparable"], out["problems"]
    same = compare(runs["window"], runs["window"])
    assert {"A: window", "B: window"} <= set(same["table"].columns)
    assert not out["adjudication_complete"]  # nothing reviewed yet: no conclusion
    assert {"A: window", "B: anchored"} <= set(out["table"].columns)
    assert "Adjudication incomplete" in render(out)
    pooled = out["table"].set_index("criterion").loc["unassigned passage share (pooled)"]
    assert pooled.notna().all()
    for mode in runs:  # recorded figure equals the one recomputed from saved windows
        m = json.loads((runs[mode] / "manifest.json").read_text())
        w = pd.read_parquet(runs[mode] / "windows.parquet")
        assert m["diagnostics"]["unassigned_share_pooled"] == \
            round(1 - w["n_clustered"].sum() / w["n_passages"].sum(), 4)
    # Different inputs are refused.
    def broken(edit):
        m = json.loads((runs["anchored"] / "manifest.json").read_text())
        edit(m)
        (tmp_path / "x").mkdir(exist_ok=True)
        for f in runs["anchored"].iterdir():
            if f.is_file() and f.name != "manifest.json":
                (tmp_path / "x" / f.name).write_bytes(f.read_bytes())
        (tmp_path / "x" / "manifest.json").write_text(json.dumps(m))
        return compare(runs["window"], tmp_path / "x")["problems"]

    assert broken(lambda m: m["inputs"].update(documents="other")) == \
        ["inputs.documents differ"]
    assert any("labels differ" in p for p in
               broken(lambda m: m["provenance"].update(labels_sha256="x")))
    assert any("source code differ" in p for p in
               broken(lambda m: m["provenance"].update(source_sha256="x")))
    assert any("beyond text.topic_mode" in p for p in broken(
        lambda m: m["provenance"]["effective_config"]["text"].update(cluster_distance=0.7)))
    assert any("no provenance" in p for p in broken(lambda m: m.pop("provenance")))
    assert any("development runs only" in p for p in
               broken(lambda m: m.update(period="holdout")))
    assert any("uncommitted" in p for p in
               broken(lambda m: m["provenance"].update(git_dirty=True)))
    # The effective universe: required, and any difference refuses the comparison.
    assert any("effective_universe" in p for p in
               broken(lambda m: m["provenance"].pop("effective_universe")))

    def issuer_edit(field, value):
        def edit(m):
            m["provenance"]["effective_universe"]["issuers"][0][field] = value
        return edit
    sym = u.issuers[0].symbol
    for field, value in (("currency", "KRW"), ("role", "adjacent"), ("segment", "zz"),
                         ("delisted", "2015-01-01"),
                         ("successor", {"symbol": "X", "shares": 1.0, "cash": 0.0,
                                        "source": "s"})):
        assert any(f"issuers.{sym}.{field}" in p for p in broken(issuer_edit(field, value)))
    assert any("effective universe differs: ['benchmarks']" in p for p in broken(
        lambda m: m["provenance"]["effective_universe"].update(benchmarks=["QQQ"])))
    assert any("issuers.NEW" in p for p in broken(
        lambda m: m["provenance"]["effective_universe"]["issuers"].append(
            {"symbol": "NEW", "segment": "s", "role": "core"})))
