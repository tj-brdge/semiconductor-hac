# Multi-layer theme graph — design (v1 draft)

Status: **v1 implemented** (see README for what has and has not run on real data). Decisions marked *(decided)* came from
the scoping interview; items marked *(open)* need an answer before or during the
milestone that depends on them.

## 1. Purpose

Build a point-in-time, multi-layer graph of listed companies that highlights
investment themes as they form, starting with semiconductors as the test case and
expanding across markets later.

Two questions, kept separate:

- **Q1 — Early detection.** Does the system flag real themes (DRAM/HBM, AI
  compute, SiC/EV power, …) before they become consensus, with a tolerable false-alarm
  rate?
- **Q2 — Signal.** Does a detection carry information that was not already in
  prices: future co-movement, or future returns relative to sensible nulls? A
  negative answer is an acceptable, publishable outcome.

## 2. Lessons carried over from the previous project

Keep:

- Strict point-in-time discipline: every datum has an availability time; features
  are computed only from data available at the as-of date.
- Missing data stays missing (the October 2025 CPI gap changed a cluster when it
  was zero-filled).
- Immutable, content-hashed raw cache; hashed configs and outputs; offline reruns.
- Honest limitations and retrospective-vs-prospective labelling.

Fix:

- **Tuning on known outcomes.** Previous thresholds and theme cases were chosen with
  2023–2026 outcomes known. v1 freezes rules before a single holdout run (§7).
- **Keyword extraction errors** (e.g. "demand growth declining" tagged `demand_up`).
  The text layer uses embeddings and clustering instead of keyword rules.
- **Snapshot-local cluster IDs.** Clusters were renumbered every month, so a
  "theme" had no identity over time. v1 tracks clusters across windows with
  persistent IDs (§5.3).
- **Fixed k=6 and a 75/25 price/macro blend.** These were arbitrary. v1 keeps the
  full dendrogram and treats macro sensitivity as its own layer.
- **Lab-notebook sprawl.** One package, one pipeline, versioned experiments, and a
  README that describes the current state only.

## 3. Scope of v1 *(decided)*

| Area | Decision |
|---|---|
| Universe | Semiconductors first (§4.2), architecture built for expansion |
| Market/macro data | Financial Modeling Prep (FMP) |
| Text data | FMP earnings-call transcripts + SEC EDGAR filings |
| Text method | Voyage embeddings + time-windowed clustering + cross-window tracking |
| Ground truth | Hand-labelled, dated theme list, frozen before evaluation |
| Validation | Development period → frozen rules → one holdout run → forward log |
| Output | Standalone interactive HTML report |

## 4. Data layer

### 4.1 Point-in-time store

All sources land in an append-only raw cache (`data/raw/`, keyed by source,
endpoint, and credential-free parameters, content-hashed). A normalisation step
writes Parquet tables queried through DuckDB. Every row carries:

| Field | Meaning |
|---|---|
| `period` | What the record describes (trading day, fiscal quarter, CPI month) |
| `published_at` | When the source says it was released (EDGAR acceptance time, call date) |
| `first_seen_at` | When we first downloaded it |
| `available_at` | Rule-based time it may be used: `published_at` + source lag (config) |
| `vintage` | Acquisition batch ID, so re-downloads never overwrite history |

One function, `as_of(table, t)`, is the only way the pipeline reads data, and a
test asserts that deleting every record with `available_at > t` leaves all outputs
at `t` unchanged.

### 4.2 Universe

- **Core (~45):** memory, logic/foundry, analog/power, equipment, materials, EDA/IP.
- **Adjacency ring (~40)** *(open)*: hyperscalers, networking/optics, power and
  utilities, EV/auto, servers. Themes cross sector lines (AI compute pulled in
  optics and power), so text clusters need somewhere to link out to. Recommended
  for v1, even though returns-based evaluation stays on the core.
- **Point-in-time membership:** listing and delisting dates, including dead names
  (FMP delisted list), to limit survivorship bias.
- **Non-US issuers:** Samsung and SK hynix do not file with the SEC; their text
  comes from FMP transcripts only, if covered. TSMC, ASML and others file 20-F/6-K.
  Coverage per issuer is reported, never silently assumed.

### 4.3 Sources

