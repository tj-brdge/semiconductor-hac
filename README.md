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

Evidence levels: **implemented** (code exists), **tested** (offline tests on
synthetic or fixture data), **live-validated** (run on real FMP/EDGAR/Voyage data
and inspected), **deferred** (not built).

| Piece | State |
|---|---|
| Point-in-time store, raw cache, FMP + EDGAR clients, per-vintage refresh | tested; **live-validated**: real ingestion has run (prices, macro, transcripts, EDGAR) |
| Market and macro layers | tested; live-validated in the first development run |
| Text layer as of the first development run (2010–2021, `voyage-finance-2`) | live-validated, and found wanting: 190 candidates (16 a year), topics dominated by call small talk and statement boilerplate, ~14,600 topic IDs over 144 windows |
| Revised text layer (PR #7 and the review fixes: filters, genre centring, overlap tracking with a similarity floor, new-high and breadth rules, content-addressed cache, as-of explanations) | tested on synthetic data and fixtures; **not yet live-validated** |
| Evaluation: detection, co-movement, buy-and-hold returns, dependence-grouped inference, adjudication | tested |
| Holdout lifecycle (attempt logged before outcomes, one clean attempt, verification) | tested; the real holdout has **not** been run |
| Forward prediction log (`themegraph forward`) | tested; not yet used on live data |
| Theme labels (`labels/themes.yaml`) | **draft**: themes listed, every date and source still blank, not frozen |
| Layer ablations (text-only vs market-only), beta matching, follow-through/fundamentals layer | deferred |

## Quick start

```bash
uv sync
uv run pytest -q              # 75 tests, ~2 min, fully offline
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
uv run themegraph ingest edgar             # 10-K/10-Q/20-F sections, 8-K/6-K exhibit 99
uv run themegraph coverage                 # per-issuer gaps, before trusting anything
uv run themegraph topics                   # read sample topics; calibrate cluster_distance
uv run themegraph run --period dev         # -> runs/dev-<timestamp>/report.html
uv run themegraph adjudicate runs/dev-...  # review matches and candidates (worksheet)
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
2. `themegraph freeze` writes `freeze.json`: hashes of code, config, labels, the
   primary metrics and the dependency environment (`uv.lock`, Python version).
3. `themegraph run --period holdout` appends an `attempt` record to
   `holdout_log.jsonl` and takes `holdout.lock` **before** building anything, so
   an interrupted attempt still counts. Only the first attempt is clean; any later
   one, under any freeze, needs `--new-attempt-reason` and is logged as not clean.
   Inspecting holdout-period topics (`themegraph topics --allow-holdout-dates`)
   is logged and also makes later attempts not clean.
4. `--verify-identical` re-runs a completed attempt and checks inputs, vintages,
   environment, ledger and per-episode outputs; it never runs a first attempt.

### Forward log

`themegraph forward predict --cutoff YYYY-MM-DD` records, append-only, what the
pipeline flags using only data downloaded by the cutoff (`forward/predictions.jsonl`).
Repeating a cutoff with the same inputs adds nothing; with different inputs it is
refused. `themegraph forward outcomes` appends outcomes for horizons that have
elapsed (`forward/outcomes.jsonl`) and never modifies predictions.

### Conventions worth knowing

- **Returns:** bought at the first close after the snapshot, sold h sessions later;
  equal-weight buy-and-hold simple return; membership fixed at the flag;
  constituents tagged complete/delisted/stale/missing; baskets under 80% coverage
  and horizons past the data are reported, not scored.
- **Availability:** historical runs reconstruct availability from publication
  times plus lags (`availability_basis: reconstructed`); this cannot undo later
  revisions or survivorship in present-day data. Forward predictions use the
  observed basis.
- **Inference** is descriptive: episodes sharing issuers over overlapping
  intervals are pooled into dependence groups before any permutation test.

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
