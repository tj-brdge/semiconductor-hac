# Theme monitor: method

How each part of the monitor works today. What changed, and when, is in `docs/DECISIONS.md`.

Settings such as `status.cooling_ratio` are keys in `config/monitor.yaml` unless another file is
named; `config/tech_v2/monitor.yaml` extends it and changes only `market`. Source files are in
`src/themegraph/monitor/`. No setting was chosen by looking at basket returns.

## Themes and the registry

A theme is a topic in company text that the monitor follows from month to month. The registry keeps
each theme's identity, history, evidence and baskets, and only appends (`registry.py`,
`clusters.py`).

- **Input.** Topics from the research pipeline in window mode: passages of calls, filings and listed
  IR documents from the 180 days before each month-end (`text.window_days` in
  `config/pipeline.yaml`).
- **Tracked topics** (`pool`). At least 3 companies with exposure (the theme's share of a company's
  window text) of 0.05 or more, and no company with over half of the passages (`min_issuers`,
  `basket.min_exposure`, `max_issuer_share`). An unpublished topic unseen for more than 6 month-ends
  is dropped.
- **Identity** (`match`). Topics are matched one-to-one to themes on either test: Jaccard overlap of
  member passages of at least 0.15 (`min_jaccard`), or cosine of at least 0.85 between genre-centred
  centroids (`min_centroid_cosine`). A theme absent for up to 6 month-ends still matches
  (`lookback_snapshots`).
- **Lineage.** An unmatched topic sharing text with a matched theme is a new theme, `split_from` it;
  an unmatched theme whose text went to a matched topic is `merged_into` it. IDs never change and
  published themes are never deleted.

**Routes into the published list.** One is enough. For "comparable" and "covered" see "Coverage and
universe changes".

| Route | Rule |
|---|---|
| `emergence` | The research emergence rule (`candidates` in `config/pipeline.yaml`) flagged the topic this month, it has at least 3 comparable companies, and its 3-month growth among comparable companies is at least 1.25 (`coverage.min_growth_ratio`) or has no baseline. |
| `established` | Present in at least 6 of the last 9 month-ends (`established.present`, `of_last`), at least 4 comparable companies (`min_issuers`), coherence at or above that month's median. |
| `newly_covered` | At least half of the topic's covered companies were not covered 3 month-ends earlier, and it meets the established bar on back-filled history: 4 covered companies, median coherence, and present in 6 of the last 9 month-ends when followed back with the identity tests. Never shown as emerging. |

A topic with no covered company is not published and is tried again later. Each unpublished topic
keeps its latest `route_check` for review. A published theme stays listed after its route stops
applying.

**What is recorded.**
- **Keyword name.** The top 3 of 8 keywords (class-based TF-IDF against the rest of the window). It
  changes only when fewer than two of its words are among the current 8 (`name_history`).
- **Measures.** One row per theme per month-end, never rewritten, no combined score: emergence (next
  section), strength (coherence, companies, sectors, months present of the last 12, documents,
  evidence roles) and tone.
- **Evidence.** Up to 5 passages (`evidence.quotes`) closest to the theme's centre, one per company,
  from the first month and the latest. Roles: `management_remarks`, `management_answer`, `filing`,
  `ir_document`, `analyst_question` (a Q&A speaker who gave no prepared remarks; never shown as a
  company statement).

**When a month-end is processed** (`completeness`, `refresh.py`). Once, and only after documents
were downloaded at or after the month-end plus 5 days (`settle_days`).
- A download counts when it covers at least 90% of the companies with documents in that source
  (`min_symbol_share`). Transcripts and EDGAR filings must both qualify; the earlier time is used.
- Until then that month-end and later ones are pending; prices and the release are still updated. A
  document downloaded after its month-end was processed is not added to it (`late_documents`).
- The first build replays the last 120 month-ends (`bootstrap_snapshots`).

## Status and emergence

Status says whether discussion of a theme is growing, steady or fading: text, not prices
(`registry.py`, `status`).

