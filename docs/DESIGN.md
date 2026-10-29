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

### 7.4 Freeze mechanics (implemented as the holdout lifecycle; see README)

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

### Review fixes after PR #7

Correctness:
- **Cluster cache** keys on the passages' IDs, issuers, genres and embedding bytes,
  plus procedure constants, embedder, settings and seed; entries are re-validated
  on load. (IDs alone returned stale labels when vectors changed.)
- **Degenerate vectors:** genres with fewer than 3 passages in a window are
  centred on the window mean; empty, non-finite or near-zero residuals and
  cancelled micro-centroids are excluded with label -1 and counted
  (`passages_excluded_degenerate`). No jitter.
- **As-of explanations:** each candidate's keywords and quotations come from its
  flag window only (with issuer, genre, context, source, availability). Whole-run
  keywords remain as a labelled retrospective summary; neither feeds detection.
- **Returns:** entry at the first close after the snapshot (the move into it is
  excluded); equal-weight buy-and-hold simple return, chosen because a mean of log
  returns is not a holdable portfolio; fixed membership with constituent statuses.
- **Snapshot availability:** returns carry when they became known; snapshots
  ignore later-published prices. Runs record input vintages and
  `availability_basis` (reconstructed for history, observed for forward).
- **Null baskets** never repeat a security or include a member; unsupported when
  non-members are too few.
- **Holdout lifecycle:** see README; attempts are logged before the build, only the
  first is clean, verification binds every input and output.

Research choices (with fixtures in `tests/`, not tuned on outcomes):
- **Generic topics** are judged by breadth when growth began. Judged at the flag,
  a theme spreading to all issuers within a quarter (AI in 2023) was never flagged.
  The cap applies only with at least 2 x min_issuers issuers with text.
- **Recurring topics:** the 24-snapshot new-high rule suppresses a topic returning
  to a level it held within the past two years. A memory cycle recurring after
  more than two years still qualifies; a relapse within one cycle does not. This
  is intended; it is a trade-off, not a finding.
- **Numeric filter** raised from 0.15 to 0.35: 0.15 dropped ordinary guidance
  passages; tables score about 0.8.
- **Tracking:** overlap-based links also require centroid cosine >= 0.5. With
  6-month windows stepped monthly, any topic seen once lives about 6 windows, so
  churn statistics report life beyond that span and first-to-last centroid cosine
  (drift). Whether overlap tracking preserves meaning on real data must be checked
  with those statistics in the next development run.

Pushed back / deferred:
- **Beta and momentum matching:** with 4-12 issuers per segment the matched cells
  are mostly empty. Nulls match on segment; momentum balance is reported as a
  diagnostic (`momentum_gap_vs_null`).
- **Layer ablations** (text-only vs market-only candidates) need a separate
  market-only detector; deferred rather than approximated.
- **Inference:** episodes sharing issuers over overlapping intervals are pooled
  into dependence groups before sign-flip tests. With few groups the results are
  descriptive; no stronger inference is claimed.

### Calibration log (development data only)

- **2026-10-24, window 2016-06-30, distances 0.50 / 0.60 / 0.70.** 0.50 fragments
  (59% of passages clustered); 0.70 merges distinct subjects into a 39-issuer
  "semiconductor manufacturing demand" topic; 0.60 separates real subjects (the
  16/14nm node ramp: UMC, TSM, ONTO, XLNX, LRCX, SNPS; cloud: ORCL, MSFT, SMCI, HPE;
  networking: ANET, CIEN, CSCO). `cluster_distance` stays 0.60.
- The largest clusters at every distance were corporate housekeeping (buybacks,
  tax, senior notes, ASU/FASB, compensation committee, board meetings from 6-K
  annual-meeting documents, generic IP risk). A housekeeping phrase filter
  (two distinct hits from a specific multi-word list) now drops these before
  embedding; fixtures check that thematic text, including investment tax credits
  for fabs and export controls, is kept.
