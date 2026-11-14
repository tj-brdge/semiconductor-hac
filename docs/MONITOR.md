# Theme monitor

`themegraph monitor refresh` keeps a persistent inventory of investment themes found
in earnings calls and SEC filings, values an equal-weight stock basket for each,
and publishes a static data feed and dashboard that any web host can serve.

## What it reuses

| Need | Reused | New |
|---|---|---|
| Topics per month-end | `pipeline.build` (window mode, the research default), passages, embeddings, caches | `monitor/clusters.py` reads one month-end at a time |
| Emergence signal | the candidate ledger's emergence rule | treated as one route in, not the only one |
| Prices | the store: dividend-adjusted closes, converted to USD at ingest | — |
| Trading calendars | `eval/returns.venue_sessions` (verified closures, fixed in #26) | daily valuation in `monitor/baskets.py` |
| Acquisitions | `universe.yaml` successor terms | applied daily |
| Ingestion | `ingest.py` jobs | called by the refresh |

The research artifacts, the declared results and `config/pipeline.yaml` are
unchanged. Monitor settings live in `config/monitor.yaml`, and none of them was
chosen by looking at basket returns.

## Inventory (`monitor/registry.py`)

**Identity.** Window-mode topics are re-clustered every month: the research runs
measured a median topic life of 2 months, so pipeline track IDs are not stable
enough to be theme IDs. The registry keeps its own identity instead. Each
month-end's topics are matched one-to-one to existing themes by either:
- shared member passages: consecutive 180-day windows share most of their text; a
  Jaccard overlap of at least 0.15 counts; or
- meaning, after a gap: a cosine of at least 0.85 between genre-centred member
  centroids.

A theme absent for up to 6 month-ends can still be matched (`match.lookback_snapshots`).
Until 2026-11-12 the code stopped at 5 (an off-by-one against this rule); themes
that returned after exactly 6 months before then were given a new ID and are not
re-joined.

**Lineage.** A second topic sharing text with a matched theme becomes a new theme
recorded as `split_from` it. A theme whose text went to another theme's topic is
marked `merged_into` that theme. IDs (`th-00042`) never change and published themes
are never deleted.

**Routes in.** Any one is enough:
- **Emergence:** the research emergence rule flagged the topic, with growth among
  comparable companies.
- **Established:** present in at least 6 of the last 9 month-ends, with 4 or more
  comparable companies and at least median coherence.
- **Newly covered:** a topic carried mostly by companies not yet covered at the
  3-month comparison base, meeting the established bar on their backfilled history
  (docs/TECH_PILOT_V2.md, "Handling new coverage"). Never shown as emerging. Only
  companies in coverage at the month-end count: a company whose `covered_from` is
  later has back-filled documents but cannot qualify a theme or enter its basket.

No route publishes a theme with nothing to buy: when none of its companies is in
coverage at the month-end, publication is blocked (`route_check.blocked`, event
`publication_blocked`) and retried at later month-ends.

Each unpublished topic keeps its latest `route_check` (months present, comparable
companies, coherence against the median, the newly-covered check), so what the
monitor did not publish can be reviewed.

A theme stays listed after the emergence rule stops firing.

**Status.**
- **Inactive:** absent for 3 or more month-ends in which it could have been seen. A
  month in which fewer than half of the companies that last discussed it have any
  documents is missing evidence: it neither counts as an absence nor ends the run.
  Every measure row says which it was: `observed: true` with `share: 0` when the
  documents were there and the theme was not found, `observed: false` with
  `share: null` when the evidence is missing. The discussion chart shows the
  second as a break in the line, never as zero. (Rows from registries written
  before this field existed are annotated the same way at export.)
- Otherwise, compare its share of discussion over the last 3 month-ends with the 3
  before, within a fixed company cohort: the companies covered and with documents at
  every one of the 6 month-ends. Theme passages from cohort companies divided by all
  passages from cohort companies, so adding or removing companies, or a company
  missing a month of documents, cannot move the ratio by itself:
  - **strengthening:** ratio of 1.25 or more;
  - **cooling:** ratio of 0.8 or less;
  - **stable:** in between.
  Each status change records its basis (cohort size, the theme's companies in it,
  the ratio). Without comparable evidence (no theme company in the cohort) the
  status is left as it was.
- **New:** not yet 6 months of history.

**Names.** A name is the top 3 keywords: point-in-time class-based TF-IDF of the
theme's passages against the rest of that window. The name changes only when fewer
than two of its words are still among the current top 8 keywords, and every change
is kept in `name_history`.

**Measures.** These are recorded per month-end and appended, never rewritten. There
is deliberately no combined score.
- **Emergence:**
  - share of all passages in the window;
  - change against 3 months earlier (`growth_3m`);
  - companies now, and those new in 3 months;
  - whether the emergence rule flagged it.
- **Theme strength:**
  - coherence: mean cosine of members to their centroid, in a run-wide
    genre-centred representation;
  - breadth: companies and segments;
  - persistence: month-ends present out of the last 12;
  - documents;
  - evidence mix.
- **Market strength:** from the basket index (below):
  - returns and excess over the benchmarks for the simulated period, the live
    period, the whole history and 1, 3, 6 and 12 months, each labelled by basis;
  - participation: constituents up over 3 months, and constituents beating SPY;
  - trend: the 3-month excess over SPY changed by 5 points or more since the last
    month-end.

**Evidence.** For each theme the registry keeps the passages closest to its centre,
one per company first. It stores those from the month the theme was first seen and
the latest ones. Each passage is labelled with its role:
- `management_remarks`;
- `management_answer`;
- `filing`;
- `analyst_question`: a Q&A turn by someone who gave no prepared remarks on that
  call. Analyst questions stay as evidence of investor attention and are labelled
  so they are not read as company statements.

## Basket method (`monitor/baskets.py`)

- **Constituents.** Every company whose exposure to the theme is at least 5% of its
  window text, core and adjacent alike. The basket shown is the basket valued, and
  the export refuses to publish otherwise.
- **Entry.** At the close of the first US session on a later UTC date than the
  version's `entry_after`:
  - for a live publication, the time of publication;
  - for a reconstructed one, the month-end.
  - "A later date", not "a later time": closes are stored with a nominal 22:00 UTC
    stamp but markets close earlier, so a refresh run during a session day buys at
    the next day's closes, never at one that may already have happened.
  - A company with no price rows at all is "not bought" too.
  - A company whose market was closed that day is bought at its next close. If it
    has no close within 5 sessions it is "not bought": its capital stays in cash and
    it is shown, never dropped.
- **Equal starting capital, buy and hold.** Share prices are never averaged.
- **Valuation and coverage.** Each day each holding is valued at its last close.
  Sessions its market traded without a close count against it:
  - fresh: 0 such sessions;
  - lagging: 1–5;
  - stale: more than 5.

  Each index day reports the share of basket value with a fresh price.
- **Acquisitions.**
  - With recorded terms, a holding becomes the acquirer's shares plus cash. Until
    the acquirer has a quote, the holding keeps its last value and counts as
    stale; a stale acquirer quote counts as stale too.
  - With cash-only terms, it becomes that cash (nothing left to price).
  - Without terms, it is held as cash at its last close, and flagged.
- **Membership changes.** The same proposed set must be observed at 2 consecutive
  month-ends (`pending` holds the proposal and its count). A different set starts a
  new proposal, a return to the current basket clears it, and a month without the
  theme resets it; each reset is logged. It then starts a new
  version at its own entry close, at the previous index level. The index is
  chain-linked, so a company added later counts only from its own entry, never
  retroactively. Once a theme is public, or its basket is bought after the current
  refresh, a change found while catching up on old month-ends (after a gap in
  refreshes) takes effect when it is decided, never backdated over days after
  publication or before the basket it replaces. A publication basket superseded
  within the refresh that published it (never held) is revised in place
  (`revised_before_entry`).
- **Invariants.** Every basket version has at least one company in coverage at its
  month-end, and each version's `entry_after` is no earlier than the one before;
  the registry refuses to write a version that breaks either. A version without
  companies left by an older registry is not valued: `performance.status` is
  `unavailable` with a `reason` and `note` (see "Repairing state").
- **Corporate actions.** Every price refresh downloads each symbol's full
  dividend- and split-adjusted history again (the provider restates it after each
  action), and the monitor values every basket from each symbol's latest download
  only. A symbol's history is never stitched from two downloads, so a split or
  dividend cannot leave a false jump; index levels of past days can move slightly
  when the provider restates a history. Mergers and delistings are recorded by hand
  in the universe file (`delisted`, `successor`).
- **Basis.** Prices are USD and dividend-adjusted, so all returns are total returns,
  benchmarks included. An index day is `live` when it falls after both the theme's
  recorded publication time (`published_at`) and the decision time of the basket
  version in force (`created_at`); otherwise it is `reconstructed`, a historical
  simulation of what the rule would have held. The dashboard calls these "Live"
  and "Simulated".
- **Returns** (`performance.returns`), each with `start`, `end`, `basis`
  (`live`, `reconstructed` or `mixed`) and, per benchmark, the benchmark return and
  `excess_vs_<B>` (basket minus benchmark, shown as percentage points):
  - `reconstructed`: the simulated stretch only, to the last simulated close;
  - `live`: the observed stretch only, from that close (or the entry close, if the
    theme was bought after publication) to the latest close;
  - `full_period`: since the first entry;
  - `1m`, `3m`, `6m`, `12m`: trailing windows, null without enough history.

  There is no "since publication" return that mixes the two without saying so.

## Data feed (schema version 1)

```
site/index.html, app.js, chart.js, styles.css   dashboard (static; reads the feed)
site/data/latest.json                  {"schema_version", "release", "path"}
site/data/status.json                  last attempt per step, errors, data dates, stale flags
site/data/releases/<id>/index.json     overview: one summary row per theme, plus lists
                                       (emerging, established, market_gaining,
                                        market_losing, cooling, inactive); each row
                                       has title, summary, key_constituents, returns
                                       per period and a weekly sparkline
site/data/releases/<id>/themes/<th>.json  title, summary, label (source, evidence),
                                       label_history, name (keyword name),
                                       description, keywords, status, route,
                                       dates, name/status history, lineage, evidence,
                                       measures, basket_versions, performance
                                       (series of {date, theme, SPY, SOXX, coverage,
                                        basis}, returns, constituents with weights,
                                        contributions and price status), market_history
```

**Publishing a release.**
- A release is written to a staging directory, checked, renamed, and only then made
  current by replacing `latest.json` atomically.
- The last 5 releases are kept.
- `status.json` is written on every attempt, including failed ones.

**Fields.**
- Fields may be added within version 1. Removing or renaming a field means version 2.
- Dates are ISO 8601. Returns are fractions (0.05 is 5%).
- Strict JSON: no NaN or Infinity. A measurement that is unavailable is `null`, with
  a reason where one exists (`performance.status`/`note`, `trading_together.*.note`,
  `measures[].observed`). A non-finite number is a calculation error: the export
  fails, the staging directory is removed, and `latest.json` keeps pointing at the
  last good release.

## Refresh and scheduling

```
themegraph monitor refresh                 # prices, documents when due, themes, valuation, export
themegraph monitor refresh --documents     # force a document download now
themegraph monitor refresh --prices-only   # prices only, never documents
themegraph monitor refresh --no-ingest     # download nothing; recompute from what is stored
themegraph monitor serve                   # http://127.0.0.1:8780/
themegraph monitor review                  # similar themes, families and tone (Markdown)
themegraph monitor refresh --fixtures --as-of 2021-06-30   # labelled synthetic data
```

**Steps, each recorded in `status.json`.** Every step is required: when one fails,
the later ones are skipped (listed under `skipped`), nothing is published, and the
previous release stays current. A theme-processing failure part-way through leaves
the saved registry as it was (it is written only at the end, previous file first
copied to `registry.prev.json`). The dashboard shows the failure and any stale
data. Labels (below) are the one optional step: a failure there is a `warning`
and the release goes out with earlier or automatic labels.
1. **Prices:** daily after the US close.
2. **Documents:** weekly. These are paid API calls (FMP, and embeddings for new
   passages); caches are reused.
3. **Themes:** only when a month-end has passed that the registry has not
   processed. Then **labels** for themes that need one (see "Theme labels"). Topics come from 180-day windows at month-ends, so recomputing them
   daily would add churn, not information. On the first run the last 120 month-ends (10 years, `bootstrap_snapshots`)
   are replayed; these are reconstructed, and are marked so. The replay is only as
   long as the documents: a company counts from its first document, so in the early
   years fewer companies are observed, and months without a theme's companies'
   documents are missing evidence rather than zero discussion.
4. **Valuation and export:** every run.

A rerun with the same data changes nothing in the registry.

**Month-end completeness.** Prices and themes can be refreshed daily but documents
are downloaded less often, and history is append-only: a month-end processed before
its documents were downloaded would stay incomplete for good. So a month-end `t` is
processed only when the documents were downloaded at or after `t` +
`completeness.settle_days` (5):
- The **document watermark** is the time of the latest download of each document
  source (transcripts, EDGAR filings) that covers at least 90% of this profile's
  companies that have documents in that source; the earlier of the sources counts,
  so both must have been downloaded. A partial batch, or another profile's smaller
  download in the shared store, does not move it.
- Until then the month-end is **pending**, with every later one. The refresh still
  succeeds and still publishes the daily price update, but themes stop at the last
  processed month-end, and `status.json` and the release list `pending_snapshots`
  with the reason and the date a download is needed after. The dashboard shows a
  notice. A pending month-end is processed by the first refresh after a qualifying
  download (`--documents`), also with `--no-ingest`; the first build follows the
  same rule, so its latest month-end may wait.
- A failed download fails the refresh: later steps are skipped, nothing is
  processed or published.
- Each processed month-end records `processed_at` and the watermark it was built
  from (`snapshot_meta`).
- **Late documents.** A document eligible for a processed month-end but first
  downloaded after it was processed is not added to that month-end. It is counted
  in `late_documents` (status and release, by month-end, with the companies), and
  it is used by every later month-end whose 180-day window still contains it.
- **What this does not prove.** No provider reports that a month is complete. The
  rule is a settling period plus evidence that a download was made after it; a
  transcript published later than that still arrives late and is reported as such.
  Fixture data carries no download record and is treated as complete.

**One command.** A plain `refresh` downloads prices every time and documents when
they are due: no full download yet, the last one is `completeness.refresh_days` (7)
old, or the latest month-end has settled since it. The documents stage in
`status.json` then starts with "automatic" and the reason. So a daily `refresh` is
the whole schedule; `--documents` and `--prices-only` override it.

**Prices that did not update.** A symbol the provider returns nothing for keeps
its old prices. The prices stage in `status.json` names such symbols with the
provider's message, and `price_gaps` (status and release) lists every covered
company or benchmark whose latest close is more than
`freshness.price_stale_sessions` (3) of its own market's sessions behind, or that
has no prices. The dashboard shows a notice and the Method page the list.

**Suggested schedule.** Credentials stay in the job's environment (`FMP_API_KEY`,
`VOYAGE_API_KEY`, the SEC user agent) and are never written to the site.
```
30 22 * * 1-5  cd /path/to/semiconductor-hac && themegraph monitor refresh ; <sync site-tech-v2/>
```
Documents are downloaded by that same job when due, so a month-end is processed
by the first weekday run at least five days after it.
Sync with `;`, not `&&`: a failed refresh exits 1, and the site still has to be
uploaded so that visitors see the new `status.json` (the failure notice) next to
the unchanged last good release.
Here `<sync site-tech-v2/>` is any static upload, for example
`aws s3 sync site-tech-v2/ s3://bucket/ --delete` or `npx wrangler pages deploy site-tech-v2`.