| Status | Rule |
|---|---|
| `new` | Set at publication; kept until the theme has six monthly rows to compare. |
| `strengthening` | Ratio of 1.25 or more (`strengthening_ratio`). |
| `stable` | Ratio between the two thresholds. |
| `cooling` | Ratio of 0.8 or less (`cooling_ratio`). |
| `inactive` | Absent at 3 month-ends in a row (`inactive_after_snapshots`), counting only months in which it could have been seen. |
| `merged` | The theme was merged into another. |

- **Ratio.** The mean of the theme's share at the last 3 month-ends divided by the mean at the 3
  before (`compare_snapshots`). Each status change records the ratio and the cohort behind it.
- **Cohort.** Share is measured among companies covered, and with text, at all 6 month-ends: the
  theme's passages from them divided by all their passages. Adding or removing companies cannot move
  it. Rows without stored per-company windows fall back to the share of all text.
- **No comparison.** The status is left as it was with fewer than 6 rows, a month of missing
  evidence among the 6, a zero base, or none of the theme's companies in the cohort.

- **Could it have been seen?** A month is an absence (`observed: true`, `share: 0`) only if at least
  half of the companies that last discussed the theme had text in that window. Otherwise it is
  missing evidence (`observed: false`, `share: null`) and does not count towards inactivity.

**Emergence measures**, per month-end row:
- `share`: the theme's share of all passages in the window.
- `share_comparable`, `growth_3m`: the share among comparable companies, and that share divided by
  the figure 3 month-ends earlier. Growth is null when the theme was absent then.
- `new_issuers_3m`: comparable companies in the theme now that were not in it 3 month-ends earlier.
  It is `None` when the theme was absent at that base month.
- `flagged` (the research rule fired) and `flagged_comparable` (the emergence route test).

The overview lists a theme as emerging when `flagged_comparable` is true in any of its last 3 rows,
or it entered by the emergence route and is `new` or `strengthening`. Headwinds are listed apart.

## Titles and summaries

Readers see a title and a summary instead of keywords. Display text only (`naming.py`, `labels`).

A published theme takes its label from the first source that applies:
1. **Curated:** `config/theme_labels.yaml`, by theme ID. Optional; never replaced by the others.
2. **Claude:** written from up to 10 of the theme's quoted passages, on the machine that runs the
   refresh (`ANTHROPIC_API_KEY`; `labels.model` is `claude-opus-5-5`).
3. **Automatic:** the top three keywords and a factual sentence (companies, sectors, kind of text).

- **Prompt and check.** The prompt asks for a title of four to nine words and a summary of two to
  four sentences about discussion, not stocks. An answer is used only if it says the subject is
  clear, cites at least two of the passages shown from at least two companies, cites nothing else,
  and its title has at most 12 words. Otherwise the automatic label is used and the reason is
  stored.
- **When written.** Once per theme and keyword name, and kept in `label_history`. Again when the
  keyword name changes, the prompt version changes (`PROMPT_VERSION` 2), or only an automatic label
  exists and a writer is available. A verdict that the passages are unclear is not asked again.
- **Limit.** `labels.max_per_refresh` is `null`: no limit. Active themes are written first and are
  always titled; a number would only pace inactive and merged themes.
- **Writer.** `labels.writer` is `claude`: a missing key or package is a warning in `status.json`
  and titles fall back to keywords. A failure never blocks a release.

## Headwind and tailwind language

Growth in discussion does not say whether the news is good or bad. Tone records which kind of
business language a theme's company passages use (`tone.py`, `tone`).

- **Passages.** Two fixed phrase lists written for this monitor: problem language (`HEADWIND`:
  weaker demand, inventory correction, pricing pressure, lowered guidance, layoffs and similar) and
  growth language (`TAILWIND`: strong demand, record revenue, raised guidance, share gains, ramp and
  similar). A passage is negative with more problem phrases than growth phrases, positive for the
  reverse. Analyst questions are left out.
- **Comparison.** A theme's negative and positive shares are compared with what its mix of genres
  would give at that window's own rates, so filings are compared with filings.
- **Direction**, per month-end (`measures[].tone.direction`):

