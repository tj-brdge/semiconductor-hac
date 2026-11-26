# Fundamentals feasibility test (experiment)

Status on 2026-11-26: **the code is built and tested offline; the test on real SEC
data was not run here**, because the SEC refused the download from the build
environment. Nothing here is used by the monitor or feeds a published figure.
Labels: **measured** (seen on real data), **tested offline** (true of the code on
synthetic tables), **proposed** (not built). Nothing in this document is measured.

## What the test is for

The proof step asked for by the desk study in `docs/PLATFORM_PLAN.md`, for revenue,
gross profit (revenue minus cost of revenue), operating income, inventory and capital
expenditure. On the SEC's quarterly Financial Statement Data Sets it has to show that
the first-reported value and its publication time can be recovered; that amendments
and restated comparatives appear as separate dated rows; which tag each company uses;
and how many of the 154 companies resolve, by metric and filer type.

## Data: not run here

One request each, with the User-Agent `themegraph-research-tool`, to
`https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets` and
`https://www.sec.gov/files/dera/data/financial-statement-data-sets/2025q2.zip`.
Both answered **HTTP 403** with the SEC's "Request Rate Threshold Exceeded" page
(reference `0.be7c4217.1795693056.1d91f7a`); the cause was not established. Not
retried, not worked around. So no file was read, the zip address is **unverified**,
and the field layout follows the SEC's specification (`aqfs.pdf`) as recalled.

## What was built

`src/themegraph/experimental/fundamentals.py` (pure pandas functions over the SUB and
NUM tables), `scripts/fundamentals_feasibility.py` (the command the owner runs) and
`tests/test_fundamentals_experimental.py` (16 offline tests on synthetic tables).

Rules in the code (all **tested offline**):

- **Entity-wide values only.** Rows with a segment or a co-registrant are dropped, and
  so are company-specific tags. If one filing gives two different values for the same
  tag, period and unit, both are dropped and counted: missing, not guessed.
- **Key.** Company, metric, period end date (rounded to the month end, as the SEC
  does), duration in quarters, unit. `fy` and `fp` are never read, so 52/53-week and
  non-December years need no special case.
- **First-reported value.** The filing with the earliest acceptance time gives the
  value and its availability time. Eastern time is converted to UTC with the same rule
  as `data/edgar.py` (a test compares the two functions).
- **Revisions.** A later filing with a different value is a dated row of its own and
  never replaces the first value. Three kinds: amendment (a `/A` form), later
  comparative (a filing for a later period), same-period refiling (for example an
  8-K recast). Size is given against the value last reported before it.
- **Tags.** An ordered list per metric; the first one present in a filing is used.
  Gross profit is the `GrossProfit` tag, else revenue minus cost from the same filing.
- **Single quarters.** A reported quarter is used as is. Otherwise a cumulative figure
  minus the one a quarter shorter that ends 70 to 115 days earlier (year-to-date
  cash-flow items; Q4 = full year minus nine months). Availability is the later of the
  two filings, and both numbers are taken as known at that time.
- **Units** (USD and TWD, for example) are never mixed. A metric that does not resolve
  is reported as missing, never as zero.

## Results

**Measured: none.** Coverage of the 154 by metric, a restatement and an amendment
with accession numbers, timestamp reliability and tag inconsistencies are what the
script's report contains; they cannot be stated here.

**Tested offline:** `.venv/bin/pytest tests/test_fundamentals_experimental.py`: 16
passed; `ruff check` clean for the new files; the script run end to end on a
five-filing synthetic zip. This shows the logic follows the rules above, not that
SEC data behaves the way the synthetic tables assume.

## What remains unverified

1. The zip address pattern, and whether older quarters use the same one.
2. Column names and formats in real files, above all whether `num.txt` has a
   `segments` column in every quarter (without it segment rows cannot be excluded;
   the report says so).
