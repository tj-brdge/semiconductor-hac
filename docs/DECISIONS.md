# Decision log

This is the record of what changed in the monitor and when, newest first.
The full original text of every note is kept verbatim in `docs/archive/MONITOR_2026-11-26.md`.
A rule change must be added here, dated, before it takes effect.

Each entry ends with a tag: rule change, display only, data input or bug fix. How things work now
is in `docs/METHOD.md`.

### 2026-11-26 — Documentation rebuilt
`MONITOR.md` (1,246 lines of descriptions mixed with dated notes) was replaced by `METHOD.md` (how
things work now), `OPERATIONS.md` (running it) and this log. Each statement was checked against
the code; where the old text and the code disagreed, the code was written down. The old file is
kept verbatim in `docs/archive/`. No rule, threshold or result changed. Tag: display only.

### 2026-11-25 — Site redesigned as a research application
Sidebar, search, sortable theme table, company pages, a Macro page and a Research and validation
page (`docs/PLATFORM_PLAN.md`, stage 1). Nothing new is computed. Verified on fixtures only.
Tag: display only.

### 2026-11-25 — Backend audit: four defects fixed
`new_issuers_3m` is `None` when the theme was absent three months earlier (it listed every
company). A young group no longer makes the whole macro table unavailable. The tailwind sentence
quotes growth figures. Links with an anchor open the right page. Three misleading labels were
reworded. Six proposals that would change a rule or a result are listed in the plan and wait for
approval; none was adopted. Tag: bug fix.

### 2026-11-25 — Licence statement
The owner stated that their FMP licence covers distribution, so prices, transcripts and derived
figures may be shown to outside readers. The licence was not read during the build.
Tag: data input.

### 2026-11-21 — Title limit removed; active themes always titled
Every active theme is written at each refresh whatever the limit, because four of 44 active themes
still had keyword titles. The same day the limit was removed at the owner's request
(`labels.max_per_refresh: null`, was 40). Tag: display only.

### 2026-11-21 — Audit of universe versions 3 and 4
Checked from the repository alone: prices, transcripts, filings and the XLI and XLB benchmarks are
unverified for the 27 added companies, as is Modine's ticker after its filer's name change. No
sector reference is set for data-centre operators. Tag: data input.

### 2026-11-20 — Universe version 3: electrical and data-centre infrastructure
16 companies added live at the owner's request, `covered_from: 2026-11-20`. Identities checked on
SEC EDGAR; prices and transcripts not checked. Tag: data input.

### 2026-11-20 — Universe version 4: materials
11 companies added live at the owner's request, `covered_from: 2026-11-20`. Qnity Electronics was
left out after the EDGAR check (one annual report). Tag: data input.

### 2026-11-20 — Sector references XLI and XLB
XLI for power and electrical, cooling and building systems, and electrical and infrastructure
contractors; XLB for the three materials sectors. Set before any return of these companies was
looked at. Existing themes keep the reference fixed at their first basket. Tag: rule change.

### 2026-11-20 — Rebuild command and `full_history_through`
The owner asked for a ten-year record that includes companies added later. The marker
(2026-11-20 for `tech-v2`) takes effect only through `themegraph monitor rebuild --confirm`, which
moves the state and site aside, replays 120 month-ends and puts the previous monitor back if it
fails. An existing registry keeps its basis. Tag: rule change.

### 2026-11-20 — Rebuilds and the forward tests
A rebuild starts an empty forward log. Events logged before it stay in the archived registry and
are not pooled with later ones; the 20-event threshold counts from the rebuild. Recorded before
any test was read. Tag: rule change.

### 2026-11-19 — SK hynix SEC filings
SK hynix (`000660.KS`) registered with the SEC in 2026; its Forms 6-K (CIK 2120882, from
2026-07-15) are now read. No rule, threshold or form list changed. Month-ends already processed
are not rebuilt. Tag: data input.

### 2026-11-19 — Company IR documents as a third source
`config/ir_documents.yaml` lists documents on a company's own site, for companies whose
transcripts and filings are thin. Stored apart as `ir:<kind>`. An earnings release within three
days of a stored SEC filing is skipped. The source does not count towards month-end completeness.
The list is empty as shipped. Tag: data input.

### 2026-11-19 — A short macro factor is left out
One factor's short history no longer disables the others: it is left out of the time frame and
named. A macro download problem is copied to `warnings.macro`. The FRED request follows redirects.
Tag: bug fix.

### 2026-11-18 — Business relationships
Added at the owner's request: which covered companies say they do business with which others.
Covered companies only, no lenders or banks. Statements from the last 24 months, three passages
per pair, at most 50 new pairs per refresh. Feeds no measure. Tag: display only.

### 2026-11-17 — Months with no CPI figure
The October 2025 CPI was never published, which cut the usable macro history to nine months. In
the monitor a run of up to three months without a figure counts as "nothing published". The
research pipeline is unchanged. Tag: data input.

### 2026-11-17 — FRED fallback for a short CPI history
When the price provider returns under 80% of the CPI months asked for, the whole series is taken
from FRED (`CPIAUCSL`), never spliced. Not exercised against the live service when written.
Tag: data input.

### 2026-11-17 — Site redesign and company names
Overview leads with emerging and strengthening themes; theme cards and pages reorganised. Company
names added to the coverage lists (`company_names`). No detection rule, membership, basket, return
or threshold changed. Tag: display only.