| Direction | Rule |
|---|---|
| `headwind` | Negative share at least 0.2 (`min_share`), at least 2.0 times the genre-matched rate (`min_ratio`), and above the positive share. |
| `tailwind` | The same test with positive passages. |
| `mixed` | Neither. |
| `unknown` | Fewer than 20 company passages (`min_passages`). |

- The five problem phrases found in the most passages are stored (`headwind_terms`) for checking.
  Tone feeds the Headwinds list and the forward tests, and nothing else.

## Families and topics

One broad subject can appear as several themes. Families and topics group them for display only: no
ID, basket or history changes (`families.py`).

- **Similarity:** cosine of the latest meaning vectors of published themes that are not inactive or
  merged. **Tree:** average linkage, cut twice:

| Level | Cut | Meaning |
|---|---|---|
| Family | cosine 0.85 (`families.min_cosine`) | Near-duplicates; the same bar as identity matching. |
| Topic | cosine 0.6 (`groups.min_cosine`) | A broader subject with its themes as subthemes. A theme that fits no topic is in none. |

- **Lead theme:** most months present in the last 12, then most companies, then oldest.
- **Topic names:** with a writer, two to six words (up to eight accepted) and one sentence, written
  only from the member themes' titles and terms. Stored in the registry (`group_names`) and written
  again only when members or titles change; at most 20 per refresh (`groups.max_names_per_refresh`).
  Otherwise a topic takes its lead theme's title.
- Both are recomputed at every refresh from text alone. `themegraph monitor review` and `feed` list
  the pairs at each similarity; a change to a cut is argued from those.

## Baskets and returns

Each published theme has an equal-weight basket of its companies, valued daily (`baskets.py`,
`basket`).

- **Constituents.** Every covered company with exposure of 0.05 or more, core and adjacent roles
  alike. The basket shown is the basket valued; the export fails otherwise.
- **Entry.** The close of the first US session on a later UTC date than the version's `entry_after`:
  the publication time for a live decision, the month-end for a replayed one. A company whose market
  was closed is bought at its next close. With no close within 5 sessions (`stale_sessions`), or no
  prices at all, it is "not bought": its capital stays in cash.
- **Weights.** Equal starting capital, then buy and hold. No rebalancing and no costs.
- **Daily value.** Each holding at its last close. Sessions its own market traded without a close
  count against it: fresh (0), lagging (1 to 5), stale (more than 5). Each index day reports the
  share of value with a fresh price (`coverage`).
- **Acquisitions** (`delisted`, `successor` in the universe file). With recorded terms a holding
  becomes the acquirer's shares plus cash; with cash-only terms, that cash; with no terms, cash at
  its last close.
- **Membership changes.** A new set must be seen at 2 consecutive month-ends
  (`version_confirm_snapshots`). A different set, a return to the current basket or a month without
  the theme resets the count. A confirmed change starts a new version at its own entry close, at the
  previous index level (chain-linked), so a company counts only from its own entry.
- **No backdating.** For a theme already public, a change found while catching up on old month-ends
  takes effect when it is decided. The registry refuses a version with no covered company, or one
  bought before the version it replaces.
- **Prices.** USD and dividend-adjusted, so returns are total returns. A US-dollar symbol's stored
  history is reused only when the last 45 days downloaded agree with it; otherwise the full history
  is downloaded again (`OVERLAP_DAYS` in `ingest.py`).
- **Simulated and live.** An index day is `live` when it is after both the theme's publication time
  and the decision time of the basket version in force. Other days are `reconstructed`: what the
  rule would have held. The site says "Live" and "Simulated".

**Returns** (`performance.returns`). Each has `start`, `end`, `basis` (`live`, `reconstructed` or
`mixed`) and, per benchmark, its return and `excess_vs_<B>`.

| Key | Covers |
|---|---|
| `reconstructed` | The simulated stretch only. |
| `live` | The observed stretch only. |
| `full_period` | Since the first entry. |
| `1m`, `3m`, `6m`, `12m` | The last 21, 63, 126 and 252 sessions; null without enough history. |

- `participation_3m`: constituents up over 63 sessions, and those beating the first benchmark.
- `market_trend`: "gaining" or "losing" when the 3-month excess over the first benchmark changed by
  5 points or more between the last two month-ends (`market.trend_threshold_pp`).
