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

A theme absent for up to 6 month-ends can still be matched.

**Lineage.** A second topic sharing text with a matched theme becomes a new theme
recorded as `split_from` it. A theme whose text went to another theme's topic is
marked `merged_into` that theme. IDs (`th-00042`) never change and published themes
are never deleted.

**Routes in.** Either route is enough:
- **Emergence:** the research emergence rule flagged the topic.
- **Established:** present in at least 6 of the last 9 month-ends, with 4 or more
  companies and at least median coherence.

A theme stays listed after the emergence rule stops firing.

**Status.**
- **Inactive:** absent for 3 or more month-ends.
- Otherwise, compare its mean share of discussion over the last 3 month-ends with
  the 3 before:
  - **strengthening:** ratio of 1.25 or more;
  - **cooling:** ratio of 0.8 or less;
  - **stable:** in between.
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
- **Entry.** At the close of the first US session after the version's `entry_after`:
  - for a live publication, the time of publication;
  - for a reconstructed one, the month-end.
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
- **Membership changes.** A change must hold for 2 month-ends. It then starts a new
  version at its own entry close, at the previous index level. The index is
  chain-linked, so a company added later counts only from its own entry, never
  retroactively. Once a theme is public, a change found while catching up on old
  month-ends (after a gap in refreshes) takes effect when it is decided, never
  backdated over days after publication.
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

## Refresh and scheduling

```
themegraph monitor refresh                 # prices + themes (if a month-end passed) + valuation + export
themegraph monitor refresh --documents     # also transcripts and filings (weekly)
themegraph monitor refresh --no-ingest     # recompute from what is already stored
themegraph monitor serve                   # http://127.0.0.1:8780/
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
   daily would add churn, not information. On the first run the last 24 month-ends
   are replayed; these are reconstructed, and are marked so.
4. **Valuation and export:** every run.

A rerun with the same data changes nothing in the registry.

**Suggested schedule.** Credentials stay in the job's environment (`FMP_API_KEY`,
`VOYAGE_API_KEY`, the SEC user agent) and are never written to the site.
```
30 22 * * 1-5  cd /path/to/semiconductor-hac && themegraph monitor refresh ; <sync site/>
0  6  * * 6    cd /path/to/semiconductor-hac && themegraph monitor refresh --documents ; <sync site/>
```
Sync with `;`, not `&&`: a failed refresh exits 1, and the site still has to be
uploaded so that visitors see the new `status.json` (the failure notice) next to
the unchanged last good release.
Here `<sync site/>` is any static upload, for example
`aws s3 sync site/ s3://bucket/ --delete` or `npx wrangler pages deploy site`.

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

A label is written once per theme and keyword name, so reruns make no calls; a
theme whose keywords drift (the registry renames it) gets a new one. Every label is
kept in `label_history` with its source and time. IDs never change. Settings:
`labels` in `config/monitor.yaml` (`writer: auto | claude | keywords`, model, a
per-refresh cap). Fixture runs never call Claude.

## Dashboard (`monitor/site/`)

Plain HTML, CSS and two small ES modules; no framework, no build step and no
third-party scripts, so the `site/` directory can be copied to any static host.
Views: overview (emerging and established themes as cards, relative-performance
lists, cooling and inactive themes), all themes (search, filters, sorting), a theme
page (chart with ranges and keyboard/pointer readout, returns by period and basis,
companies, evidence, disclosures for method, membership, names and diagnostics), and
method & data (coverage, freshness, the last refresh's stages, release ID).

- One return period (3, 6 or 12 months) is used across the overview and the list.
  A theme without that much price history shows how much it has instead.
- Simulated and live returns are labelled everywhere ("Simulated", "Live",
  "Simulated + live"); live days are shaded on charts.
- Discussion measures and stock measures are shown side by side, never combined.
  "Relative performance improving/weakening" (feed: `market_gaining`/`losing`):
  the basket's 3-month return minus the first benchmark's changed by at least
  `market.trend_threshold_pp` points since the previous month-end.
- Chart colours are a validated categorical palette (basket blue, benchmarks orange
  and green, dashed); identity is also given by the legend, end labels and the
  readout, and a table view lists the values.

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