## Headwinds (`monitor/tone.py`)

Growth in discussion says a subject is getting attention, not whether it is good or
bad news. For short ideas the monitor also records what kind of news a theme is.

- **Passages.** Each passage is scored with two fixed phrase lists of business
  language written for this monitor: problem language (weaker demand, inventory
  correction, destocking, pricing pressure, margin compression, cancellations,
  push-outs, lowered guidance, impairments, layoffs, budget cuts, churn, ...) and
  growth language (strong demand, record revenue, raised guidance, share gains,
  ramp, momentum, ...). A passage is negative when it has more problem phrases than
  growth phrases, positive for the reverse. Ambiguous words are left out
  ("uncertainty" is in every safe-harbour statement; "shortage" is often good for
  pricing).
- **Themes, per month-end** (`measures[].tone`): over the theme's management and
  filing passages (analyst questions are attention, not company statements), the
  share that is negative and positive, compared with the share its mix of genres
  would give at that window's own rates (filings are compared with filings: risk
  factors are negative everywhere).
- **Direction** (`tone` in config/monitor.yaml, declared 2026-10-29 from the text
  alone, before any return was looked at): `headwind` when at least 20% of its
  company passages are negative, at least 2x the genre-matched rate and more than
  the positive share; `tailwind` by the same rule for positive passages; `mixed`
  otherwise; `unknown` with fewer than 20 company passages.