- `hindsight`: the first basket's companies over the 126 sessions before entry. Kept apart from
  every return figure, because nobody could have held it.

## Benchmarks

Benchmarks are what each basket is compared with, on the same price basis (`market`, `registry.py`).

| Profile | Every theme | Sector reference |
|---|---|---|
| `tech-v2` | SPY, XLK | One, where the rule below selects it. |
| `semis` | SPY, SOXX | None. |

- **Sector reference** (`market.sector_benchmarks`, `sector_benchmark_min_share`). When at least
  half of a theme's first basket is in one sector, the theme also gets that sector's fund: SOXX
  (semiconductors, semiconductor equipment), IGV (application software, data and cloud software),
  CIBR (cybersecurity), XLI (power and electrical, cooling and building systems, electrical and
  infrastructure contractors), XLB (industrial gases, electronic materials and chemicals, metals and
  minerals). Other sectors, including data-centre operators, have none.
- It is fixed once, from composition only, and never changed. The first benchmark in the universe
  file (SPY) is the trading calendar. A refresh fails at loading if a configured benchmark has no
  prices.

## Co-movement

"Trading together" asks whether a basket's stocks move as a group beyond the market. Prices only; it
never affects which themes are published (`comovement.py`, `comovement`).

- **Windows.** The last 126 sessions and the 126 before (`window_sessions`).
- **Pool.** Covered companies trading at the window's start with their own close on at least 90% of
  the sessions (`min_observations_share`).
- **Estimator.** Simple daily returns on the benchmark calendar, last close carried over days
  without one. Covariance shrunk towards a single-index model on SPY (`index`; Ledoit and Wolf
  2003). The measure is each pair's correlation beyond SPY.
- **Comparison.** The basket's average pairwise value against 1,000 random baskets (`null_draws`) of
  the same size, from the same segments where possible, seeded per theme. Stored as counts (`below`,
  `ties`, `above`), `percentile` = (below + ties / 2) / draws, and `segment_matched`.
- **Wording.** "together" at a percentile of 0.9 or more, "less" at 0.1 or less, else "usual".
- **No verdict** (`informative: false`, with a note) when shrinkage is complete, SPY did not move,
  every random basket ties, or fewer than two of the basket's companies are in the pool.

## Market clusters

Market clusters show which covered stocks are trading together now. Prices only; nothing in the
registry changes (`market_clusters.py`).

- **Window and estimator:** as in "Co-movement", latest window only.
- **Tree:** average linkage on 1 minus the correlation beyond SPY.
- **Cut:** no number of clusters is chosen. Each company's residual returns are shuffled in time 200
  times with a fixed seed and the highest pairwise correlation of each shuffle is recorded. The cut
  is the 95th percentile of those maxima. Companies that join nothing above it are in no cluster.
- **Name:** the sector holding at least 60% of the cluster's companies, otherwise its two largest
  sectors. It says who is in the cluster, not why they move together. Each cluster lists the theme
  baskets that hold two or more of its companies.

## Relationships network

The network draws the covered companies as points joined by two kinds of link. It describes the
latest window and feeds no measure (`network.py`).

- **Trading together:** the pair's correlation beyond SPY is at or above the market-cluster cut. At
  most 600 links, strongest first.
- **Discussing the same theme:** both companies are in the latest month of the same theme that is
  not inactive or merged. At most 900 links, pairs sharing the most themes first.
- **Positions:** a force-directed layout with a fixed seed, computed with the release. Closeness
  means only that companies are linked, directly or through others.

## Macro sensitivity

Macro tables ask whether a group of stocks tended to move with rates, oil, inflation or the dollar,
beyond the market. They describe the past and feed nothing else (`macro.py`).

- **Factors:** monthly change in the 2-year and 10-year Treasury yields, monthly log change in WTI
  crude and the dollar index, and CPI growth. CPI for month M counts in month M + 2
  (`availability.macro_release_lag_months` in `config/pipeline.yaml`). Up to 3 months with no CPI
  figure count as "nothing published": zero change, then the whole change when a figure arrives.