### 2026-11-16 — Topics (theme groups)
The family tree is also cut at cosine 0.6 (`groups.min_cosine`) to form broader topics, with names
limited to 20 per refresh. The cut was set before any group was seen on live data.
Tag: display only.

### 2026-11-16 — Market cluster names
Each cluster is named after the sector holding at least 60% of its companies, otherwise its two
largest sectors. Tag: display only.

### 2026-11-16 — Macro sensitivity, with five time frames
Added for theme baskets and market clusters: spans of 30, 60, 90, 180 and 360 days (at the owner's
request), up to 144 months, ten non-overlapping stretches per span, and significance from slid
factor histories. It replaced the same day's first version (one span, 36 months, |t| >= 2) before
any live result was seen. Feeds nothing. Tag: rule change.

### 2026-11-16 — Companies against the macro factors
Per-company links and clusters at the owner's request, replacing the same day's first version (36
months, F-test, one span). History of 72, 96 or 144 months by span. The 5% level and the 0.7
cluster cut were set before any result on live data. Tag: rule change.

### 2026-11-15 — Title writing after the first live feed
After a feed with 433 published themes and 31 active: active themes are labelled first, titles of
up to twelve words are accepted, and a label already written for the current keywords is kept when
over the limit. The `anthropic` package is installed with the project. Tag: display only.

### 2026-11-14 — `tech-v2` becomes the one monitor
Every command without `--profile` uses `tech-v2` (127 companies at that date). Before, the default
was the semiconductor pilot and the two were refreshed separately, so one fell behind. `semis`
remains for the research protocol. Tag: rule change.

### 2026-11-14 — Title prompt version 2
Titles are asked to say what the subject is and what is happening to it, in four to nine words.
Themes labelled under the earlier version are labelled again once. Tag: display only.

### 2026-11-14 — Forward tests: reporting clarifications
Recorded before any outcome existed; nothing registered changed. "Ready" needs 20 counted events
with a completed window and a computable primary outcome. Exact zeros are valid outcomes without a
sign. A read stores a nominal p-value; Holm's result is derived only when all three tests are read.
Tag: rule change.

### 2026-11-12 — Headwind forward test registered
Registered before any outcome existed: events logged live once per headwind spell, 126-session
excess over SPY as the primary outcome, a one-sided sign test at 5%, read once at 20 counted
events. The full text is in `docs/DESIGN.md`. Tag: rule change.

### 2026-11-12 — Emergence and tailwind forward tests registered
Two more claims, logged and scored like the headwind test. The overlap rule was stated exactly for
all three, and Holm's correction across the three primary p-values was declared. Exploratory
splits were declared so they cannot be chosen later. Tag: rule change.

### 2026-11-12 — Transcript Q&A boundary corrected
Found in a code review. The operator's opening mention of a Q&A session had been taken as its
start, so prepared remarks were labelled Q&A in those transcripts. Genres, and therefore topics,
change. Earlier declared results stand; a monitor state built before this should be rebuilt.
Tag: bug fix.

### 2026-11-12 — Matching lookback off-by-one
A theme absent for exactly 6 month-ends is now matched, as `match.lookback_snapshots` says; the
code had stopped at 5. Themes given a new ID before the fix are not re-joined. Tag: bug fix.

### 2026-10-29 — Headwind direction and theme families declared
Declared from the text alone, before any result: headwind at 20% negative passages, twice the
genre-matched rate and above the positive share, tailwind symmetrically; families at cosine 0.85.
Tag: rule change.

### 2026-10-29 — Beyond-index correlation computed from residuals
The earlier subtraction left rounding noise at full shrinkage, which the monitor ranked against
random baskets. The monitor now gives no verdict when the comparison carries no information. Not a
specification change. Tag: bug fix.

### 2026-10-29 — Basket repairs for coverage expansions
From this date the monitor no longer writes an empty basket version or backdates a newly-covered
entry processed more than 35 days late. Earlier state is not rewritten; such themes show "Not
valued". Tag: bug fix.

### 2026-10-29 — Universe version 2: software, cybersecurity and data/cloud
51 companies added to the `tech-v2` universe with full history (date from the universe change
log). Benchmarks SPY and XLK, with SOXX, IGV and CIBR as sector references. Tag: data input.

### 2026-10-28 — Market layer v2
Decided by the owner before any result under it was seen: simple daily returns linked
geometrically and single-index shrinkage on SPY replace residuals on SPY and SOXX. SOXX had
removed the co-movement a semiconductor theme should show. Earlier results stand. Tag: rule change.

### 2026-10-27 — The holdout counts as inspected
The monitor reads data through the current date, which includes the 2022 to 2026 holdout. Clean
evidence now comes only from the forward log and live index days. Tag: rule change.

### 2026-10-27 — Universe version 1 and the technology pilot selection
The semiconductor pilot universe as of the monitor's first release (76 companies). The 51
technology pilot companies were selected and their identities checked on SEC EDGAR on this date;
the pilot was implemented and tested on fixtures only. Tag: data input.

## Changes the sources do not date

- Status moved from the share of all text to the fixed-cohort share, because adding companies had
  marked unchanged themes as cooling. Tag: rule change.
- The newly-covered route and the `observed` field for months with missing evidence were added.
  Tag: rule change.