| Source | Content | Timing |
|---|---|---|
| FMP | Adjusted daily prices, FX, Treasury yields, CPI, WTI, profiles, transcripts | Call date + configurable lag; macro release rules as before |
| EDGAR | 10-K, 10-Q, 8-K (+ exhibits 99.x), 20-F, 6-K | Exact acceptance timestamp |

EDGAR requests use a declared User-Agent and stay under the SEC rate limit.

## 5. The layers

Each layer produces, for every as-of date, a weighted issuer–issuer edge list
(plus, for text, issuer–topic edges). The multi-layer graph is the stack of these
edge lists on a common node set.

### 5.1 Market layer (HAC)

- Daily log returns, residualised against SPY and, as a separate variant, against
  SOXX. Residualising on the sector ETF removes the common semiconductor beta, so
  sub-themes can show up.
- Rolling windows of 63 and 126 sessions; shrinkage correlation (Ledoit-Wolf);
  distance `sqrt(2(1 - rho))`.
- Average-linkage HAC. **No fixed k.** The edge weight is the cophenetic similarity
  (how high in the tree two names merge), so all cut levels are kept.
- Output: `market_affinity[t, i, j]`.

### 5.2 Macro-sensitivity layer

- Rolling regressions of residual returns on changes in 2Y/10Y yields, the dollar,
  oil and CPI surprises (point-in-time). Edge weight = similarity of exposure
  vectors.
- Kept as its own layer rather than blended into price distances, so its
  contribution can be measured in ablations.

### 5.3 Text layer (temporal text clustering)

1. **Passages.** Transcripts split by speaker turn (prepared remarks vs Q&A
   tagged); filings split by section (Business, MD&A, Risk Factors, 8-K exhibits).
   Long passages are chunked at roughly 300–500 tokens.
2. **Novelty filter.** Passages that nearly duplicate the same issuer's earlier
   text (boilerplate risk factors, repeated safe-harbour language) are down-weighted
   or dropped. Otherwise clusters form around legal boilerplate.
3. **Embedding.** Voyage (finance-tuned model; exact model ID pinned in config).
   Embeddings are cached by `(text_hash, model_id)` and never recomputed silently.
4. **Windowed clustering.** For each as-of date: passages with
   `available_at` in a trailing window (default 180 days, stepped monthly) are
   clustered (HAC on cosine distance with a fixed distance threshold; HDBSCAN as an
   ablation).
5. **Tracking.** Clusters in consecutive windows are linked by centroid similarity
   and member overlap, giving **topic tracks** with persistent IDs and lifecycle
   events: birth, growth, merge, split, decline.
6. **Exposure.** `exposure[t, issuer, topic]` = the issuer's share of novel passage
   weight in that topic. Text affinity between issuers = similarity of their
   exposure vectors.
7. **Labels for humans only.** Topic names come from c-TF-IDF keywords plus
   representative quotes. Labels are never used as features.

### 5.4 Later layers (not v1)

Fundamentals co-movement (growth, margins, inventory), supply-chain links, and the
advisory AI evidence reviewer from the previous project.

## 6. Theme candidates

A **theme candidate** at time `t` is a topic track plus the set of issuers whose
exposure to it is rising, flagged when all of these hold (thresholds fixed in
config before holdout):

- **Text emergence:** the topic's share of all novel passages, smoothed over one
  quarter of snapshots so the earnings-call calendar does not read as growth, rises
  for *m* consecutive quarter steps and by at least `min_growth_ratio` overall,
  with at least *n* issuers exposed. Snapshots whose windows were still filling at
  the start of the text history are skipped.
- **Breadth:** the exposed issuers span at least two sub-industries, or the
  candidate is marked "single-segment".
- **Market alignment (scored, not required):** market affinity among the exposed
  issuers rises relative to its own trailing baseline and to random issuer sets of
  the same size.

Output: a candidate ledger `(candidate_id, t_first_flag, topic_track, issuers,
scores)`, written and hashed **before** any forward outcome is computed.

Both "text leads market" and "market leads text" orderings are recorded. Which one
comes first is itself a finding.

## 7. Validation protocol

### 7.1 Periods *(decided: frozen holdout + forward)*

| Period | Dates | Use |
|---|---|---|
| Development | 2010-01 → 2021-12 | Build, tune, and inspect freely |
| Holdout | 2022-01 → 2026-10 | Run **once** with frozen rules |
| Forward | from the freeze date | Append-only prediction log; the only clean evidence |