- **Spans:** 30, 60, 90, 180 and 360 days, taken as 1, 2, 3, 6 and 12 months (`SPANS`). The return
  over a span is set against each factor's change over the same span, from every starting month.
- **Estimate:** least squares of the return on SPY's return and the factor changes, all
  standardised. +0.40 means the return was 0.4 of its usual move higher when the factor rose by one
  of its usual moves, with the market and the other factors held fixed.
- **"Clear" (slid-history significance):** the factor history is slid against the returns by every
  offset of at least a year. A link is clear when its coefficient is larger than in 95% of those
  offsets.

| | Theme baskets and market clusters (`macro_table`) | Companies (`company_macro`) |
|---|---|---|
| Return | Equal-weight monthly return of the group's current companies | The company's monthly return |
| History | Up to 144 complete months (`HISTORY_MONTHS`) | 72 months for 30, 60 and 90 days; 96 for 180; 144 for 360 (`SPAN_WINDOW`). A company needs the whole window. |
| Minimum | 10 non-overlapping stretches of the group's history per span (`MIN_BLOCKS`); otherwise no entry | A span without the whole window stored is disabled. |
| Short factor | Left out and named when its latest unbroken run is shorter than the longest factor's, or 72 months if that is less | Left out and named when it lacks the whole window |

For companies only:
- **Macro-sensitive:** the factors together account for more of the company's return than in 95% of
  slid histories. Only these companies are clustered.
- **Clusters:** average linkage on how alike the companies' factor-driven return paths are, cut at
  an average correlation of 0.7 (`MIN_CORR`). Named after the strongest average sensitivity, and the
  second when it is at least 75% as large.
- **Chance comparison:** the number of companies that would have passed in each slid history. A span
  is marked as not beyond chance when the real count is not above the 95% level.

If the provider returns under 80% of the CPI months asked for, the whole CPI series is taken from
FRED (`CPIAUCSL`). A failure in these tables never blocks a release.

## Business relationships

This layer records which covered companies say they do business with which others. It describes
statements and feeds no measure (`relations.py`, `relations`).

1. **Candidates.** Passages from a company's own statements (analyst questions left out) in the last
   24 months that name another covered company. Names are `Issuer.name`, its short form and
   `Issuer.aliases`. Matching is case-sensitive on whole words; tickers and a list of short forms
   that are ordinary words are never matched.
2. **Reading.** For each ordered pair, Claude reads the 3 most recent passages, each from a
   different document and cut to 110 words either side of the name. It answers from the speaker's
   side: supplies, buys from, partner, competitor, owns a stake in, owned by, or none.
3. **Check.** An answer must cite one of the passages shown; otherwise it is recorded as none.
4. **Links.** One link per pair and kind. "A buys from B" and "B supplies A" are the same link. A
   link records who said it, when, and whether both companies said it.

- Results are kept in `<state>/relations.json`. A pair is read again only when a newer passage about
  it appears. Each refresh reads at most 50 new pairs (`relations.max_per_refresh`).
- Covered companies only; lenders and banks are out of scope. A failure never blocks a release.

## Forward tests

Three claims about returns were registered on 2026-11-12, before any outcome existed. The monitor
logs events live and each test is read once (`forward_tests.py`; logging is in `registry.py`). The
registration text is in `docs/DESIGN.md`, section 11. The hypotheses, as coded:

| Test | Hypothesis | Excess expected |
|---|---|---|
| `headwind` | "After a theme turns headwind, its basket lags SPY over the next 6 months." | below zero |
| `emergence` | "A theme published through the emergence rule has a basket that beats SPY over the next 6 months." | above zero |
| `tailwind` | "After a theme turns tailwind, its basket beats SPY over the next 6 months." | above zero |

- **`headwind` and `tailwind` events.** A published theme's direction is headwind or tailwind at a
  month-end processed within 35 days of the refresh (`live_window_days`), and the last month in
  which the theme was present did not have the same direction: one event per spell. Headwinds go to
  `headwind_log`, tailwinds to `forward_log`.