- **Overview.** A *Headwinds* section lists active headwind themes, spreading ones
  first (growth in discussion among comparable companies, then how far above the
  usual rate). Each theme appears in one section only, so headwinds are not also
  listed as emerging or established. Cards show the phrases matched most often.
- **Forward test.** Each time a published theme turns headwind at a month-end
  processed live, the registry appends one record to `headwind_log` (theme, basket
  held, decision time, benchmarks). The test that will be read from it is fixed in
  DESIGN.md, "Headwind forward test".
- **Limits.** Phrase lists do not understand negation or context ("no sign of
  weakness" counts as problem language); the matched phrases and the quoted
  passages are on the page so a reader can check. Whether headwind baskets go on to
  lag their benchmarks has not been tested; that belongs in the forward prediction
  log, declared before any result, not in tuning these thresholds.

## Theme families (`monitor/families.py`)

Topics are re-clustered every month, so one broad subject can appear as several
themes. They are grouped for display only: theme IDs, baskets, measures and history
are unchanged, and nothing is merged.

- **Similarity:** cosine of the themes' latest meaning vectors (genre-centred member
  centroids, the vectors identity matching uses after a gap).
- **Grouping:** average-linkage clustering of active published themes, cut at
  `families.min_cosine` (default 0.85, the identity bar: themes this close would be
  matched as one theme if they were not present at the same time). Average linkage
  does not chain two unrelated themes through a third.