**Contamination caveat:** both the author and any LLM used in development already
know the 2022–2026 history. The holdout is therefore *pseudo*-out-of-sample. It is
reported as such, and the forward log is the definitive test.

### 7.2 Ground-truth label file *(decided: hand-labelled)*

`labels/themes.yaml`, frozen and hashed before the holdout run. Per theme: name,
core issuers, and dated markers with a citation for each:

- `emergence_marker`: the earliest public evidence that the theme existed.
- `consensus_marker`: the point where it became mainstream (for example, a major
  press cover story, a dedicated ETF launch, or broad sell-side adoption).

Detection lead time = `consensus_marker − t_first_flag`.

Candidate themes:

- **Development period:** 2016–18 memory supercycle, crypto-mining GPU demand,
  cloud/data-centre build-out, 2019–21 SiC/EV power, 2020–21 chip shortage and auto
  semis, 5G handsets.
- **Holdout period:** 2022 inventory correction, 2023 AI compute, HBM, CoWoS and
  advanced packaging, AI networking/optics, China domestic substitution and export
  controls, the analog bottoming cycle, the 2025–26 DRAM upcycle.
- **Negative controls (needed to measure false alarms):** themes that were hyped but
  faded, such as the metaverse (2021) and crypto mining after 2022.

### 7.3 Metrics (primary metrics marked ★, fixed in advance)

**Q1 — detection**

- ★ Recall: labelled themes flagged before their consensus marker.
- ★ Median lead time.
- False alarms per year. Unmatched candidates are adjudicated blind, from a sample
  with dates hidden.

**Q2 — signal**

- ★ **Co-movement test:** does a text-first candidate predict a rise in market
  affinity among its issuers over the next 1, 2 and 3 years (252/504/756
  sessions), compared with
  random issuer sets matched on size, sub-industry, beta and momentum? This is the
  cleanest test of whether text adds information prices did not already have.
- Return test: forward excess return of candidate baskets versus SOXX, versus
  matched random baskets, and versus a plain momentum basket.
- Ablations: text-only, market-only, and text + market, to measure what each layer
  adds.
- Statistics: few independent episodes are expected, so the report shows
  episode-level results, block-bootstrap intervals and the null distribution. No
  annualised performance claims.

### 7.4 Freeze mechanics

`freeze` writes `freeze.json` with the hashes of code, config, label file, and
primary metric definitions, and creates a git tag. The holdout runner refuses to
run without a valid freeze and logs that it ran. Any change after the freeze makes a
new version that can only be evaluated forward.

## 8. Output

A standalone HTML report (served locally), with these views:

- Multi-layer graph at a chosen date, with layer toggles and edge-strength
  threshold.
- Topic-track timeline (a stream graph of birth, growth, merge and split) with
  representative quotes linked to their source documents.
- Candidate ledger beside the labelled themes, showing lead times.
- Q1/Q2 results with null distributions and a limitations section.

Numerical outputs are written as Parquet/JSON alongside the report.

## 9. Implementation

### 9.1 Stack

Python 3.12, `uv`, polars/pandas, DuckDB + Parquet, scipy/fastcluster,
scikit-learn, networkx, httpx, the voyageai client, pydantic configs, pytest, ruff,
and GitHub Actions CI (tests run offline against fixtures).

### 9.2 Layout

```
src/themegraph/
  data/      fmp.py, edgar.py, store.py, pit.py
  universe/  membership.py
  market/    returns.py, hac.py, macro.py
  text/      passages.py, novelty.py, embed.py, cluster.py, track.py
  graph/     layers.py, candidates.py
  eval/      labels.py, detection.py, comovement.py, returns.py, nulls.py, freeze.py
  report/    build.py, templates/
config/      universe.yaml, pipeline.yaml
labels/      themes.yaml
tests/       fixtures/ + unit and point-in-time invariance tests
docs/        DESIGN.md
```

### 9.3 Milestones