- **Second pass, same window.** Housekeeping clusters fell sharply (15,250 of
  41,549 passages dropped as tables/boilerplate) and real subjects led: node
  ramp/EUV, automotive ADAS, wireless/IoT, optical datacom, cloud, solar.
  Residual non-thematic clusters were call openings ("thank you for joining…
  webcast"), indebtedness, conflict minerals, impairment/fair value, and
  change-in-control/severance; those phrases joined the list (still two distinct
  hits). Single-company topics are left alone: `min_issuers` keeps them out of
  candidates. Generic recurring topics (gross margin) are left to the new-high rule.
- **Second window, 2019-06-30.** 0.60 again separates subjects that 0.70 merges
  (Tesla with ENPH and ON; Vistra with MU) and clusters 67% of passages, so
  `cluster_distance` stays 0.60. Real subjects lead: 5G infrastructure/baseband
  (XLNX, QRVO, SWKS, LSCC, QCOM; 25 issuers), memory/NAND recovery, US-China
  tariffs (33 issuers at 0.70), Xeon data-centre platforms, Tesla Gigafactory.
  New residual: SPAC wording ("initial business combination", "trust account")
  from VRT's pre-merger filings, now filtered. Generic risk-factor, inventory,
  opex and gross-margin topics recur in both windows and are left to the
  new-high rule.

- **Full development run, 2010-2021 (first run after calibration).** Tracking
  churned: 15,376 tracks, ~107 births per monthly window, median life 2 windows
  against a 7-window passage span, 6.9% of tracks outliving the span. Candidates
  were mostly three-issuer supply-chain fragments ("suppliers, manufacturing,
  capacity, demand"), several flagged in 2010. Diagnosis: a fresh or split-off
  track has no history, so the new-high test passes trivially, and share growth
  can come from one issuer writing more. Three rule changes, made on development
  data, logged here before re-running:
  1. *Full history*: no flag until the whole 24-snapshot new-high lookback lies
     after the text warm-up (first possible flag about 2013-03 for data starting 2010-01).
  2. *Lineage*: a split-off track inherits its ancestors' share from before it was
     born for the new-high test. Risk accepted: a real theme that splits off a
     larger, louder topic is suppressed until it outgrows the parent's level.
  3. *Spread*: issuers in the topic must rise by `min_issuer_growth` (1) between the
     start of growth and the flag.
  Plausible flags in the pre-change run, to check they survive: China LTE /
  TD-SCDMA (MRVL, QRVO, SWKS, 2011-10) and foundry capex (LRCX, TSM, UMC; AMAT,
  ASML, KLAC, 2011). The China LTE flag falls before the new earliest flag date,
  so it will not survive rule 1 on this data; that is the cost of the warm-up.

- **Rerun with the three rules.** Earliest flag 2012-09-30 (filings before 2010
  fill the warm-up, so the lookback limit binds rather than the warm-up). Leading
  candidates are now subjects: FinFET / node transitions (TSM, UMC, XLNX, KLAC,
  LRCX, CDNS; split across four tracks), RF filters and LTE band content (AVGO,
  QRVO, SWKS), etch intensity (AMAT, LRCX, CAMT), Windows on ARM (INTC, MSFT,
  NVDA). Residual noise: 20-F/6-K cover-page wording (registrant, Securities
  Exchange Act), now filtered; "inaudible" transcript markers dropped from
  keywords. The node theme appearing as four tracks is the tracking churn that
  the stable-topic upgrade is meant to fix.

- **Stable topics (`text.topic_mode: anchored`), added as a variant.** The window
  method still re-cuts topics monthly (median life 2 windows; the node/FinFET theme
  appeared as four candidates). The anchored method discovers topics every 12
  snapshots from the trailing two years of text (past only), assigns each window's
  passages to the nearest topic centre (cosine >= 0.45 after genre centring with the
  refit's genre means), and clusters what no topic claims into new topics at once.
  At a refit, new centres inherit the identity of the closest old topic (one-to-one,
  cosine >= 0.70, old centres re-expressed through their latest members). On the
  synthetic demo it finds the planted theme 333 days before consensus with 8 topics
  that persist for the whole period. Comparison on development data:
  `themegraph run --period dev --topic-mode anchored` against the window run (145
  ledger rows), on topic life, duplicate candidates, and how the list reads.
  Unknowns to check on real data: the share of passages left unassigned at 0.45 and
  the number of new topics born from the unassigned pool each month.

- **Bug: mixed exchange calendars erased returns (fixed).** The return panel used
  the union of US, Korean, Japanese and Taiwanese trading days. On a day only one
  market traded, every other stock's return that day and the next became missing,
  so windows spanning US holidays (Thanksgiving to MLK Day) dropped US stocks
  below `min_observations_share`. Snapshots for January–February 2017 and 2019 lost
  almost every US issuer, which left candidates flagged then without a market
  alignment score. Every other window lost some observations too. Returns now run
  between each stock's own consecutive closes and are summed onto the benchmark's
  (SPY) sessions. All market-layer results change slightly; rerun before comparing.

### Review fixes after the anchored-topics PR (2026-10-25)

Verified against HEAD 06daa45 before changing anything; each has a regression test.

- **Refit matching** (`text/anchored.py: match_identities`). The Hungarian assignment
  ran over all similarities and then dropped pairs below 0.70, so
  `[[0.75, 0.65], [0.69, 0.00]]` lost the valid 0.75 continuation. Matching now runs
  over valid pairs only, maximising first the number of continued identities, then
  total similarity.
- **Ancestry and recurring fragments.** A refit that cut a topic in two kept one
  piece's identity and made the other an unrelated birth, which then looked brand new
  to the candidate rules. Two changes. Lineage events: an unmatched new topic is a
  `split` of the old topic that held at least `anchor_lineage_share` (0.5) of its
  training passages; a retiring topic whose passages mostly went to one continuing
  topic is a `merge`. Parentage uses shared text, never shared companies. For
  detection, every anchored birth (refit or unclaimed text) gets a point-in-time
  history (`topic_history.parquet`): at birth, the preceding windows are re-assigned
  with the codebook of that moment, and the new topic's share and issuer count there
  fill its pre-birth snapshots for the growth, new-high and spread tests. A recurring
  fragment carries its old level and is not flagged; a subject absent from earlier
  text starts low and stays discoverable. Anchored mode uses this history instead of
  the window mode's parent inheritance, which would hold a new theme split out of a
  large, loud topic to the parent's level.
- **Forward replay and partial vintages** (`data/store.py`). Ingestion stamped every
  row with the ingest time, and reads took only the latest vintage: replaying an
  earlier cutoff after a refresh lost data, and a partial EDGAR batch
  (`--symbols`) hid every other issuer's filings, in development runs too. Reads now
  combine all vintages up to the requested one, latest version per record;
  `first_seen_at` is the earliest vintage holding exactly that content, so unchanged
  records keep their first-seen time and revisions are never backdated.
  `forward predict` reads only vintages downloaded by the end of the cutoff day.
- **Input identity** (`pipeline.input_bindings`). Fingerprints were row-order
  dependent and covered only `doc_id` and text for documents. They now hash every
  column except bookkeeping, order-independently; `first_seen_at` is included for
  forward (observed-basis) inputs only. Holdout verification compares the same
  fingerprints, plus a scheme number.
- **Basket accounting** (`eval/returns.py`). A position without an exit value used
  to be dropped and the rest averaged (four members at +10% and one missing gave
  "ok, +10%"). Now any unvalued bought position makes the basket
  `unvalued_position` with no return, plus a worst-case bound (missing at -100%).
  Members never bought (no entry close) are excluded at entry as before. Acquired
  members convert into the acquirer's shares and cash on the recorded terms
  (universe `successor`, terms checked against the closing 8-Ks: XLNX 1.7234 AMD;
  MXIM 0.6300 ADI; LLTC $46.00 + 0.2321 ADI); a delisting without recorded terms is
  still valued at the last close, counted as an assumption.
- **Calibration parity** (`prepare_passages(ranges=...)`). `themegraph topics`
  cropped documents before exact-duplicate removal, so text first seen before the
  crop survived calibration but not a run. Eligibility is now decided on the full
  history; only embedding is restricted.
- **Comparable diagnostics** (`eval/diagnostics.py`). The anchored drift compared
  centres from different refits' coordinates. Both modes now report the same
  member-based measures in one representation: unassigned share, births and lasting
  births per window, month-to-month and first-to-last member cosine, tightness, and
  near-duplicate candidates; manifests also record stage timings.

### Window vs anchored comparison (criteria declared before results)

Both runs: same vintages and canonical input hashes, same embeddings and cache, same
`config/pipeline.yaml`, differing only by `--topic-mode`; `themegraph compare`
refuses otherwise. Adjudication is done from `adjudication_packet.md` (text only, no
returns or market data); `report.html` is not opened until both worksheets are done.

Primary, in order:
1. Labelled development themes confirmed by adjudication, and their lead time before
   consensus (compared per theme, not only as medians: six development themes is a
   small sample).
2. Confirmed false alarms: negative-control matches confirmed, plus unmatched
   candidates judged noise, per year.

Secondary (diagnostic, never decisive alone): candidates judged duplicate and
near-duplicate pairs; member-based consistency (month-to-month cosine, drift,
tightness); unassigned share; births per window; runtime.

Decision rule: make `anchored` the default only if it confirms at least as many
labelled themes as `window`, misses none that `window` confirms (or the report states
the trade explicitly), and has no more confirmed noise per year. Longer topic life
does not count in its favour on its own. If the primary criteria tie, keep `window`
unless anchored has materially fewer duplicates (at least 30% fewer) with no worse
consistency. Prerequisite: approved label dates; without them no theme is evaluable
and only sections 3-5 of the comparison can be read.

### Label verification (2026-10-25)

Every proposed marker in `labels/themes_draft.yaml` was checked against SEC EDGAR,
the only source reachable from the build environment (five quotes re-checked
independently). Result per marker (`verification.status`): 6 verified, 1 verified
with a correction (AI power emergence moves to Vertiv's 2023-04-26 release), 23 with
an SEC primary alternative, 4 unverifiable here (press-only). Nothing is approved.

Definitions applied: emergence = earliest dated public disclosure naming the driver
as affecting business or demand (publication date recorded beside the event date);
consensus = treated as a market-wide theme by major press. SEC filings can verify
emergence but rarely consensus, so most consensus markers still need a press check.

Open judgement calls (listed per theme as `review_needed`):
- Ordering: for crypto GPUs (2017), HBM and advanced packaging the earliest issuer
  filing comes after, or close to, the press-based consensus. Either emergence may
  rest on press and trade sources, or these themes cannot test early detection from
  SEC text alone.
- Scope: AI networking vs optics (SEC evidence 2023-05 vs 2023-08) and AI power
  equipment vs power generation (2023-04 vs 2024-05) look like two themes each.
  Proposed splits, not applied.
- Negative controls: the metaverse chip issuers disclosed no metaverse exposure
  (zero mentions in NVDA's FY22 10-K and QCOM's 2021 10-K), so "not flagged" is weak
  evidence; crypto mining 2021 is a fair control for NVDA.

Second SEC pass (2026-10-25, three quotes re-checked independently), recorded as
`additional_sec_evidence`; no dates changed and nothing approved:
- China export controls: earliest explicit issuer statements on the October 2022
  rules are Lam's 10-Q (2022-10-24) and KLA's 10-Q (2022-10-28).
- AI power: equipment side nVent 2023-07-28 (after Vertiv 2023-04-26); generation side
  Vistra and GE Vernova first link demand to AI in FY2024 10-Ks (February 2025); Eaton
  names data-centre strength but no AI. This strengthens the proposed split.
- AI networking/optics: Fabrinet 2023-08-21 places optics in August 2023.
- SiC for EVs: ON's analyst day 2019-03-08. DRAM 2025: Micron's 8-K of 2025-08-11 is
  the first explicit DRAM pricing statement. Advanced packaging: no SEC filer states a
  CoWoS constraint in 2023.

### Before freezing and running the holdout

1. Approve labels: resolve the calls above, confirm press-only markers, copy approved
   dates into `labels/themes.yaml` (`themegraph labels` must report no problems).
   *Done 2026-10-25:* the user approved all 17 entries as drafted. That keeps the
   drafted dates for crypto GPU, HBM and advanced packaging, even where the SEC
   evidence is later. AI networking/optics and AI power stay single themes, and both
   negative controls stay. The weak DRAM 2025 markers stand. Press-only markers
   (`unverifiable_here`) rest on the user's approval, not on SEC verification. They
   can be edited until the freeze. `themegraph labels`: no problems. The dev runs at
   1b3113a were evaluated against the blank labels, so both modes must be rerun
   before adjudication (no theme was evaluable).
2. Rerun both topic modes on development data from one commit and data state, and
   adjudicate both from the packets; `themegraph compare` must report comparable runs
   and complete adjudication.
3. Choose `text.topic_mode` by the declared rule above; record the decision and the
   comparison output here.
4. Only then `themegraph freeze` and the single holdout run.

### Anchored assignment calibration (rule fixed before rerunning, 2026-10-25)

First like-for-like development runs (same code, data and config, 2026-10-25):

| | window | anchored (assign 0.45) |
|---|---|---|
| passages in no topic (median) | 0.42 | 0.69 |
| topic tightness (median) | 0.63 | 0.65 |
| month-to-month / first-to-last member cosine | 0.84 / 0.36 | 0.98 / 0.61 |
| births per window (lasting) | 107 (8.8) | 19 (6.6) |
| distinct candidates (near-duplicate pairs) | 115 (28) | 25 (0) |

At 0.45 anchored topics leave most text unassigned, so a fair comparison needs the
assignment threshold calibrated first. Rule, using diagnostics only (never
candidates, labels or returns): choose `anchor_assign_similarity` from the
calibration table (`text_tracking.calibration`, `anchor_calibration.parquet`) so that
the assigned share (existing topics' claim at the threshold plus topics found in
unclaimed text) is closest to the window mode's clustered share, 0.58, among
thresholds in steps of 0.05; then confirm with one rerun that the assigned share is
within 0.05 of 0.58 and tightness is at least the window's 0.63. If tightness falls
below, take the next higher threshold. The chosen value is recorded here before
adjudication starts, and both runs are then adjudicated and compared as declared.

*Correction (review of 531575c).* The calibration report mixed two stages: its
"found in unclaimed" figure was final assigned minus initial claims, so passages
rejected by the size and issuer rules showed up as negative discoveries, and its shares
used usable passages as the denominator while the window comparison uses all passages.
The 0.15 gap below is therefore a net figure, not a measured rejection rate. Runs now
record each stage per window (passages, usable, claimed by existing topics, rejected by
the size/issuer rules, retained, discovered in unclaimed text, assigned, unassigned),
on both denominators, with totals that must reconcile. The threshold estimates below
also used a codebook built under 0.45; a different threshold changes which topics exist,
so they are estimates only.

Calibration run (assign 0.45, 2026-10-25): existing topics would claim 0.98 / 0.92 /
0.80 / 0.63 / 0.46 / 0.31 / 0.19 of usable passages at 0.25 ... 0.55; only 0.31 was
actually assigned at 0.45. The 0.15 gap is the per-window size rule (a topic needs
min_cluster_passages from min_cluster_issuers in the window): with hundreds of fixed
topics, many catch only a few passages in a month and are dropped there, which window
clustering avoids by construction. Estimated assigned share: 0.35 gives 0.54-0.65
(flat or proportional loss), 0.40 gives 0.42-0.48, 0.30 gives 0.62-0.77. Chosen:
**0.35** (closest to 0.58; ties go to the higher threshold). To be confirmed by the
rerun criteria above (assigned within 0.05 of 0.58, tightness >= 0.63; otherwise
0.40). Recorded before any adjudication.

*Revised confirmation (review of 531575c).* Matching window mode's coverage is a
precondition for a fair comparison, not evidence of quality: extra coverage made of
boilerplate would help nothing. The confirmation therefore needs all of:
(1) assigned share of all passages within 0.05 of window mode's in the same data state
(window mode measured 0.58 on all passages); (2) member tightness at least window
mode's; (3) a reading of `coverage_samples.md` (text only) in which assigned passages
are mostly about business subjects and unassigned ones mostly boilerplate or one-offs.
If 0.35 fails (1) or (2), the stage table says why (too few claims, or claims rejected
by the size/issuer rules); 0.40 is tried only if it addresses that cause. If no tested
value passes, that is the result, reported as such, and the comparison proceeds with
both modes described rather than a forced threshold.

*Confirmation result for 0.35 (measured, 2026-10-25; run on the code before the
review fixes after 531575c, so without provenance or coverage samples; the coverage
and tightness diagnostics are unaffected).* Assigned share of all passages 0.663
(window 0.583; outside the 0.05 band), tightness 0.582 (window 0.632; below). **0.35
fails criteria (1) and (2).** The failure is over-assignment with looser topics, so the
next value under the rule is **0.40**, which addresses that cause. Interpolating the two
measured anchored runs (0.45: 0.308 assigned, tightness 0.649; 0.35: 0.663, 0.582)
suggests roughly 0.45-0.55 assigned and tightness about 0.61-0.62 at 0.40, i.e. it may
fail as well. If it does, the result is that no tested threshold gives anchored topics
window mode's coverage and tightness together; the comparison then proceeds at a value
chosen and stated in advance, with that trade-off reported, not a forced pass.
Other measured differences at 0.35 (not criteria): 72 candidates (0.45: 25; window:
115), one near-duplicate pair, 56% of topics outliving a window, 11.1 lasting births
per window, first-to-last member cosine 0.59.

*Confirmation result for 0.40 (measured, 2026-10-25; both modes run from one clean
checkout at 1b3113a; wording corrected in the review of db9e8aa, see below).*
Measured, with the criteria's basis (the median over 144 monthly windows of each
window's share): assigned share of all passages 0.475 (window 0.583; outside the
0.05 band); median topic tightness 0.611 (window 0.632; below). **0.40 fails
criteria (1) and (2).**

Stage accounting at 0.40, measured, as integer totals over all windows (from the
manifest's `anchor_calibration.totals`), with pooled shares (total / 994,341):

| stage | passages | pooled share | median per-window share |
|---|---:|---:|---:|
| all passages | 994,341 | 1 | |
| claimed by existing topics | 623,526 | 0.6271 | 0.616 |
| rejected by the size/issuer rule | 143,713 | 0.1445 | 0.149 |
| retained (claimed - rejected) | 479,813 | 0.4825 | |
| discovered in unclaimed text | 1,845 | 0.0019 | 0.001 |
| assigned (retained + discovered) | 481,658 | 0.4844 | 0.475 |
| unassigned | 512,683 | 0.5156 | 0.525 |

The totals reconcile exactly: 623,526 - 143,713 + 1,845 = 481,658 and
481,658 + 512,683 = 994,341. The median column does not (0.616 - 0.149 + 0.001 =
0.468, not 0.475) because each median comes from a different window: a median of
differences is not the difference of medians. That is a property of the summary,
not an accounting error. The earlier text called the medians "stage totals"; they
are not. The pooled assigned share (0.484) is not the criterion's basis. Window
mode's pooled share was not recorded at 1b3113a. It can be recomputed from
`windows.parquet` without a rerun, and `compare` now shows both.

At 0.40, discovery in unclaimed text added 0.19% of passages; nearly all coverage
came from existing topics' claims. This run says nothing about discovery at other
thresholds.

Measured anchored points (median assigned share, median tightness): 0.45 (0.308,
0.649), 0.40 (0.475, 0.611), 0.35 (0.663, 0.582). **No tested threshold met both
criteria.** Estimate only (linear interpolation between the tested points, not
measured): the coverage band would need a threshold near 0.385 or lower, with
tightness about 0.60; window tightness would need about 0.43 or higher, with
coverage about 0.37. On that estimate the two do not overlap between 0.35 and
0.45. Interpolation is not evidence about untested values. No further threshold
search is planned to firm this up.

Criterion (3), qualitative (one reader, 8 + 8 random passages per year from each
run's `coverage_samples.md`): both modes mix their samples. Each assigns some
housekeeping (option and equity tables, merger and underwriting agreements, call
closings), and each leaves substantive passages unassigned. The anchored unassigned
sample read as containing more substantive Q&A (process transitions, packaging,
export controls, product ramps). The reading does not separate the modes. It is a
judgement, not a measurement.

*Comparison value (stated before any adjudication, 2026-10-25): 0.40,* chosen by the
user as the most balanced tested value. On the median basis it is 0.108 below window
coverage and 0.021 below window tightness. The alternatives were 0.35 (0.080 above on
coverage, 0.050 below on tightness) and 0.45 (0.275 below on coverage, 0.017 above on
tightness). Every result carries the caveat that anchored topics cover fewer
passages: median per-window share 0.475 vs 0.583. Measured differences at 0.40 (not
criteria):
- 44 candidates (window: 115)
- 0 near-duplicate pairs (window: 28)
- 44.5% of topics outlive a window (window: 6.9%)
- 8.8 lasting births per window (window: 8.8)
- month-to-month member cosine 0.982 (window: 0.843)
- first-to-last member cosine 0.59 (window: 0.36)
- refit continuity 0.29, with 425 splits and 533 merges over 12 refits
- text stage 1,128 s (window: 246 s)

The runs at 1b3113a are the record of this confirmation, but not the comparison
itself. They were scored against the blank labels in force then, and they predate
the effective-universe provenance, so `compare` refuses them. The comparison uses
fresh runs of both modes at the stated value (see docs/EVIDENCE.md).

### Review fixes after 531575c (2026-10-25)

- **Calibration accounting** (`text/anchored.py`): explicit per-window stages and
  totals (see the correction above); empty-codebook and no-usable-passage windows get
  rows; both denominators reported.
- **Outcome calendars** (`eval/returns.py`): horizons count the benchmark's (US)
  sessions; each security trades on its venue's sessions (currency as exchange proxy):
  bought at the entry session's close if its venue traded, else at the venue's next
  session; sold at the exit session's close, else at its venue's last session before
  it. A missing quote on a day the venue traded is never filled. Before, a US basket
  flagged 2019-08-31 tried to enter on Labor Day (a Korea-only date) and was
  "insufficient_coverage". The momentum comparison had the same flaw: any foreign-only
  date in its lookback made US stocks ineligible for the null pool.
- **Successor valuation**: known stock terms are valued at the successor's exit close,
  else its close within 5 sessions (`converted_stale`), else unvalued; cash-only terms
  need no quote; the acquired company's last close is used only when terms are unknown
  (`delisted`, counted as an assumption). Before, a missing successor quote silently
  fell back to the acquired company's last close ("ok, +5%").
- **Comparison provenance** (`provenance.py`, `eval/compare.py`): manifests record a
  content hash of src/, the git commit and a dirty flag, the labels hash, lockfile,
  Python and key package versions, and the effective configuration. `compare` refuses
  missing provenance, uncommitted changes, non-development runs, and any difference
  except `text.topic_mode`; same-mode comparisons get distinct columns.
- **Coverage samples**: each run writes `coverage_samples.md` (text only) for the
  revised confirmation step.

### Review fixes after db9e8aa (2026-10-25)

- **Venue closures versus missing data** (`eval/returns.py: venue_sessions`).
  Before, a venue counted as closed on any date when none of its securities had a
  quote. A venue-wide data outage therefore passed as a holiday, and stale-price
  limits were skipped. Reproduced: two KRW securities quoted [100, 100, 110] then
  missing, SPY quoted, h = 15 gave `ok`, +10%, on an exit price 14 sessions old.
  Now a quote-free run of calendar sessions counts as a closure only if all of these
  hold:
  - the venue trades again afterwards;
  - the run lasts at most `MAX_CLOSURE_SESSIONS` (7);
  - at least 2 securities are quoted on both venue sessions bracketing it.

  Every other session is open, and a missing price there falls to the stale and
  unvalued rules. The same case now gives `unvalued_position`. Genuine witnessed
  holidays behave as before. Ordinary positions and acquisition successors use the
  same session sets, and cash-only conversions need no quote.

  Why not an exchange calendar: it would add a dependency whose historical holiday
  tables we would have to trust in turn. The inferred rule fails safe (unknown
  coverage counts as open, so the stale and unvalued rules apply).

  Costs:
  - A single-security venue (TWD: 2408.TW) never verifies a closure. Its holidays
    count as missing quotes: stale within 5 sessions, else unvalued. At entry the
    member is not bought (`no_entry`).
  - A venue-wide outage of at most 7 sessions, bracketed by data in a venue with 2+
    securities, is still indistinguishable from a holiday.

  The limit of 7 comes from the longest known closures of the tracked venues,
  counted in US sessions: Korea's Chuseok in October 2017 and Japan's Golden Week
  in 2019, 6 each. It affects outcome returns only, never topics or candidates.
- **Universe provenance** (`provenance.py`, `eval/compare.py`). Manifests now record
  `effective_universe`: the UniverseConfig object the run used, so programmatic
  overrides are captured. `compare` requires it and refuses any difference, naming
  the issuer and field. The universe.yaml file hash is still recorded but not
  compared: the effective object covers everything it would, ignores comment-only
  edits, and also catches overrides the file hash cannot see.
- **Reporting.**
  - Calibration summaries add `pooled_of_all_passages`, computed from the integer
    totals so the shares reconcile.
  - Diagnostics add `unassigned_share_pooled`.
  - `compare` shows pooled and median coverage. For runs made before this change,
    it computes the pooled figure from `windows.parquet`.
  - The 0.40 write-up above was corrected: medians are labelled as medians, the
    stage table uses integer totals, "no threshold" became "no tested threshold",
    and the interpolation is labelled an estimate.

### AI-assisted adjudication (declared 2026-10-26, before any adjudication result)

The user asked for Claude to help adjudicate. The protocol below was fixed before
any decisions were seen:

- **Where:** a local review page, `themegraph review RUN_DIR ...`
  (`src/themegraph/review/`), served on 127.0.0.1 with a per-session token. It
  reads and writes each run's `adjudication.yaml` directly and shows one candidate
  at a time. Claude's suggested decision comes from Claude Opus 5.5 (structured
  JSON output, effort medium, server-side refusal fallback on), with prompt
  version `adj-prompt-1`.
- **What Claude sees:** keywords, quotes, issuers and the first-flag date. For
  duplicate checks it also gets earlier candidates' keywords from the past 365
  days. It never sees returns, report.html, or labels other than the one theme
  named in a match entry. It is told not to consider prices or later events.
- **Blind sample:** a seeded random 20 unmatched candidates per run, the seed taken
  from the candidate ledger's hash and kept in `adjudication_review.json`. On these
  the server withholds Claude's suggestion until the person has decided, and it
  records the person's first answer as `blind_human_decision`.
- **Who decides what:**
  - Theme-match and negative-control entries are always decided by the person.
  - Unmatched candidates may be recorded from Claude's suggestion (`decided_by:
    ai`), but only once the blind sample is complete.
- **When Claude's decisions count:** in `themegraph compare`, only if the run's
  blind agreement is at least 80% on at least 20 entries
  (`compare.MIN_AGREEMENT`, `MIN_BLIND_PAIRS`). Otherwise the comparison reports
  adjudication as incomplete. The 80% bar was set before seeing any agreement and
  is not to be changed after.
- **Provenance:** each worksheet entry keeps `decided_by`, Claude's suggestion and
  reason, the model that answered and the prompt version, and the blind decision.
  (`themegraph adjudicate RUN --import FILE` also accepts the same fields from a
  JSON export.) Results report human and
  AI decisions separately.
- **Limitation:** for a match entry the theme name is necessarily visible, so those
  judgements are not blind to the label. That is why they stay with the person.

### Comparison round 1 (cmp2 runs, 2026-10-27): interim, adjudication incomplete

Measured from `themegraph compare runs/cmp2-window runs/cmp2-anchored`:
- **Blind agreement failed the bar** declared in advance: 45% (window) and 35%
  (anchored), each on 20 blind entries, against a bar of 80%. So Claude's 91 and 15
  bulk decisions do not count, and those entries must be decided by the person. The
  bar is not lowered.
- **Labelled themes (all decided by the person):** both runs confirm 1 of 6
  (`memory_supercycle_2016`).
  - Lead before consensus: 821 days (window) vs 424 days (anchored).
  - Auto-matches rejected on reading: window `chip_shortage_auto_2021` and
    `handset_5g_2019`; anchored `handset_5g_2019`.
- **Negative controls (decided by the person):** window 0 confirmed false alarms;
  anchored 1 (`crypto_mining_2021`).
- **Not yet a result:** unmatched noise, duplicates and real-but-unlabelled counts
  include Claude's decisions. The decision rule needs confirmed noise per year, so
  no method is chosen yet.
- **Open methodological question, raised by the user while reviewing:** analysts'
  questions share the `transcript:qa` genre with management's answers.
  `themegraph questions RUN` estimates their share in topics, candidates and quotes,
  computed from the saved runs. Whether to separate them is to be decided before the
  remaining manual decisions; separating them would require new runs and a new
  review.

### Evaluation status change: monitoring (2026-10-27)

The product is a theme monitor (docs/MONITOR.md) that reads data through the
current date. That includes 2022-01-01..2026-10-31, the period reserved as the
holdout.
- **The holdout is inspected.** From 2026-10-27 that period counts as inspected,
  not as an untouched holdout. A later formal holdout run would still be
  reproducible, but must not be described as out-of-sample evidence.
- **Clean evidence from now on:** the forward prediction log, and the monitor's
  `live` index days (observed after the registry's first refresh).
- **Research results stand.** The declared research results above are unchanged.
  The window vs anchored comparison stays open as research. The monitor uses
  window mode, the current default, and anchored mode remains experimental.

### Market layer v2 (specification change, 2026-10-28)

Decided by the project owner on 2026-10-28, before any result under it was seen.
`market.estimator: single_index` replaces v1 (`residual_lw`) in config/pipeline.yaml:

| | v1 (to 2026-10-27) | v2 |
|---|---|---|
| Returns | daily log returns summed onto the SPY calendar | simple daily returns linked geometrically onto the SPY calendar (each stock's own sessions compounded into the calendar step) |
| Market removed by | OLS residuals on SPY and SOXX within each window | the single-index model on SPY |
| Covariance | Ledoit-Wolf shrinkage of standardised residuals toward the identity | Ledoit-Wolf (2003) shrinkage of the return covariance toward the single-index model |
| Correlation used | correlation of the residuals | correlation of what the shrunk covariance leaves beyond the index |

Why: SOXX in the residual model removed the co-movement among semiconductor stocks
that a semiconductor theme should show, and does not apply to other sectors.
Clustering (average-linkage HAC on sqrt(2(1 - correlation)), cophenetic affinity)
and every use of affinity are unchanged.

Property to keep in mind: with a single-index target, every off-diagonal
beyond-index correlation equals the sample one times (1 - shrinkage). The intensity
is high in short windows (about 0.9 for 63-126 sessions with this universe size), so
levels are small. Rankings, and comparisons against random baskets of the same
segment mix, are unaffected; those are what the research tests and the dashboard use.

Results declared before this change (the comparison rounds, the threshold
confirmations and the adjudications) were produced with v1 and stand as recorded;
they are not recomputed. Runs record the estimator in their configuration hash.
`estimator: residual_lw` reproduces v1.

Numerical correction (2026-10-29, not a specification change): the beyond-index
correlation was computed as the shrunk covariance minus beta_i * beta_j * var(index).
At full shrinkage that subtraction leaves rounding noise (about 1e-16) where the
value is exactly zero, and the monitor ranked that noise against random baskets.
It is now computed from the index residuals directly ((1 - shrinkage) times their
sample correlation), which is algebraically the same and exactly zero at full
shrinkage. Companies with no residual variance are left out, an index without
variance raises, and the monitor gives no verdict when the comparison carries no
information (docs/MONITOR.md, "Trading together"). Shrinkage intensities and all
non-degenerate correlations are unchanged up to rounding.

### Headwind direction and theme families (declared 2026-10-29)

Declared before any result under them was seen, from the text alone. A theme is a
*headwind* in a month when at least 20% of its management and filing passages use
more problem language than growth language (fixed phrase lists in
`monitor/tone.py`), at least 2x the rate its genre mix gives in the same window, and
more than its positive share; *tailwind* symmetrically. Themes are grouped into
display families by average-linkage clustering of their meaning vectors cut at a
cosine of 0.85, the identity-matching bar. Neither changes which themes are
published, their baskets or their history. Hypothesis for the forward prediction
log, not yet registered: headwind themes' equal-weight baskets lag their benchmarks
over the following 3 and 6 months. No threshold here is to be adjusted using returns.

## 12. Open questions

1. Include the adjacency ring (§4.2) in v1? Recommended: yes, for text only.
2. Should the transcript and filing lag rules (`available_at`) be the same as
   before (two days for sources)?
3. Who adjudicates false alarms blind: you, a colleague, or both?
4. Development/holdout split at 2021-12: acceptable, or move it given which themes
   you most want in the holdout?
5. Data licensing: confirm that FMP terms allow storing transcripts in a private
   repo; otherwise raw data stays out of Git and only hashes are committed.
