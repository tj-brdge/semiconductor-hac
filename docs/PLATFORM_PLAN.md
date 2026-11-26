# Platform plan (2026-11-25)

The long-term vision is an institutionally credible research and monitoring
platform. It is **not** marketed as a proven return-predicting system: nothing
here has been shown to predict returns. This file is the working plan: stages,
acceptance criteria, what was found, and what waits for the owner's approval.
It is deliberately short; how things work today is in `METHOD.md` and `OPERATIONS.md`.

## Stages

| # | Stage | Done when | Depends on | State |
|---|---|---|---|---|
| 1 | Frontend redesign on existing analytics | Every page renders from the real feed schema with no console errors at desktop and phone widths, light and dark; no figure is computed that the feed does not hold; a Research and Validation page exists; old links still work | Nothing | Built on fixtures; **not yet seen on real data** |
| 2 | Backend audit and defect fixes | Each finding is classed (bug, misleading, limitation, enhancement); bugs that do not touch a rule are fixed with a regression test; rule changes are listed for approval, not made | Nothing | Three bugs fixed; proposals below |
| 3 | Real-data review | The owner runs a refresh on this branch and sends the exports listed below; pages and audit findings are checked against them | Stage 1, owner's machine | Waiting |
| 4 | Fundamentals feasibility | A go or no-go with evidence on original values, dates, restatements, coverage, cost and redistribution | `data.sec.gov` access | Desk study done: go, with a small proof step still to run |
| 5 | Economic-impact extraction feasibility | A frozen sample, adjudicated labels and a pre-declared comparison against the phrase lists | Owner's approval and labelling time | Proposal below; nothing run |
| 6 | Distribution readiness | Hosting, secrets and the public/private split reviewed | Stages 1 to 3 | Licensing settled by the owner (below); the rest not started |

## Licensing

The owner stated on 2026-11-25 that their FMP licence covers distribution. On that
basis prices, transcripts and figures derived from FMP data may be shown to outside
readers. The licence itself was not read here; its exact terms (attribution, any
limit on raw data downloads as opposed to display) stay the owner's to check. SEC
filings are public and may be redistributed with citation.

## Exports needed from the owner's machine

After `themegraph monitor refresh` on this branch (profile `tech-v2`):

1. `site-tech-v2/data/status.json`
2. `site-tech-v2/data/latest.json` and the `index.json` in the release folder it names
3. Four or five files from that release's `themes/` folder: one emerging, one long-running, one headwind, one inactive, one with live days
4. `monitor_state-tech-v2/coverage_report.json`
5. `monitor_state-tech-v2/themes_feed.md` (`themegraph monitor feed`)
6. `monitor_state-tech-v2/forward_tests.md` (`themegraph monitor forward`)
7. The refresh's printed log (stage lines with time and memory)

None of this blocks the frontend, which is built and tested on the fixtures.

## Backend audit (read-only pass, offline checks)

**Fixed here (implementation defects; no rule changed):**

- *"New in the last three months" listed every company* when the theme was absent
  three months earlier. Now there is no comparison (`None`). Display only.
- *One young group made the whole macro table unavailable.* A group with 15 to 23
  months of history passed the length gate but allowed no slid-history offset, and
  the error was swallowed as "not computed" for every theme. Such a group now has
  no entry.
- *The tailwind sentence quoted problem-language figures.* It now quotes the growth
  figures that decided the label.
- *Links such as `#/method#freshness` opened the overview.* The router now reads
  the anchor.

**Misleading outputs, reworded in the interface (no calculation changed):**

- Contribution to return covers the current basket version only; the column says so.
- The theme page's growth figure (one month against one month) differs from the
  status ratio (three-month averages); both are now named for what they are.
- Status markers are decided looking back, and one large month moves the status
  twice; the chart says so.

**Proposed, waiting for approval (each changes a result or a rule):**

| Proposal | Why | Effect |
|---|---|---|
| Treat a carried price as missing in macro and participation statistics | A stock with no quotes after delisting enters as a run of zero returns | Changes macro tables for baskets holding such stocks. Unverified whether the real store triggers it |
| Dampen status changes (for example, two month-ends in a row) | One spike month causes up to four status changes | A detection rule; must be dated and judged on text only |
| Cap how long a theme's status can stay frozen without evidence | Themes whose companies stop reporting never go inactive | A status rule |
| Make "gaining / losing ground" say which month left the window, or replace it | The one-month change in two overlapping three-month windows can read "gaining" with no new outperformance | Display, or a measure change |
| Make `--as-of` cut every input on real data, not only themes | Valuation, macro and clusters use the whole store | CLI semantics |
| Adjust co-movement for markets that close before the US | Seoul, Tokyo and Taipei closes precede the US close; those names can look more connected | A measure change; currently documented as a limitation |