- **`emergence` events.** A theme is published through the emergence route with basis `live`; logged
  to `forward_log`. Replayed history logs nothing in any test.
- **What an event stores.** The basket held and its version, the decision time, the first benchmark,
  the theme's sector reference, and the measures known then.
- **Counted events.** Within a test, events are taken in order of decision time. An event is left
  out when an earlier counted event began fewer than 126 sessions before it and holds at least half
  of this event's companies.
- **Outcome.** The event's basket, equal starting capital, bought at the first session on a later
  UTC date than the decision and held 126 sessions (`PRIMARY`): its total USD return minus the
  recorded benchmark's (the monitor's first, SPY) over the same sessions. A company without a price
  stays in cash.
- **Ready.** 20 counted events (`MIN_EVENTS`) with a completed 126-session window and an outcome
  that can be computed. Completed windows without one are listed with the reason and do not count.
- **Read.** `themegraph monitor forward` shows counts only. `--read TEST` is refused until the test
  is ready, stores the result in `<state>/forward_reads.json`, and returns that stored result
  unchanged afterwards.
- **Statistic.** A one-sided sign test on the non-tied outcomes. A read stores the valid outcomes,
  exact ties, non-tied count, wins, median excess and the nominal p-value. Stored beside it as
  secondary: 63 sessions against the same benchmark, and 126 sessions against the sector reference.
- **Supported or not.** Decided only by Holm's step-down correction across the three primary
  p-values at 5% (`ALPHA`), derived once all three tests have been read. Until then every p-value is
  nominal and unadjusted and no claim is called supported.
- **Independence.** The sign test treats counted events as independent. The overlap rule removes
  only events sharing half their companies within the window. Baskets that share fewer companies, a
  sector or the same market months remain related, and the correction for three tests does not
  address that.
- **Rebuilds.** Each test is read from one registry's log; a rebuilt registry starts empty.

## Coverage and universe changes

The monitor covers a fixed list of companies, and "strongest" always means strongest among them. The
list is configuration (`config/universe.yaml`, `config/tech_v2/universe.yaml`, `coverage.py`).

- **Universe file.** `issuers` (with segment, role, currency, `covered_from`, `covered_until`,
  `delisted`, `successor`), `sectors` (segment to sector label), `benchmarks`, `version`, `changes`.
- **Covered.** On a day when `covered_from` is empty or on or before it, and `covered_until` is
  empty or after it.
- **Comparable.** At month-end t, against the base 3 month-ends earlier: covered at the base,
  covered at t, and first document at least 12 months before t (`coverage.min_history_months`).
  Growth, new-company counts and the emergence and established routes use comparable companies only,
  so back-filled documents cannot look like growth. Others are shown as recently added.
- **Adding a company.** Add the issuer with `covered_from: <date>`, append a change `{version, date,
  added: [...]}` and set `version` to match. Loading refuses a change log that disagrees with the
  issuers' dates, a version that is not the latest change's, or a segment without a sector.
- **Removing a company.** Keep its entry, set `covered_until` and record `removed`. It stays in
  baskets already bought and enters no new version.
- **Live expansions.** A newly-covered theme whose new companies have `covered_from` on or after the
  registry's go-live (its first refresh, stored as `go_live`) is bought at the first close after the
  refresh that published it, and its days are live. Earlier measures and basket versions are never
  recomputed; each records the universe version it was computed under.
- **Rebuild marker.** `full_history_through: <date>` in the universe file makes a registry built on
  or after that date treat every company with `covered_from` on or before it as covered from the
  start. A registry fixes this when first built. For an existing registry nothing changes until
  `themegraph monitor rebuild --confirm` replays the history; until then each refresh warns
  (`warnings.history`).
- **IR documents.** `config/ir_documents.yaml` lists company IR documents to read (empty as
  shipped). An earnings release within three days of the company's stored SEC filing is skipped.

## Profiles

A profile is one named set of configuration files, registry and site (`config/profiles/`,
`profiles.py`).

