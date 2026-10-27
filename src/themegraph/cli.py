"""Command line: `themegraph <command>`. Run `themegraph -h` for the list."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from .config import ROOT, load_pipeline, load_universe

DATA = ROOT / "data"
RUNS = ROOT / "runs"


def _store():
    from .data.store import Store
    return Store(DATA / "store")


def cmd_demo(a):
    from . import pipeline as P
    from . import synthetic as S
    from .report.build import build_report
    from .text.embed import HashingEmbedder

    cfg = S.demo_config(load_pipeline())
    u = S.synthetic_universe()
    prices, docs = S.synthetic_prices(u), S.synthetic_documents(u)
    labels = S.synthetic_labels()
    for period in ("dev", "holdout"):
        out = Path(a.out) / period
        print(f"== synthetic {period}")
        ctx = P.build(prices, docs, cfg, u, HashingEmbedder(cfg.embedding.dimensions),
                      out / "cache", period)
        res = P.evaluate(ctx, labels)
        P.save(ctx, res, out, {"synthetic": True})
        print(json.dumps({"q1": res["q1"], "q2": res["q2"]}, indent=1, default=str))
        print("report:", build_report(out, u, f"Synthetic demo · {period}"))


def cmd_ingest(a):
    from . import ingest as I
    cfg, u = load_pipeline(), load_universe()
    end = dt.date.fromisoformat(a.end) if a.end else dt.date.today()
    store = _store()
    if a.source == "edgar":
        from .data.edgar import EdgarClient
        client = EdgarClient(DATA / "raw" / "edgar", vintage=a.vintage)
        print(I.ingest_edgar(client, cfg, u, store, end, a.symbols))
        return
    from .data.fmp import FMPClient
    client = FMPClient(DATA / "raw" / "fmp", vintage=a.vintage)
    if a.source == "fmp-prices":
        print(I.ingest_fmp_prices(client, cfg, u, store, end))
    elif a.source == "fmp-macro":
        print(I.ingest_fmp_macro(client, cfg, store, end))
    elif a.source == "fmp-transcripts":
        print(I.ingest_fmp_transcripts(client, cfg, u, store))


def cmd_coverage(a):
    from .ingest import coverage
    df = coverage(_store(), load_universe())
    print(df.to_string(index=False))


def cmd_run(a):
    from . import ingest as I
    from . import pipeline as P
    from .eval.freeze import begin_holdout, complete_holdout, completed_attempt, verify_holdout
    from .eval.labels import load_labels
    from .report.build import build_report
    from .text.embed import make_embedder

    cfg, u = load_pipeline(), load_universe()
    overrides = {}
    if a.topic_mode and a.topic_mode != cfg.text.topic_mode:
        if a.period != "dev":
            sys.exit("--topic-mode is for development comparisons; the holdout uses the "
                     "frozen configuration")
        cfg = cfg.model_copy(update={"text": cfg.text.model_copy(
            update={"topic_mode": a.topic_mode})})
        overrides["text.topic_mode"] = a.topic_mode
    if a.verify_identical and a.period != "holdout":
        sys.exit("--verify-identical applies to holdout runs only")
    if a.period == "holdout" and a.verify_identical:
        completed_attempt()  # refuse before loading anything if there is nothing to verify
    store = _store()
    up = a.input_vintage
    prices, docs, factors = I.load_prices(store, up, u), I.load_documents(store, up, u), \
        I.load_factors(store, cfg, up)
    tag = f"-{a.topic_mode}" if overrides else ""
    out = Path(a.out or RUNS / f"{a.period}{tag}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}")
    inputs = {
        **P.input_bindings(prices, docs, observed=False),
        "vintages": dict(store.used),
        "input_vintage_upto": up,
        # Historical runs reconstruct availability from publication times plus lags;
        # they cannot undo later revisions or survivorship in present-day vintages.
        "availability_basis": "reconstructed",
    }
    if overrides:
        inputs["config_overrides"] = overrides
    if a.period == "holdout" and not a.verify_identical:
        attempt = begin_holdout(inputs, a.new_attempt_reason)
    ctx = P.build(prices, docs, cfg, u, make_embedder(cfg.embedding), DATA / "cache",
                  a.period, factors)
    # Save layers and the hashed candidate ledger before any outcome is computed, so
    # the ledger is fixed first and a failure in evaluation never loses a finished build.
    P.save(ctx, None, out, inputs)
    print("layers and candidate ledger saved:", out)
    res = P.evaluate(ctx, load_labels())
    manifest = P.save(ctx, res, out, inputs)
    if a.period == "holdout":
        outputs = P.output_bindings(ctx, res, manifest)
        if a.verify_identical:
            verify_holdout(inputs, outputs)
            print("holdout verified: identical inputs and outputs")
        else:
            complete_holdout(attempt, outputs)
    print("report:", build_report(out, u, f"Theme graph · {a.period}"))


def cmd_topics(a):
    """Print topics for a few windows at one or more cluster distances."""
    import pandas as pd

    from . import ingest as I
    from . import pipeline as P
    from .data.pit import snapshot_ts
    from .text.embed import make_embedder

    cfg = load_pipeline()
    late = [d for d in a.dates if pd.Timestamp(d).date() > cfg.periods.dev_end]
    if late:
        if not a.allow_holdout_dates:
            sys.exit(f"{late} fall after the development period ({cfg.periods.dev_end}). "
                     "Reading holdout topics contaminates the holdout; pass "
                     "--allow-holdout-dates to do it anyway (it is logged).")
        from .eval.freeze import _append
        _append(ROOT, {"event": "holdout_topic_inspection", "dates": late,
                       "note": "holdout topics inspected before evaluation: not clean"})
    docs = I.load_documents(_store(), universe=load_universe())
    # Embed only what the sample windows need (each window plus the novelty filter's
    # lookback); eligibility is still decided on the full history, as in a run.
    from .text.novelty import LOOKBACK_DAYS
    ranges = []
    for d in a.dates:
        hi = snapshot_ts(pd.Timestamp(d))
        ranges.append((hi - pd.Timedelta(days=cfg.text.window_days + LOOKBACK_DAYS), hi))
    p, V = P.prepare_passages(docs, cfg, make_embedder(cfg.embedding), DATA / "cache",
                              ranges=ranges)
    for d in a.dates:
        for dist in a.distances:
            c = cfg.model_copy(update={"text": cfg.text.model_copy(
                update={"cluster_distance": dist})})
            print(P.describe_window(p, V, snapshot_ts(pd.Timestamp(d)), c, a.top))
            print()


def cmd_adjudicate(a):
    """Create/refresh a run's adjudication worksheet and print adjudicated counts."""
    from .eval import adjudication as A

    run = Path(a.run_dir)
    if a.import_file:
        out = A.import_decisions(run, Path(a.import_file), replace=a.replace)
        print(f"imported {out['applied']} decisions")
        for w in out["warnings"]:
            print("warning:", w)
    path = A.write_worksheet(run)
    res = json.loads((run / "results.json").read_text()) if (run / "results.json").exists() \
        else {}
    n = res.get("q1", {}).get("themes_evaluable", 0)
    print(path)
    print(json.dumps(A.summary(A.load(path), n), indent=1))


