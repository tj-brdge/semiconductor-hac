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
  - returns and excess over SPY and SOXX, since publication and over 1, 3, 6 and 12
    months;
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
  - With recorded terms, a holding becomes the acquirer's shares plus cash.
  - Without terms, it is held as cash at its last close, and flagged.
- **Membership changes.** A change must hold for 2 month-ends. It then starts a new
  version at its own entry close, at the previous index level. The index is
  chain-linked, so a company added later counts only from its own entry, never
  retroactively.
- **Basis.** Prices are USD and dividend-adjusted, so all returns are total returns,
  benchmarks included. Each index day is marked `reconstructed` (before the
  registry's first refresh) or `live` (observed afterwards).

## Data feed (schema version 1)

```
site/index.html                        dashboard (static; reads the feed)
site/data/latest.json                  {"schema_version", "release", "path"}
site/data/status.json                  last attempt per step, errors, data dates, stale flags
site/data/releases/<id>/index.json     overview: one summary row per theme, plus lists
                                       (emerging, established, market_gaining,
                                        market_losing, inactive)
site/data/releases/<id>/themes/<th>.json  name, description, keywords, status, route,
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

**Steps, each recorded in `status.json`.** If a step fails, later steps that need it
are skipped and the current release stays published. The dashboard shows the
failure and any stale data.
1. **Prices:** daily after the US close.
2. **Documents:** weekly. These are paid API calls (FMP, and embeddings for new
   passages); caches are reused.
3. **Themes:** only when a month-end has passed that the registry has not
   processed. Topics come from 180-day windows at month-ends, so recomputing them
   daily would add churn, not information. On the first run the last 24 month-ends
   are replayed; these are reconstructed, and are marked so.
4. **Valuation and export:** every run.

A rerun with the same data changes nothing in the registry.

**Suggested schedule.** Credentials stay in the job's environment (`FMP_API_KEY`,
`VOYAGE_API_KEY`, the SEC user agent) and are never written to the site.
```
30 22 * * 1-5  cd /path/to/semiconductor-hac && themegraph monitor refresh && <sync site/>
0  6  * * 6    cd /path/to/semiconductor-hac && themegraph monitor refresh --documents && <sync site/>
```
Here `<sync site/>` is any static upload, for example
`aws s3 sync site/ s3://bucket/ --delete` or `npx wrangler pages deploy site`.

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