3. Whether `accepted` is the original acceptance time, in Eastern time, as assumed.
4. Every tag name. The list comes from the task and the published taxonomies; the
   IFRS names (`Revenue`, `CostOfSales`, `ProfitLossFromOperatingActivities`,
   `Inventories`, `PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`)
   are the least certain. The report lists each candidate with the number of companies
   using it, and related tags that are not candidates.
5. Whether `Revenues` is the total when a contract-revenue tag is also present. A
   switch of tag between filings can look like a revision (marked `tag_changed`).
6. Whether small changes are restatements or rounding (thousands, then millions).
   The report separates revisions under and over 0.5%.
7. Coverage: the desk study's 138 quarterly and 11 annual-only filers. 68 of the 154
   have no CIK in the universe file and are looked up in the SEC ticker file.
8. Whether annual-only filers' 6-K interim figures are in the data sets. Without
   them no single quarters can be derived for those companies.
9. The first value in a loaded window can be a later filing's comparative when the
   original is in a quarter that was not loaded. These are flagged; only a full
   history removes them. Derived quarters carry no revision history of their own.

## How the owner runs it

```
export EDGAR_USER_AGENT='<your organisation> <your contact address>'
.venv/bin/python scripts/fundamentals_feasibility.py --download 2024q2 2024q3 2025q1 2025q2
```

or, with zips downloaded by hand, `--zips <folder>` (plus `--ticker-map <file>`, a
saved copy of the SEC's `company_tickers.json`, unless `EDGAR_USER_AGENT` is set). It
refuses to download without `EDGAR_USER_AGENT`, makes one paced request per file and
stops at the first refusal. Output: `runs/fundamentals_feasibility/` (not in git),
`report.md` and six CSV files.

Those four quarters show a quarter first reported (`2024q2`) and again as a
comparative a year later (`2025q2`), and a 10-K (`2025q1`) after its nine-month 10-Q.
Then check five companies by hand against their filings, including one amendment and
one annual-only filer; the report lists the largest revisions of each kind with both
accession numbers.

## Proposal: withheld until the test has run

A proposal needs evidence, and there is none measured, so **no fundamentals layer is
proposed for building**. What follows is the design the test was written to check,
for judging the real results: **proposed, not built, not supported by measurement**.

- **Metrics.** The five above. Gross margin = gross profit / revenue; operating
  margin likewise; capital expenditure and inventory also as a share of revenue.
- **Stored per company, metric, period and unit.** The first-reported value, its
  availability time (acceptance time plus the project's filing lag), the tag and
  accession number, and every later revision as its own dated row. Reads go through
  `as_of`, like every other table.
- **Per company.** Year-on-year growth of single-quarter revenue and margin change in
  points against the same quarter a year earlier, only from values available at the
  snapshot date and in one unit. No value, no figure.
- **Per theme.** Median across member companies, the interquartile range, breadth
  (share of companies with growth above zero, or margin rising), and coverage shown
  beside every figure (how many members have a value, out of how many). Each company
  counts once, so one large company cannot dominate; no revenue-weighted aggregate. A
  theme figure is not shown below a minimum number of companies.
- **Excluded.** Estimates and guidance; anything from a press release; companies
  that do not file, and annual-only filers for quarterly figures, until a source for
  them is validated in the same way; currency conversion.

**Acceptance criteria for building it** (to be judged on the measured report):

1. Revenue resolves for at least 95% of the quarterly filers and each other metric
   for at least 85%, or the gaps are explained company by company (for example, no
   inventory at a software company).
2. Five hand checks against filings agree exactly, including one amendment and one
   annual-only filer.
3. Acceptance times are present for all filings and consistent with filing dates.
4. Every revision over the threshold in the sample is explained as a real
   restatement, a tag switch or rounding, and the code labels each kind correctly.
5. A segment column is present in every quarter used, or another way to exclude
   segment rows is shown to work.
6. The owner accepts figures that are days to weeks later than the earnings release.
