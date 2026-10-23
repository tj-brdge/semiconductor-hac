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
    from .eval.freeze import guard_holdout, log_holdout
    from .eval.labels import load_labels
    from .report.build import build_report
    from .text.embed import make_embedder

    cfg, u = load_pipeline(), load_universe()
    if a.period == "holdout":
        state = guard_holdout()
        if state["already_run"] and not a.verify_identical:
            sys.exit("holdout already run under this freeze; pass --verify-identical to "
                     "re-run and check the result matches")
    store = _store()
    prices, docs, factors = I.load_prices(store), I.load_documents(store), \
        I.load_factors(store, cfg)
    out = Path(a.out or RUNS / f"{a.period}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}")
    ctx = P.build(prices, docs, cfg, u, make_embedder(cfg.embedding), DATA / "cache",
                  a.period, factors)
    inputs = {
        "prices": P.frame_sha256(prices), "documents": P.frame_sha256(docs[["doc_id", "text"]])
        if len(docs) else None,
    }
    # Save layers and the hashed candidate ledger before any outcome is computed, so
    # the ledger is fixed first and a failure in evaluation never loses a finished build.
    P.save(ctx, None, out, inputs)
    print("layers and candidate ledger saved:", out)
    res = P.evaluate(ctx, load_labels())
    manifest = P.save(ctx, res, out, inputs)
    if a.period == "holdout":
        guard_holdout(manifest["results_sha256"])
        if not a.verify_identical:
            log_holdout(manifest["results_sha256"])
    print("report:", build_report(out, u, f"Theme graph · {a.period}"))


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
    s.add_argument("--verify-identical", action="store_true")
    s.set_defaults(fn=cmd_run)
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