- **Lead:** the most persistent theme in the family, then the one with the most
  companies, then the oldest.
- **Overview:** one card per family; a family shown in an earlier section is not
  repeated, and the card links to the overlapping themes. The theme page lists them
  with their similarity and shared companies. The all-themes list shows every theme.
- **Setting the cut:** `themegraph monitor review [--profile P]` writes
  `<state>/review.md`: how many pairs of active themes are at or above each cosine,
  the most similar pairs with shared companies and keywords, the families formed,
  and every theme's tone with its matched phrases. It reads text measures only. The
  cut is chosen by reading those pairs (are they the same subject?), never from
  returns, and recorded with its date if changed.

## Forward tests (`monitor/forward_tests.py`)

The only clean evidence that anything here predicts returns. Three claims are
registered in DESIGN.md (headwind baskets lag SPY; emergence baskets beat it;
tailwind baskets beat it). The registry logs an event, with the basket held, each
time the condition is met at a month-end processed live (`headwind_log`,
`forward_log`); replayed history is never logged.

```
themegraph monitor forward [--profile P]             # progress: counts only, no returns
themegraph monitor forward --read headwind           # the one read, when 20 outcomes are usable
```

- **Ready** means 20 counted events with a completed 126-session window and a
  primary outcome that can be computed; completed windows without one are listed
  with the reason (for example, no benchmark price) and do not count.