| | `tech-v2` | `semis` |
|---|---|---|
| Use | The monitor: every command without `--profile` uses it (`ACTIVE`) | The semiconductor pilot, kept for the research protocol |
| Universe | `config/tech_v2/universe.yaml`, version "4" | `config/universe.yaml`, version "1" |
| Companies | 154 | 76 |
| Monitor settings | `config/tech_v2/monitor.yaml` | `config/monitor.yaml` |
| Registry, site | `monitor_state-tech-v2/`, `site-tech-v2/` | `monitor_state/`, `site/` |
| Theme IDs | `tech-NNNNN` | `th-NNNNN` |

The 154 companies of `tech-v2` are the 76 of the semiconductor pilot, 51 in software, cybersecurity
and data/cloud, 16 in electrical and data-centre infrastructure and 11 in materials. The last 27
have `covered_from: 2026-11-20`. `full_history_through` is 2026-11-20; a registry built before that
date treats those 27 as added live until a rebuild.

- Universe and monitor files can `extends:` a base file. The document and price store is shared;
  every read is limited to the profile's universe.
- Library calls and tests that name no profile use `semis` (`DEFAULT`).
- Every release and status file records `effective`: the profile, universe name and version, and
  each configuration file with its SHA-256.

## Data feed

A refresh publishes static JSON that the site reads; any web host can serve it (`export.py`, schema
version 1).

| File under `<site>/data/` | Holds |
|---|---|
| `latest.json` | Pointer to the current release. |
| `status.json` | Last attempt per step, errors, warnings, data dates. Written on every attempt. |
| `releases/<id>/index.json` | One summary row per theme, lists, companies, network, clusters, macro, forward tests, coverage. |
| `releases/<id>/themes/<theme>.json` | The full record of one theme. |

- **Publishing.** A release is written to a staging directory, checked, renamed, and only then made
  current by replacing `latest.json`. The last 5 releases are kept.
- **Failures.** Downloads (when run), loading, themes and valuation are required steps. When one
  fails, later steps are skipped, nothing is published and the previous release stays current.
- **Optional parts.** Labels, topic names, business relationships and macro tables report a warning
  or "not computed" and never block a release.
- **Strict JSON.** No NaN or Infinity. An unavailable measurement is `null`, with a reason where one
  exists. A non-finite number fails the export.
- **Lists in `index.json`:** `emerging`, `headwinds`, `established`, `market_gaining`,
  `market_losing`, `cooling`, `newly_covered`, `inactive`.
- **Freshness.** `price_gaps` lists covered companies and benchmarks more than 3 of their own
  market's sessions behind, or with no prices (`freshness.price_stale_sessions`). Documents are
  flagged stale after 21 days (`documents_stale_days`).
- **Units.** Dates are ISO 8601. Returns are fractions (0.05 is 5%).

## Known limits

- **Survivorship.** History is replayed with today's company list, so simulated history leaves out
  companies that no longer exist and includes companies nobody would have chosen then.
- **Simulated versus live.** Only `live` index days were observed after a theme was public.
  Simulated results are labelled and are not a track record.
- **Baskets versus benchmarks.** Baskets are equal-weight and benchmarks are
  capitalisation-weighted, so part of any difference is a size effect. No costs are included.
- **Status is sensitive to one month.** One large month enters the 3-month average and later leaves
  it, so it can change the status more than once.
- **Tone is a phrase list.** It does not understand negation or context ("no sign of weakness"
  counts as problem language) and cannot tell who benefits.
- **Non-US close timing.** Seoul, Tokyo and Taipei close before the US. Co-movement and market
  clusters do not adjust for this.
- **Model-written text.** Titles, summaries, topic names and business-relationship readings are
  written by a model from quoted passages. The checks limit errors but do not remove them.
- **Macro tables.** With five factors and five spans some clear links appear by chance, groups share
  companies, and today's members are used throughout.
- **Nothing is validated as predictive.** No measure here has been shown to predict returns. The
  forward tests are the only planned evidence, and a test cannot be read before 20 of its events
  have a completed 126-session window.
- **Nothing was verified on real data from the build environment.** It has no price store, documents
  or monitor state, so the code is tested on synthetic fixtures only. For the 27 companies added on
  2026-11-20, prices, transcripts and the XLI and XLB benchmarks are unverified.
