# Technology pilot v2

A controlled expansion of the theme monitor from the semiconductor pilot toward the
wider technology ecosystem. It runs as its own profile (`tech-v2`) beside the
published semiconductor monitor (`semis`, the default), which is unchanged.

```
themegraph monitor refresh --profile semis                  # the published monitor, as before
themegraph monitor refresh --profile tech-v2 --documents    # the pilot: first run forks the semis registry
themegraph monitor compare --profile semis --against tech-v2   # comparison report (Markdown)
themegraph monitor serve --profile tech-v2                  # http://127.0.0.1:8780/, the pilot's site
```

Status on 2026-10-27: implemented and tested on synthetic fixture data only. It has
**not** been run on real data (see "Before the first real run").

## Universe

`config/tech_v2/universe.yaml` extends `config/universe.yaml`: all 76 semiconductor
pilot companies stay, with their roles and dates, and 51 companies are added
(universe version 2), covered from the start (see "How the pilot is built").

| Category | Companies | Sector benchmark |
|---|---|---|
| Application software (horizontal enterprise platforms) | 21 | IGV |
| Cybersecurity | 14 | CIBR |
| Data platforms and cloud software | 16 | IGV |

### Selection criteria

Applied in this order, from SEC EDGAR on 2026-10-27 (`company_tickers_exchange.json`
and each company's filing index). No price or return was looked at.

1. **Business.** Primary business in application software, cybersecurity, or data
   platforms and cloud software (SIC 7370–7374 for most; PANW and FTNT file under
   3577, AKAM under 7389, IBM under 3570, classified by business).
2. **Listing and identity.** Listed on NYSE or Nasdaq today; one security per
   company (one CIK; the other tickers on a CIK are OTC ordinaries, preferreds or
   rights, not second share classes); not already in the universe.
3. **Disclosure history.** Periodic SEC reports (10-K/10-Q, or 20-F for SAP, NICE,
   CHKP and MNDY) since at least 2023-10-27: three years give the 12-month minimum
   before a company counts in growth comparisons plus two years of simulated history.
   (Companies join the 10-year replay when their documents begin.) The filer's
   history must be the same business.
4. **Representation.** At least 12 companies per category.

### The 51 companies

| Company | Ticker | Category | Exchange | SIC | SEC reports | Since |
|---|---|---|---|---|---|---|
| Salesforce, Inc. | CRM | Application software | NYSE | 7372 | 10-K/10-Q | 2004-08-25 |
| ADOBE INC. | ADBE | Application software | Nasdaq | 7372 | 10-K/10-Q | 1995-02-17 |
| ServiceNow, Inc. | NOW | Application software | NYSE | 7372 | 10-K/10-Q | 2012-08-10 |
| INTUIT INC. | INTU | Application software | Nasdaq | 7372 | 10-K/10-Q | 1996-06-12 |
| Workday, Inc. | WDAY | Application software | Nasdaq | 7374 | 10-K/10-Q | 2012-12-07 |
| Autodesk, Inc. | ADSK | Application software | Nasdaq | 7372 | 10-K/10-Q | 1995-05-01 |
| SAP SE | SAP | Application software | NYSE | 7372 | 20-F | 1999-05-18 |
| Atlassian Corp | TEAM | Application software | Nasdaq | 7372 | 10-K/10-Q/20-F | 2016-08-18 |
| HUBSPOT INC | HUBS | Application software | NYSE | 7372 | 10-K/10-Q | 2014-11-13 |
| DOCUSIGN, INC. | DOCU | Application software | Nasdaq | 7372 | 10-K/10-Q | 2018-06-08 |
| Zoom Communications, Inc. | ZM | Application software | Nasdaq | 7370 | 10-K/10-Q | 2019-06-07 |
| Paycom Software, Inc. | PAYC | Application software | NYSE | 7372 | 10-K/10-Q | 2014-05-21 |
| Paylocity Holding Corp | PCTY | Application software | Nasdaq | 7372 | 10-K/10-Q | 2014-05-14 |
| PTC INC. | PTC | Application software | Nasdaq | 7372 | 10-K/10-Q | 1995-05-15 |
| monday.com Ltd. | MNDY | Application software | Nasdaq | 7372 | 20-F | 2022-03-16 |
| PEGASYSTEMS INC | PEGA | Application software | Nasdaq | 7374 | 10-K/10-Q | 1997-03-31 |
| BOX INC | BOX | Application software | NYSE | 7372 | 10-K/10-Q | 2015-03-30 |
| DROPBOX, INC. | DBX | Application software | Nasdaq | 7372 | 10-K/10-Q | 2018-05-11 |
| NICE Ltd. | NICE | Application software | Nasdaq | 7372 | 20-F | 2002-06-24 |
| WORKIVA INC | WK | Application software | NYSE | 7372 | 10-K/10-Q | 2015-03-11 |
| BLACKLINE, INC. | BL | Application software | Nasdaq | 7372 | 10-K/10-Q | 2016-12-12 |
| Palo Alto Networks Inc | PANW | Cybersecurity | Nasdaq | 3577 | 10-K/10-Q | 2012-10-04 |
| CrowdStrike Holdings, Inc. | CRWD | Cybersecurity | Nasdaq | 7372 | 10-K/10-Q | 2019-07-19 |
| Fortinet, Inc. | FTNT | Cybersecurity | Nasdaq | 3577 | 10-K/10-Q | 2010-03-05 |
| Zscaler, Inc. | ZS | Cybersecurity | Nasdaq | 7371 | 10-K/10-Q | 2018-06-07 |
| Cloudflare, Inc. | NET | Cybersecurity | NYSE | 7372 | 10-K/10-Q | 2019-11-12 |
| Okta, Inc. | OKTA | Cybersecurity | Nasdaq | 7372 | 10-K/10-Q | 2017-06-08 |
| SentinelOne, Inc. | S | Cybersecurity | NYSE | 7372 | 10-K/10-Q | 2021-09-10 |
| QUALYS, INC. | QLYS | Cybersecurity | Nasdaq | 7372 | 10-K/10-Q | 2012-11-09 |
| Tenable Holdings, Inc. | TENB | Cybersecurity | Nasdaq | 7372 | 10-K/10-Q | 2018-09-07 |
| Rapid7, Inc. | RPD | Cybersecurity | Nasdaq | 7372 | 10-K/10-Q | 2015-08-19 |
| VARONIS SYSTEMS INC | VRNS | Cybersecurity | Nasdaq | 7372 | 10-K/10-Q | 2014-05-08 |
| CHECK POINT SOFTWARE TECHNOLOGIES LTD | CHKP | Cybersecurity | Nasdaq | 7372 | 20-F | 2000-06-28 |
| Gen Digital Inc. | GEN | Cybersecurity | Nasdaq | 7372 | 10-K/10-Q | 1995-06-08 |
| AKAMAI TECHNOLOGIES INC | AKAM | Cybersecurity | Nasdaq | 7389 | 10-K/10-Q | 2000-05-15 |
| Snowflake Inc. | SNOW | Data and cloud software | NYSE | 7372 | 10-K/10-Q | 2020-12-03 |
| Datadog, Inc. | DDOG | Data and cloud software | Nasdaq | 7372 | 10-K/10-Q | 2019-11-13 |
| MongoDB, Inc. | MDB | Data and cloud software | Nasdaq | 7372 | 10-K/10-Q | 2017-12-15 |
| Palantir Technologies Inc. | PLTR | Data and cloud software | Nasdaq | 7372 | 10-K/10-Q | 2020-11-13 |
| Elastic N.V. | ESTC | Data and cloud software | NYSE | 7372 | 10-K/10-Q | 2018-12-12 |
| Dynatrace, Inc. | DT | Data and cloud software | NYSE | 7372 | 10-K/10-Q | 2019-09-05 |
| TWILIO INC | TWLO | Data and cloud software | NYSE | 7372 | 10-K/10-Q | 2016-08-09 |
| Gitlab Inc. | GTLB | Data and cloud software | Nasdaq | 7372 | 10-K/10-Q | 2021-12-07 |
| Nutanix, Inc. | NTNX | Data and cloud software | Nasdaq | 7372 | 10-K/10-Q | 2016-12-08 |
| UiPath, Inc. | PATH | Data and cloud software | NYSE | 7372 | 10-K/10-Q | 2021-06-09 |
| JFrog Ltd | FROG | Data and cloud software | Nasdaq | 7372 | 10-K/10-Q | 2020-11-05 |
| DigitalOcean Holdings, Inc. | DOCN | Data and cloud software | NYSE | 7370 | 10-K/10-Q | 2021-05-07 |
| Fastly, Inc. | FSLY | Data and cloud software | Nasdaq | 7372 | 10-K/10-Q | 2019-08-09 |
| INTERNATIONAL BUSINESS MACHINES CORP | IBM | Data and cloud software | NYSE | 3570 | 10-K/10-Q | 1994-03-28 |
| TERADATA CORP /DE/ | TDC | Data and cloud software | NYSE | 7372 | 10-K/10-Q | 2007-11-13 |
| C3.ai, Inc. | AI | Data and cloud software | NYSE | 7372 | 10-K/10-Q | 2021-03-02 |

### Not included, and why

| Reason | Companies |
|---|---|
| Already covered by the semiconductor pilot | MSFT, GOOGL, AMZN, META, ORCL (cloud); CSCO, ANET (networking); SNPS, CDNS (design software); AAPL, DELL, HPE |
| No longer listed (acquired) | Dayforce (DAY), CyberArk (CYBR), Confluent (CFLT), Informatica (INFA), Ansys (ANSS), Splunk (SPLK) |
| Under three years of SEC reports | Rubrik (RBRK, from 2024-06), SailPoint (SAIL, from 2025-03), CoreWeave (CRWV, from 2025-05): add in a later universe version once they have the history |
| Filing history is a different business | Nebius (NBIS): its filings before 2024 are Yandex N.V. |
| Outside the three categories | Roper (ROP, diversified), Fair Isaac (FICO, credit scores), AppLovin (APP, advertising), Shopify (SHOP, commerce) |
| Vertical-market software, deferred | Tyler (TYL), Veeva (VEEV), Guidewire (GWRE), AppFolio (APPF), Manhattan Associates (MANH), Bentley (BSY), Unity (U): their disclosures mostly concern end markets (government, life sciences, insurance, property, logistics, construction, games) the universe does not cover |

**Known bias.** The candidates were drawn from companies prominent enough to be well
known in each category, and prominence today partly reflects past success. The
filters above are mechanical, but the starting list is not. Excluding acquired
companies is right for a forward-looking monitor, but reconstructed (simulated)
history for these categories therefore leaves out companies that no longer exist.
For a later version, a rule-based screen (SIC codes plus a point-in-time size rank
at a fixed date, from the price provider) would remove the judgment from the start.

## How the pilot is built

Like the semiconductor pilot: the first refresh replays the last 120 month-ends (10 years) with
all 127 companies and builds every theme's history from the companies' earlier
documents and prices, labelled simulated, then runs live from that refresh. Every
company counts from the first month, in themes, growth comparisons (after its 12
months of documents) and baskets.

- The pilot has its own registry and theme IDs (`tech-NNNNN`); the semiconductor
  registry is not copied or changed. `themegraph monitor compare` matches themes
  between the two by their companies.
- Why 10 years: the topic layer already computes every month back to 2010 on each
  refresh, so a longer replay costs little; 10 years spans a full cycle (the 2018
  chip downturn, COVID, the 2022 rate shock, the AI build-out). Longer is possible
  (`bootstrap_snapshots` in config/monitor.yaml), but the further back, the more the
  history uses today's companies for a past that had different ones.
- The simulated history uses today's company list, so it leaves out companies that
  have since been acquired (see "Known bias"). It is never shown as live.
- Fork mode (`fork_from: semis` in the profile) is the alternative for a live
  expansion of a published monitor whose theme IDs and basket history must carry on:
  the added companies then get a `covered_from` date, enter through the newly-covered
  route, and count in growth only once covered at the comparison base. It is kept and
  tested, but not used for this pilot, since nothing has been published yet.

## Benchmarks

Every theme is compared with SPY (the market) and XLK (US technology sector). A theme
also gets one sector reference when at least half of its first basket's companies are
in one sector: SOXX for semiconductors and semiconductor equipment, IGV for
application software and data and cloud software, CIBR for cybersecurity. The choice
is fixed when the first basket is decided, from composition only, and recorded with
the theme (`sector_benchmark`); themes inherited from the semiconductor pilot get it
when the fork first processes them. Themes with no majority sector (most cross-sector
themes) are compared with SPY and XLK only.

Why not SOXX for everything: it is the semiconductor pilot's assumption, and it would
compare a software or security theme with chip stocks. Why not choose per theme by
correlation: that would pick benchmarks from returns.

All of these are capitalisation-weighted, while theme baskets are equal-weighted, so
part of any difference is a size effect. RSPT (equal-weight technology) would be a
closer match for XLK; it was not added because its price history under that ticker
could not be checked here (renamed from RYT in 2023).

The semiconductor profile keeps SPY and SOXX for every theme, as before.

## Handling new coverage

Three labels, three different routes into the inventory:

- **Emerging**: the emergence rule fired with growth among *comparable* companies
  (covered at the 3-month base and now, with 12 months of documents). New companies
  cannot make a theme emerge: they are not comparable until they were covered at the
  base date.
- **Established**: present in 6 of the last 9 month-ends, 4+ comparable companies,
  at least median coherence.
- **Newly covered** (new): a topic carried mostly (at least half) by companies not
  yet covered at the base date cannot use either route for months, although it may
  be long-running. It is published as "newly covered" when it meets the established
  bar on the backfilled history: present in 6 of the last 9 month-ends (followed back
  through earlier month-ends' clusters with the same passage/meaning matching used for
  identity), 4+ companies, at least median coherence. It is never shown as emerging.
  Its basket starts when it is published; the backfilled presence is recorded as
  evidence (`newly_covered`), not as an earlier discovery, and no measures are
  written for months before the expansion.

Other safeguards that apply to every profile:

- **Status** (strengthening/cooling) compares a theme's share of a fixed company
  cohort across the six month-ends compared: companies covered and with documents
  in every one of them. Adding or removing companies, or a company missing documents
  for a month, changes neither side of the comparison. A month in which most of the
  theme's companies have no documents is not counted as an absence.
- **Basket membership**: a change is adopted only when the same proposed set is seen
  at two consecutive month-ends; a different set, a return to the current basket, or
  a month without the theme resets the count. New companies that join an existing
  theme enter its basket this way, in a new version decided when it is confirmed;
  earlier versions and returns are unchanged.
- **The fork**: `tech-v2` starts from a copy of the semiconductor registry (theme
  IDs, basket versions, measures and events kept; `fork` records the source, the
  month-end and the source registry's hash) and then runs independently. Themes it
  creates are named `tech-NNNNN`, so IDs never collide.

## Validation

`themegraph monitor compare` writes: themes added, retained, lost, split or merged;
membership, status and basket changes for the semiconductor pilot's themes; naming
and evidence for a sample; generic and possibly duplicate themes; topics tracked but
not published, with the reason; per-company document and price coverage; and each
profile's runtime, peak memory, provider requests and cache reuse.

### Synthetic fixture results (not a real validation)

Semiconductor fixture to 2021-06-30, technology fixture forked there, both run to
2021-12-31 (`tests/test_profiles.py` runs the same sequence):

- All 7 published themes retained with the same IDs, statuses and baskets; no
  emergence flags gained; no splits or merges.
- The AI build-out theme gained the two data companies planted to join it, through
  a confirmed basket version decided at the refresh; its status stayed "stable".
- Under the previous status rule (share of all text), four of the seven inherited
  themes (th-00001 to th-00004) would have been marked "cooling" in the second and
  third month after the expansion (ratios 0.59 to 0.78 against the 0.80 threshold)
  with no change in their own discussion: the reported bug, reproduced on the fork.
  With the cohort rule all stayed "stable", as in the semiconductor fixture.
- 2 themes added through the newly-covered route: the planted "AI agents" topic
  (6 new companies across three sectors, present in 7 of 9 backfilled months) and
  the data/cloud segment topic.
- Not published: the security and application software segment topics (tech-00011,
  tech-00012), only because their coherence was below that month's median, the same
  rule that keeps the semiconductor fixture's equipment and memory topics out.
  12 of 24 new companies are in no published theme.
- Runtime: theme step 18.6 s for the semiconductor fixture (48 companies, 24 month-ends at the time
  replayed) and 27 s for the technology fixture (72 companies, 6 month-ends after the
  fork; the topic layer rebuilds every window, and the fixture caches are per state
  directory). Peak memory about 0.7–0.8 GB for the test process. These numbers say
  nothing about real data volumes.

### Before the first real run

Real-data execution was blocked here: the FMP API is not reachable from this
environment and no FMP or Voyage key is configured. On a machine with the existing
credentials (FMP_API_KEY, VOYAGE_API_KEY, the SEC user agent):

1. If you already ran the pilot in fork mode, move its state aside first
   (`mv monitor_state-tech-v2 monitor_state-tech-v2.fork; mv site-tech-v2 site-tech-v2.fork`).
2. `themegraph monitor refresh --profile tech-v2 --documents`: downloads prices,
   transcripts and filings for all 127 companies (cached responses are reused for the
   76 already downloaded; roughly 51 × 40–60 transcript calls and the matching filings
   for the new companies) and embeds the new passages (paid, one-time; cached by text).
3. Check the coverage table in the comparison report: every new company should have
   prices from at least 2023 and transcripts; the ETFs XLK, IGV and CIBR must have
   prices (not verifiable from SEC data).
4. `themegraph monitor compare --profile semis --against tech-v2`, then read the
   sample of missed topics, the newly covered themes' evidence, and the run metrics
   (the topic layer's time and memory with about 65% more text) before deciding
   anything about scale.