- **A read** stores the raw result once: valid outcomes, exact ties, the non-tied
  outcomes the sign test uses, the wins among those, the median and the nominal
  one-sided p-value. It never changes afterwards.
- **Supported or not** is decided only by Holm's correction across the three
  primary p-values, derived when all three tests have been read. Until then every
  p-value is shown as nominal and unadjusted and no claim is called supported.
- **Independence.** The sign test treats counted events as independent. The overlap
  rule removes events sharing half their companies within the window; baskets that
  share fewer companies, a sector or the same market months remain related, and the
  correction for three tests does not address that.

The Method page shows the same table and wording (`forward_tests` in `index.json`). A rebuild from scratch starts new, empty
logs: events are only ever logged by live refreshes, so keep the state directory
once the monitor is live (a rebuilt registry cannot recover them).

## Market clusters (`monitor/market_clusters.py`)

The market layer's hierarchical agglomerative clustering (HAC) of stock returns is
used by the research only as a similarity score and never cut. The dashboard's
*Market clusters* page cuts it, to show which monitored stocks are trading together
right now. Prices only; it changes nothing in the registry and selects no theme.

- **Window and estimator:** the last 126 sessions, companies with their own close on
  at least 90% of them, each pair's correlation beyond SPY from the single-index
  model (the same as "Trading together").
- **Tree:** average linkage on 1 - correlation. Shrinkage scales every off-diagonal
  correlation by the same factor, an affine change of distance, so average linkage
  gives the same tree with or without it; both levels are reported.
- **Cut, with no number of clusters chosen:** each company's residual returns are
  shuffled in time (200 times, fixed seed), keeping each company's returns but
  removing any co-movement, and the highest pairwise correlation of each shuffle is
  recorded. The cut is the 95th percentile of that maximum: a cluster only joins
  companies, or groups, whose average correlation beats the strongest pair random
  data produce in 95% of shuffles, across all pairs at once. Companies joined below
  it are listed as in no cluster.
- **Page:** each cluster with its companies, sectors, average correlation and the
  theme baskets holding two or more of its companies; the full tree with the cut
  line, clusters as labelled bands (no colour coding, so any number of clusters
  stays readable), and a tooltip on every join and ticker. Feed: `market_clusters`
  in `index.json` (`pool`, scipy-style `merges` as [left, right, correlation, size],
  `leaves` in tree order, `clusters`, `cut`).

## Network (`monitor/network.py`)

The dashboard's *Network* page draws the covered companies as points with two kinds
of link, which can be switched on and off separately:
- **Trading together** (solid): the pair's correlation beyond SPY over the last six
  months is above the market-cluster cut, i.e. stronger than the strongest pair that
  shuffled returns produce.
- **Discussing the same theme** (dashed): both companies discuss the same active
  theme this month; pairs sharing more themes are listed first.

Positions are computed with the release (a small force-directed layout with a fixed
seed), so the page has no layout code and a release always draws the same picture.
Companies that sit close together are linked, directly or through others; distance
means nothing else. Selecting a theme, a sector or a company highlights it and its
links, and a company's panel lists its themes, the companies it trades with and
those it shares themes with. Feed: `network` in `index.json` (`nodes` with `x`, `y`,
`degree`, `cluster`, `themes`; `price_links`; `discussion_links`). It describes the
latest window only and feeds no measure.