**Research limitations now stated on the Research and Validation page:**
survivorship, coverage as a chosen list, equal-weight baskets against
capitalisation-weighted benchmarks with no costs, status sensitivity to one month,
the phrase-list tone measure, non-US closing times, model-written text, and
overlap between themes.

**Computed but not shown:** participation of constituents, the cohort size behind
each status change, growth-language shares, and the joint macro p-value. Candidates
for the interface once seen on real data.

## Survivorship

The simulated history is rebuilt with today's list of companies. This is stated on
the Research and Validation page, in every simulated tag, and here. It cannot be
removed with the data in the system. Doing it properly needs:

- a list of candidates at each date under a rule fixed in advance (historical
  index or industry membership, or a size-and-listing screen), including companies
  later acquired or failed;
- for each: listing and delisting dates, deal terms, ticker changes and identifiers;
- prices, transcripts and filings for companies no longer listed (provider
  coverage for these is unverified);
- code that applies each company's covered-from and covered-until dates in a
  rebuild, instead of clearing them, and records the basis in every snapshot.

Until then, simulated results are labelled and never presented as a track record.

## Fundamentals feasibility (desk study)

**Recommendation: go, on SEC as-filed data, after one small proof step.** No file
was downloaded: `data.sec.gov` is blocked in the build environment.

- **Source.** The SEC's quarterly Financial Statement Data Sets hold numbers "as
  filed" from 10-K, 10-Q, 20-F and their amendments, with the filing's acceptance
  time. Keeping, for each company, metric and period, the value with the earliest
  acceptance gives the first-reported figure; later rows are revisions with their
  own dates. The "frames" interface returns the latest filed value and must not be
  used.
- **Coverage of the 154.** 138 file quarterly reports. 11 file annual reports only
  (TSMC, UMC, ASML, STMicro, Arm, GlobalFoundries, Camtek, SAP, monday.com, NICE,
  Check Point). SK hynix has no annual report on EDGAR yet. Samsung, Nanya, Tokyo
  Electron and Advantest do not file. Several of these classifications come from
  the coverage file's notes and are not re-verified.
- **Metrics.** Revenue, gross margin, inventory, capital expenditure and operating
  income are feasible for US filers. Known work: several revenue tags over time,
  gross profit often derived, fourth quarters derived from the annual report,
  cash-flow items reported year to date, and 52/53-week years.
- **Dates.** Filing dates are later than the earnings release, which carries no
  structured data: safe, but days to weeks stale.
- **Provider alternative.** FMP statements carry filing and acceptance dates and
  has "as reported" endpoints, but whether its standard figures are first-reported
  or restated is unconfirmed. The owner's licence covers distribution (see
  Licensing above).
- **Redistribution.** SEC data is public and may be redistributed with citation.
- **Size and cost.** Medium engineering effort; no data cost for SEC.

**Proof step before any pipeline:** download one quarterly data set, resolve the
five metrics for the covered companies, and hand-check five against their filings,
including one amended filing and one annual-only filer.

## Economic-impact extraction: proposed evaluation

The current measure counts fixed phrases and cannot tell who benefits from a
development and who is hurt. The proposal is a small controlled comparison. It
runs nothing until approved, and it is judged on reading accuracy, never returns.

- **Schema (per passage).** Driver (short phrase); affected party (the speaker, a
  named company, or an unnamed group such as customers); effect on that party
  (benefits, harmed, neutral, unclear); channel (demand, supply or capacity,
  pricing, cost, margin, other); direction (improving, deteriorating, unchanged,
  unclear); statement type (observed, guidance, risk, hypothetical); the verbatim
  words relied on; and an explicit "not enough to say".
- **Sample.** 300 stored passages from the last twelve months, drawn with a fixed
  seed: 100 each from themes now labelled headwind, tailwind and mixed, spread
  across sectors and sources, at most three per company and theme. The list of
  passage IDs is committed before anything is read.
- **Adjudication.** Two readers label each passage independently, without seeing
  either method's answer: effect on the speaker, and whether another party is
  affected the opposite way. Disagreements are settled together; agreement is
  reported.
- **Comparison, declared before results.** Phrase lists against extraction on the
  adjudicated labels: accuracy and per-class precision and recall for the effect
  on the speaker; how often an opposite-effect party is correctly found; share
  answered "not enough to say". Every quoted span must appear verbatim in the
  passage. Extraction replaces nothing unless it is clearly better on these.
- **Cost.** About 300 model calls of roughly 1,000 input and 200 output words each
  for the pilot: small. A monthly run over active themes only would be a few
  thousand calls. Prices to be confirmed before any run.
- **Caching and reproducibility.** One stored answer per passage text, prompt
  version, schema version and model, kept beside the registry like the business
  relationships. The sample, the labels and the scoring script are committed.
- **Safeguard.** Passage text is treated as data to read, never as instructions.