| # | Deliverable |
|---|---|
| M0 | Repo scaffold, tooling, CI, this doc agreed |
| M1 | Point-in-time store; EDGAR + FMP ingestion; universe membership; coverage report |
| M2 | Market + macro layers; dendrogram/affinity outputs |
| M3 | Text layer: passages, novelty, embeddings, windowed clustering, tracking |
| M4 | Multi-layer graph, candidate ledger, first report (development period only) |
| M5 | Label file drafted, reviewed, frozen; development-period evaluation |
| M6 | Freeze; single holdout run; results report |
| M7 | Forward logging on a daily/monthly refresh; expand universe beyond semiconductors |

## 10. Environment prerequisites

- Network access to `financialmodelingprep.com`, `api.voyageai.com`, `www.sec.gov`
  and `data.sec.gov`.
- `EDGAR_USER_AGENT` naming the requester and a contact email (SEC policy).
- `FMP_API_KEY` and `VOYAGE_API_KEY` set as environment secrets, never in code,
  logs or chat.
- Embedding cost to be estimated from actual corpus token counts at M3, before
  the first full run.

## 11. Implementation notes (v1)

- **Defaults taken for §12 questions:** adjacency ring included (text and market
  affinity; returns use core names only); two-day transcript lag; dev/holdout split
  at 2021-12-31.
- **Null baskets** for co-movement and returns are drawn from non-members with the
  same segment mix; percentiles count ties as half.
- **Large windows:** above 5,000 passages, clustering first groups passages into
  deterministic micro-clusters (seeded mini-batch k-means), then runs HAC on those.
- **EDGAR timestamps:** `acceptanceDateTime` is read as US/Eastern (it carries a
  misleading "Z"), which can only delay availability.
- **Horizons:** outcomes are measured 1, 2 and 3 years after a first flag, to
  match multi-year holding and the time a theme takes to move from disclosures to
  prices (the synthetic demo showed a 63/126-session horizon missing a theme whose
  text led prices by about nine months). A flag counts as early up to three years
  before its consensus marker. A flag is scored only once its horizon has fully
  elapsed inside the evaluated period, so long horizons trade fewer scoreable
  episodes for relevance; the start date moved to 2010 to recover some of them.
- **Embeddings:** a hashing embedder exists for tests and the demo only; reports
  warn whenever a run did not use the configured research model.

### Text layer revision after the first real run (2010–2021 development data)

The first run produced 190 candidates (16 a year). Its largest topics were call
small talk ("think, just, really, question"), financial-statement wording
("months ended, non-GAAP") and risk-factor language, and topic tracking created
about 14,600 topic IDs over 144 windows, so persisting subjects were repeatedly
"reborn" and re-flagged. Changes, made by reading topics, not by looking at
returns:

- **Filters:** passages that are mostly numbers, or contain repeated
  financial-statement or safe-harbour phrases, are dropped before clustering.
- **Genre centring:** each window's vectors are centred within their genre (call
  remarks, call Q&A, each filing section), removing the shared register of each
  kind of text. Removing the window's top principal directions is available
  (`remove_top_components`) but off: on synthetic data it removed the topics
  themselves.
- **Tracking:** a cluster continues a topic if it shares at least 30% of its
  passages with the topic's previous cluster (consecutive windows overlap) or its
  centroid is close. Each run reports churn (`text_tracking` in the manifest:
  births per window, median topic life).
- **Emergence:** a candidate's smoothed share must reach a level above anything
  the topic reached in the previous 24 snapshots, and the topic must not be
  discussed by more than half of the issuers with text in that window.
- **Keywords:** computed once per topic from its largest window, with a stop-list
  for call filler and calendar/accounting words.
- **Speed:** micro-clustering in a 128-dimension PCA space with HAC on
  full-dimension centroids, windows clustered in parallel and cached by their
  passages and settings. About 20 s per 19,000-passage window instead of 150 s.
- **Calibration:** `themegraph topics --dates ... --distances ...` prints the
  topics a window yields at several cluster distances, so the threshold is chosen
  by reading topics. It must be fixed before the freeze.

## 12. Open questions

1. Include the adjacency ring (§4.2) in v1? Recommended: yes, for text only.
2. Should the transcript and filing lag rules (`available_at`) be the same as
   before (two days for sources)?
3. Who adjudicates false alarms blind: you, a colleague, or both?
4. Development/holdout split at 2021-12: acceptable, or move it given which themes
   you most want in the holdout?
5. Data licensing: confirm that FMP terms allow storing transcripts in a private
   repo; otherwise raw data stays out of Git and only hashes are committed.