The research report's multi-layer graph (`report.html` in a run directory) is a
separate, historical view with a time slider; see "Research commands for another profile" in the README.

## Theme labels (`monitor/naming.py`)

Topics are named by keywords ("ethernet · transceivers · enterprise"); readers get a
title and a one- or two-sentence summary. Each published theme takes its label from
the first source that applies:

1. **Curated:** `config/theme_labels.yaml`, by theme ID
   (`th-00004: {title: ..., summary: ..., by: ...}`). For human corrections.
2. **Claude:** written from the theme's quoted passages, on the machine that runs
   the refresh (`pip install -e '.[review]'` and `ANTHROPIC_API_KEY` in the job's
   environment; never in the browser). Claude must cite at least two passages from
   two companies among those it was shown, keep the title to 8 words, describe
   discussion rather than stocks, and may answer that the evidence is ambiguous.
   An answer that fails any check is not used.
3. **Automatic:** a title from the top three keywords and a factual summary (how
   many companies, which sectors, which kind of text). It claims no thesis, and the
   page says it is automatic.

To turn written labels on: `pip install -e '.[review]'` and set `ANTHROPIC_API_KEY`
in the environment of the machine that runs the refresh. `labels.writer: claude`
(the configured value) reports a missing key or package as a warning in
`status.json`, and titles fall back to keywords until it is fixed.

Titles are asked to say what the subject is and what is happening to it in four to
nine words, and the summary's first sentence to say what the companies are
discussing (`PROMPT_VERSION` 2, 2026-11-14). Themes labelled by Claude under an
earlier version are labelled again once; curated labels are never replaced.

A label is written once per theme and keyword name, so reruns make no calls; a
theme whose keywords drift (the registry renames it) gets a new one. Every label is
kept in `label_history` with its source and time. IDs never change. Settings:
`labels` in `config/monitor.yaml` (`writer: auto | claude | keywords`, model, a
per-refresh cap). Fixture runs never call Claude.

## Dashboard (`monitor/site/`)

