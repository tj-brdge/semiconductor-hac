# semiconductor-hac · themegraph

A point-in-time, multi-layer graph of listed companies that flags investment themes
as they form, then tests whether those flags carry signal. Two layers drive it:

- **Market layer:** hierarchical agglomerative clustering (HAC) of residual daily
  returns, kept as a full tree (cophenetic affinity) rather than a fixed number of
  clusters. A macro-sensitivity layer sits beside it.
- **Text layer:** earnings-call and SEC-filing passages, embedded, clustered in
  rolling windows, and linked into **topic tracks** with persistent IDs.

A **theme candidate** is a topic whose share of disclosures grows across issuers.
Every candidate is written to a hashed ledger before any outcome is computed.
Semiconductors are the first test case; the design is in
[docs/DESIGN.md](docs/DESIGN.md).

## Status

| Piece | State |
|---|---|
| Point-in-time store, raw cache, FMP + EDGAR clients | built; **not yet run on live data** (see prerequisites) |
| Market, macro, text layers; tracking; candidates | built, tested |
| Evaluation (detection, co-movement, returns), freeze/holdout guard | built, tested |
| Interactive HTML report | built |
| Theme label file (`labels/themes.yaml`) | **draft**: themes listed, dated markers still to fill |
| Real-data dev run, holdout run, forward log | not started |

## Quick start

```bash
uv sync
uv run pytest -q              # 36 tests, ~1 min, fully offline
uv run themegraph demo        # synthetic planted themes -> runs/demo/{dev,holdout}/report.html
```

The demo plants three themes in synthetic prices and transcripts: a real theme in
the development period, a hype-only theme that never co-moves (negative control),
and a real theme in the holdout period. The pipeline should find all three and
show co-movement only for the real ones.

## Real data

Prerequisites (cloud environment settings or your shell):

- Network access to `financialmodelingprep.com`, `api.voyageai.com`, `www.sec.gov`
  and `data.sec.gov`.
- `FMP_API_KEY`, `VOYAGE_API_KEY`.
- `EDGAR_USER_AGENT="<your name or org> <contact email>"`, which the SEC requires.

```bash
uv run themegraph ingest fmp-prices        # USD closes, FX-converted for KRW/TWD/JPY listings
uv run themegraph ingest fmp-macro         # yields, CPI (release-lagged), oil, dollar
uv run themegraph ingest fmp-transcripts
uv run themegraph ingest edgar             # 10-K/10-Q/20-F sections, 8-K exhibit 99
uv run themegraph coverage                 # per-issuer gaps, before trusting anything
uv run themegraph run --period dev         # -> runs/dev-<timestamp>/report.html
```

Raw responses land in `data/raw/` (content-hashed, never overwritten); normalised
tables in `data/store/<table>/<vintage>.parquet`; embeddings are cached per model.
`data/` and `runs/` are git-ignored.

### Refreshing data

Re-run the same `ingest` commands. Data that providers revise or extend (adjusted
price history, the list of earnings calls, EDGAR filing lists, the latest
treasury quarter and macro year) is cached per **vintage**, the download date,
so a refresh on a new day fetches it afresh while re-runs on the same day reuse
one consistent snapshot. Data that never changes (transcript text, filing
documents, finished quarters and years) is downloaded once and reused forever.
Old vintages are kept; `--vintage YYYY-MM-DD` rebuilds from a past snapshot
without downloading.

### Holdout protocol

1. Fill every `emergence_marker` / `consensus_marker` in `labels/themes.yaml` with a
   date **and** a source, then set `frozen: true`. Check with `themegraph labels`.
2. `themegraph freeze` writes `freeze.json`: hashes of code, config, labels and the
   primary metrics. Commit it.
3. `themegraph run --period holdout` refuses to run without a matching freeze and
   logs the run in `holdout_log.jsonl`. It runs once; `--verify-identical` re-runs
   only to confirm the same result.

## Layout

```
config/pipeline.yaml   every rule and threshold (part of the frozen spec)
config/universe.yaml   76 issuers: 46 semiconductor core + 30 adjacency ring
labels/themes.yaml     hand-labelled themes and negative controls (draft)
src/themegraph/
  data/      cache, point-in-time rules, store, FMP and EDGAR clients
  market/    returns, residuals, HAC affinity, macro exposures
  text/      passages, novelty filter, embeddings, windowed clustering, tracking
  graph/     layer edges, candidate detection and ledger
  eval/      labels, detection, co-movement and return tests, freeze
  report/    standalone HTML report
  pipeline.py, ingest.py, synthetic.py, cli.py
tests/                 offline unit, invariance and end-to-end tests
```