def cmd_review(a):
    """Serve the local review page for one or more runs' adjudication worksheets."""
    from .review.server import serve

    runs = {}
    for d in a.run_dirs:
        p = Path(d)
        if not (p / "candidate_ledger.parquet").exists():
            raise SystemExit(f"{p} is not a run directory (no candidate_ledger.parquet)")
        runs[p.name] = p
    serve(runs, port=a.port, model=a.model, open_browser=not a.no_browser)


def cmd_questions(a):
    """Share of analysts' questions in a run's Q&A text, topics, candidates and quotes."""
    from .eval.diagnostics import question_share

    for d in a.run_dirs:
        print(d)
        print(json.dumps(question_share(Path(d)), indent=1))


def cmd_monitor(a):
    """Theme monitor: refresh the inventory and data feed, or serve the dashboard."""
    from .monitor import refresh as M
    from .monitor.profiles import load_profile

    prof = load_profile(a.profile)
    a.state = a.state or str(prof.state)
    a.site = a.site or str(prof.site)
    if a.action == "refresh":
        as_of = dt.date.fromisoformat(a.as_of) if a.as_of else None
        if a.fixtures and as_of is None:
            sys.exit("--fixtures needs --as-of (fixture data ends 2021-12-31)")
        st = M.refresh(Path(a.state), Path(a.site), fixtures=a.fixtures, as_of=as_of,
                       ingest_prices=not a.no_ingest, ingest_documents=a.documents,
                       profile=prof)
        print(json.dumps({k: st.get(k) for k in ("ok", "errors", "skipped", "warnings",
                                                 "published_release", "data_as_of",
                                                 "stale", "metrics")}, indent=1,
                         default=str))
        sys.exit(0 if st["ok"] else 1)
    if a.action == "compare":
        from .monitor.compare import compare_profiles
        other = load_profile(a.against)
        out = Path(a.out) if a.out else Path(a.state) / "comparison.md"
        text = compare_profiles(prof, Path(a.state), Path(a.site), other,
                                Path(a.against_state or other.state),
                                Path(a.against_site or other.site))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(text)
        print(f"\nwritten to {out}")
        return
    import functools
    import http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=a.site)
    with http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler) as httpd:
        print(f"Dashboard: http://127.0.0.1:{a.port}/  (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def cmd_compare(a):
    """Side-by-side development comparison of two runs (declared criteria)."""
    from .eval.compare import compare, render
    result = compare(Path(a.run_a), Path(a.run_b))
    print(render(result))
    if not result["comparable"]:
        sys.exit(1)


def cmd_forward(a):
    """Forward log: record as-of predictions, or append matured outcomes."""
    import pandas as pd

    from . import forward as Fw
    from . import ingest as I
    from .text.embed import make_embedder

    cfg, u = load_pipeline(), load_universe()
    store = _store()
    out = ROOT / "forward"
    if a.action == "predict":
        cutoff = dt.date.fromisoformat(a.cutoff) if a.cutoff else dt.date.today()
        # Only vintages downloaded by the end of the cutoff day: replaying an earlier
        # cutoff after a refresh sees what was available then, not today's revisions.
        upto = f"{cutoff:%Y%m%d}T235959Z"
        recs = Fw.predict(I.load_prices(store, upto, u), I.load_documents(store, upto, u), cfg, u,
                          make_embedder(cfg.embedding), DATA / "cache", out, cutoff)
        print(f"{len(recs)} prediction records for cutoff {cutoff} in {out / Fw.PREDICTIONS}")
        return
    prices = I.load_prices(store, universe=u)
    closes = prices.pivot_table(index="date", columns="symbol", values="close")
    closes.index = pd.DatetimeIndex(closes.index).tz_localize(None).normalize() \
        .tz_localize("UTC") + pd.Timedelta(hours=22)
    from .eval.returns import execution_context
    xc = execution_context(closes, u, cfg.market.benchmark)
    new = Fw.evaluate_outcomes(out, closes, cfg.market.sector_benchmark,
                               cfg.evaluation.forward_sessions, closes.index.max(),
                               xc["delisted"], xc["successors"], xc["calendar"], xc["venues"])
    print(f"{len(new)} new outcome records in {out / Fw.OUTCOMES}")


def cmd_freeze(a):
    from .eval.freeze import freeze
    print(json.dumps(freeze(), indent=1))


def cmd_labels(a):
    from .eval.labels import load_labels
    labels = load_labels()
    problems = labels.problems({i.symbol for i in load_universe().issuers})
    print(f"{len(labels.themes)} themes; frozen={labels.frozen}")
    print("\n".join(problems) or "no problems")


def cmd_report(a):
    from .report.build import build_report
    print(build_report(Path(a.run_dir), load_universe()))


def main(argv=None):
    p = argparse.ArgumentParser(prog="themegraph")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("demo", help="run the full pipeline on synthetic planted themes")
    s.add_argument("--out", default=str(RUNS / "demo"))
    s.set_defaults(fn=cmd_demo)
    s = sub.add_parser("ingest", help="fetch data into the immutable cache and store")
    s.add_argument("source", choices=["edgar", "fmp-prices", "fmp-macro", "fmp-transcripts"])
    s.add_argument("--end", help="last date (default today)")
    s.add_argument("--symbols", nargs="*", help="EDGAR only: limit to these symbols")
    s.add_argument("--vintage", help="snapshot date (YYYY-MM-DD) for data that changes over "
                   "time; default today. Reuse a past date to rebuild from that snapshot "
                   "without downloading")
    s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("coverage", help="per-issuer data coverage")
    s.set_defaults(fn=cmd_coverage)
    s = sub.add_parser("run", help="build layers, candidates, evaluation and report")
    s.add_argument("--period", choices=["dev", "holdout"], default="dev")
    s.add_argument("--out")
    s.add_argument("--verify-identical", action="store_true",
                   help="re-run the completed holdout attempt and check every binding")
    s.add_argument("--new-attempt-reason",
                   help="required for any holdout attempt after the first; logged, not clean")
    s.add_argument("--input-vintage", help="use, per table, the latest stored vintage at "
                   "or before this ID (e.g. 20261023T120000Z); default: latest")
    s.add_argument("--topic-mode", choices=["window", "anchored"],
                   help="development runs only: override text.topic_mode to compare "
                   "methods; recorded in the manifest")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("topics", help="print topics for sample windows (calibration)")
    s.add_argument("--dates", nargs="+", default=["2016-06-30"])
    s.add_argument("--distances", nargs="+", type=float, default=[0.5, 0.6, 0.7])
    s.add_argument("--top", type=int, default=12)
    s.add_argument("--allow-holdout-dates", action="store_true",
                   help="inspect dates after dev_end; logged as holdout contamination")
    s.set_defaults(fn=cmd_topics)
    s = sub.add_parser("forward", help="as-of prediction log (predict) and outcomes")
    s.add_argument("action", choices=["predict", "outcomes"])
    s.add_argument("--cutoff", help="prediction date YYYY-MM-DD (default today)")
    s.set_defaults(fn=cmd_forward)
    s = sub.add_parser("adjudicate", help="write/refresh a run's adjudication worksheet")
    s.add_argument("run_dir")
    s.add_argument("--import", dest="import_file", metavar="FILE",
                   help="merge decisions exported by the Theme Adjudication Desk")
    s.add_argument("--replace", action="store_true",
                   help="with --import: overwrite decisions already recorded")
    s.set_defaults(fn=cmd_adjudicate)
    s = sub.add_parser("review", help="review adjudication worksheets in a local page")
    s.add_argument("run_dirs", nargs="+")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--model", default="claude-opus-5-5",
                   help="Claude model for suggestions (needs the review extra)")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("monitor", help="theme monitor: refresh the data feed or serve it")
    s.add_argument("action", choices=["refresh", "serve", "compare"])
    s.add_argument("--profile", default="semis",
                   help="configuration profile in config/profiles (default: semis, the "
                        "semiconductor pilot); sets the files, state and site below")
    s.add_argument("--state", help="registry directory (default: the profile's)")
    s.add_argument("--site", help="dashboard directory (default: the profile's)")
    s.add_argument("--against", default="tech-v2",
                   help="compare: the other profile (default tech-v2)")
    s.add_argument("--against-state", help="compare: the other profile's registry directory")
    s.add_argument("--against-site", help="compare: the other profile's site directory")
    s.add_argument("--out", help="compare: write the report here (default: <state>/comparison.md)")
    s.add_argument("--as-of", help="data cutoff date (default: today)")
    s.add_argument("--fixtures", action="store_true",
                   help="synthetic labelled fixture data instead of the store")
    s.add_argument("--no-ingest", action="store_true", help="skip the price download")
    s.add_argument("--documents", action="store_true",
                   help="also download transcripts and filings (weekly job)")
    s.add_argument("--port", type=int, default=8780)
    s.set_defaults(fn=cmd_monitor)
    s = sub.add_parser("questions", help="share of analyst questions in a run's text")
    s.add_argument("run_dirs", nargs="+")
    s.set_defaults(fn=cmd_questions)
    s = sub.add_parser("compare", help="compare two development runs (window vs anchored)")
    s.add_argument("run_a")
    s.add_argument("run_b")
    s.set_defaults(fn=cmd_compare)
    s = sub.add_parser("freeze", help="hash code/config/labels before the holdout run")
    s.set_defaults(fn=cmd_freeze)
    s = sub.add_parser("labels", help="validate labels/themes.yaml")
    s.set_defaults(fn=cmd_labels)
    s = sub.add_parser("report", help="rebuild report.html for a run directory")
    s.add_argument("run_dir")
    s.set_defaults(fn=cmd_report)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