Plain HTML, CSS and two small ES modules; no framework, no build step and no
third-party scripts, so the `site/` directory can be copied to any static host.
Views: overview (emerging themes, headwinds and established themes as cards, one per
family; relative-performance lists; cooling and inactive themes), companies, the
company network, market clusters (the stock-return HAC cut into groups), all themes (search, filters, sorting), a theme
page (chart with ranges and keyboard/pointer readout, returns by period and basis,
companies, evidence, disclosures for method, membership, names and diagnostics), and
method & data (coverage, freshness, the last refresh's stages, release ID).

- **Companies** (`#/companies`, `companies` in `index.json`): every covered company
  with the active themes it belongs to, searchable by ticker, sector or theme. A
  filled tag is a basket holding; an outlined tag is a company that discusses the
  theme this month without being in the basket (a change needs two month-ends).
- **In their own words**: each card and theme page shows one passage extracted from
  the evidence (`quote` in a theme's summary row): the passage closest to the
  theme's centre, a company statement where there is one, cut at a sentence end and
  attributed. It is never rewritten, so it is available without written labels.
- One return period (3, 6 or 12 months) is used across the overview and the list.
  A theme without that much price history shows how much it has instead.
- Simulated and live returns are labelled everywhere ("Simulated", "Live",
  "Simulated + live"); live days are shaded on charts.
- **About this theme**: a longer summary built only from recorded data (what the
  theme's distinctive terms are; who discusses it and which companies carry most of
  it; how it developed, with each change in status; where the evidence comes from;
  and, in its own paragraph, how the stocks did). Written titles and summaries stay
  above it; Claude-written summaries may run to four sentences.
- **Strengthening and weakening points**: every status change that marks a change of
  pace (strengthening ▲, recovered ▲, cooling ▼, growth levelled off ▼, went quiet ▼),
  with the cohort ratio that decided it, on a monthly chart of the theme's share of
  all monitored text and on the basket chart (at the session after the month-end).
  Labels are drawn where they fit; every point is also in the readout and the list
  under the chart.
- Theme charts mark events with arrows: the first month the emergence rule flagged
  the theme, the month it entered the monitor (when different), and later basket
  versions, each at the session that acted on it. The basket starts when the theme
  is decided, so for a theme that entered by emergence the arrow is at the chart's
  first day; earlier events are listed under the chart, not drawn.
- **Hindsight (opt-in).** A theme page can show the six months before the basket
  started: the first basket's companies held equally from the window's first close,
  scaled to meet the basket at 100 on its entry close, with the benchmarks on the
  same basis, as a dotted line on a grey band. The companies were chosen with
  information from the entry date, so nobody could have held this; it lives in
  `performance.hindsight`, separate from `series` and `returns`, and never enters a
  return figure or the headline numbers (`baskets.hindsight`). It answers "had these
  stocks already moved before the theme was found?"
- Discussion measures and stock measures are shown side by side, never combined.
  "Relative performance improving/weakening" (feed: `market_gaining`/`losing`):
  the basket's 3-month return minus the first benchmark's changed by at least
  `market.trend_threshold_pp` points since the previous month-end.
- Chart colours are a validated categorical palette (basket blue, benchmarks orange
  and green, dashed); identity is also given by the legend, end labels and the
  readout, and a table view lists the values.

## Profiles (`config/profiles/`, `monitor/profiles.py`)

**One monitor.** Since 2026-11-14 every command without `--profile` uses `tech-v2`:
all 127 companies (the 76 semiconductor-pilot companies plus software,
cybersecurity and data/cloud), one download of prices and documents, one registry
(`monitor_state-tech-v2/`) and one site (`site-tech-v2/`). The profile keeps its
name and directories so the registry, forward-test logs and site already built
carry on. Before that date the default was the semiconductor-only pilot, and the
two were refreshed separately: a refresh of one downloaded prices and documents
for its own companies only, so the other fell behind.

`semis` remains as the semiconductor pilot (`monitor_state/`, `site/`): the research
protocol (development runs, the holdout, its forward log) is defined on that
universe, so `themegraph run --period holdout` needs `--profile semis`. Its monitor
no longer needs refreshing; its companies and themes are all in the one monitor.

A profile names its universe, pipeline and monitor files, its registry (state)
directory and its site directory.

- `--profile NAME` on every `themegraph monitor` action and on the research
  commands (`ingest`, `coverage`, `run`, `topics`, `forward`, `report`).
- Universe and monitor files can `extends:` a base file, so the technology universe
  adds companies to the semiconductor universe without copying it.
- The document and price store is shared; every read is limited to the profile's
  universe (`load_documents/load_prices(..., universe=)`), including the research
  commands, which read the default universe. Embedding and cluster caches are
  content-addressed (text hash and model; passages, vectors and settings) and are
  shared safely. Raw provider responses are shared too.
- `fork_from`: the first refresh copies the source profile's registry (IDs, basket
  versions, history) and records `fork`; afterwards the registries are independent.
  `id_prefix` names the profile's new themes.
- Every release and status file records `effective`: the profile, the universe name
  and version, and each configuration file used with its SHA-256.

**Run metrics.** status.json records seconds per stage, the process's peak memory,
and counts of provider requests, raw-cache hits, embeddings computed and reused, and
cluster windows computed and reused (`themegraph/usage.py`; counts only).

## Trading together (`monitor/comovement.py`)

Whether a theme's stocks move as a group beyond the market. Prices only; it is
shown beside the basket's returns and never affects which themes are published.

- Returns: simple daily returns on the benchmark calendar, linked geometrically
  (a company's last close is carried over days without one, so a move across a gap
  lands on its next close and compounds to the true price change).
- Covariance: all monitored companies with their own close on at least 90% of the
  sessions, over the last 126 sessions and the 126 before, shrunk toward a
  single-index model on SPY (Ledoit & Wolf 2003, `market.hac.single_index_shrinkage`,
  the same estimator as the research market layer v2).
- Measure: the average correlation beyond SPY among the companies in the current
  basket, compared with 1,000 random baskets of the same size, drawn from the same
  segments where possible (`eval.comovement.matched_null_sets`, seeded per theme so
  reruns agree). Stored as counts: `below`, `ties` (within 1e-9), `above`, plus
  `percentile` = (below + ties / 2) / draws, `same_segment_share` and
  `segment_matched` (every slot from the member's own segment). The page and the
  summary state the counts ("more than 993 of 1,000, and the same as 6") and say
  "same segment mix" only when `segment_matched`. `verdict`: "together" at a
  percentile of 0.9 or above, "less" at 0.1 or below, "usual" otherwise (wording
  bands only).
- No verdict (`informative: false`, `percentile: null`, a `note`) when the
  comparison carries no information: shrinkage of 1 (the model puts all
  co-movement on the index, so every beyond-index correlation is exactly zero);
  the index did not move in the window; or every random basket ties. A company the
  index explains completely (no residual variance) is left out, like one with too
  few closes. Beyond-index correlations are computed from the index residuals
  directly, not by subtracting the market part from the shrunk covariance, which
  left rounding noise (about 1e-16) that the comparison then ranked; baskets are
  ranked on the unshrunk residual correlation, which orders them exactly as the
  shrunk values do whenever shrinkage is below 1.
- With a single-index target every beyond-index correlation is the sample value
  times (1 - shrinkage), and the shrinkage is high over six months, so the level is
  small and only the comparison is used for the verdict. When most random-basket
  slots cannot come from the theme's own segments, the page says that sector
  co-movement may explain part of it.

## Universe changes (`config/universe.yaml`, `monitor/coverage.py`)

v1 covers the semiconductor pilot universe. **"Strongest" always means strongest
among the monitored companies**, and the dashboard states which companies those
are: universe name, version, company count by sector, benchmarks, and any
recently added companies. Broader coverage is a later milestone; the boundaries
are configuration, not code:

| Configured in `config/universe.yaml` | Used by |
|---|---|
| `issuers` (symbol, segment, role, currency, `covered_from`, `covered_until`) | ingestion, topic detection, baskets |
| `sectors` (segment → sector label) | theme sector spans, constituent sectors, the coverage statement |
| `benchmarks` | ingestion and every comparison column and chart line (the first is the trading calendar) |
| `name`, `description`, `version`, `changes` | the coverage statement and the feed |

**Themes are not sectors.** A theme is a topic in the text; its companies can come
from any sector, and each theme lists the sectors it spans (for example,
AI build-out across memory, networking and power). Adjacent companies (role
`adjacent`) are excluded from the research return baskets but **included** in the
monitor's baskets, so the displayed company list and the calculated basket match.

**Adding a company.** Add the issuer with `covered_from: <date>`, append a change
`{version, date, added: [...]}`, set `version` to that change's version, then run
`themegraph monitor refresh --documents`. `load_universe` refuses a change log that
disagrees with the issuers' dates, or segments without a sector.

**Removing a company.** Do not delete its entry; set `covered_until: <date>` and
record `removed: [...]` in a change. Its history stays, and new basket versions no
longer include it. Existing versions keep holding it under the basket rules.

**Comparable companies.** Back-filled documents for a newly added company must not
look like accelerating adoption. A company counts in growth, new-company counts
and both routes into the inventory at month-end t, against the baseline 3 months
earlier, only if it was covered at the baseline, is covered at t, and its first
document is at least `coverage.min_history_months` (12) before t. Other companies
are still shown on the theme, labelled *recently added*, and are bought into new
basket versions from their own entry date. The emergence route also requires
comparable-company growth of at least `coverage.min_growth_ratio` (1.25×) where a
baseline exists. Measures and basket versions written before a change are never
recomputed, and each measure row and basket version records the universe version
it was computed under.

**Live expansions.** A newly-covered theme whose new companies' `covered_from` is on
or after the monitor's go-live date is a live expansion: its basket is bought at
the first close after the refresh that published it, also when that refresh ran
late and processed an old month-end (`live_window_days` does not apply), and its
days are `live`. The month-end stays recorded as `published_snapshot`, the evidence
date. Coverage dates inside a build's replayed history keep the reconstructed
month-end entry, like every other replayed theme.

## Repairing state

Registries are append-only and the fixes above apply to what is written from now
on. Earlier state is not rewritten; the export says what it cannot value:
- **Empty basket versions** (possible only from the coverage-expansion path before
  2026-10-29): the theme shows "Not valued" and `performance.reason` is
  `empty_basket`.
- **Backdated newly-covered entries** (a live expansion processed more than 35 days
  late before 2026-10-29): `route` is `newly_covered`, `basis` is `reconstructed` and
  version 1's `entry_after` is a month-end before `published_at`.
The default build of both profiles (full history, no `covered_from` after go-live)
cannot produce either. If a state directory has them, rebuild it: move the state and
site directories aside (`mv monitor_state-tech-v2 monitor_state-tech-v2.old; mv
site-tech-v2 site-tech-v2.old`) and run the refresh again; the replay rebuilds the
history and the previous directories stay available for comparison.

## Limits and decisions

- **Survivorship.** The universe is today's list of companies, so reconstructed
  history leaves out companies that no longer exist. Only `live` index days are
  free of this bias.
- **Holdout.** The monitor reads data through today, which includes the
  2022–2026 holdout. From 2026-10-27 that period counts as inspected (DESIGN.md).
- **No human review needed.** Publication does not wait for adjudication.
  Adjudication remains a research tool for measuring how well the routes work.
- **Window mode for topics.** The anchored method stays experimental. The registry's
  own matching provides the stable identities that anchored mode would otherwise
  supply.
- **Fixtures.** `--fixtures` runs the full path on synthetic companies and prices,
  and the dashboard says so on every page.
