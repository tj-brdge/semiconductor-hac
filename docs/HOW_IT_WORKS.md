# How it works, in plain language

One page for someone new to the project. How to run it is in
[OPERATIONS.md](OPERATIONS.md); how each measure is calculated is in
[METHOD.md](METHOD.md).

## The question

What are companies talking about more than before, which companies are involved,
and how have those companies' shares done? Discussion and prices are measured
separately and never mixed into one score.

The monitor covers 154 companies: semiconductors, software and security,
electrical and data-centre infrastructure, and materials. "Strongest" on the
site always means strongest among these companies.

## The steps

| # | Step | What happens |
|---|---|---|
| 1 | Collect | Download daily prices, earnings-call transcripts and SEC filings for the company list. Every record keeps the time it became available, so nothing is used before it existed. |
| 2 | Split | Cut each document into passages (a speaker's turn, a filing section). Tables and legal boilerplate are dropped. |
| 3 | Embed | Turn each passage into a set of numbers that captures its meaning. Each passage is paid for once. |
| 4 | Find topics | At each month-end, take the last 180 days of passages and group those that mean similar things. A group discussed by several companies is a topic. |
| 5 | Keep identity | Match this month's topics to last month's, so a theme keeps one ID over time. Splits and merges are recorded. |
| 6 | Decide what to publish | A topic becomes a published theme when it is **emerging** (its share of discussion grew fast across comparable companies), **established** (present in 6 of the last 9 months, with at least 4 companies) or **newly covered** (long-running among companies just added). |
| 7 | Describe | Each month, measure the theme's share of discussion, its growth over three months, its companies, and whether the language is about problems or growth. Quote the passages closest to its centre. A title and short summary are written from those passages. |
| 8 | Build a basket | Every company with at least 5% of its recent text in the theme goes into an equal-weight basket, bought at the next close and held. A change of membership must hold for two months. |
| 9 | Compare | Basket return against the S&P 500, the technology sector and a sector benchmark where one fits. Separately: whether the basket's shares move together beyond the market, and which shares group together on prices alone. |
| 10 | Publish | Write a complete release, check it, then switch the website to it. A failed step leaves the last good release in place and says what failed. |

## What the website shows

- **Overview:** counts, then tables of emerging and strengthening themes, themes
  where companies describe problems, long-running themes, and baskets gaining or
  losing ground against the market.
- **Themes:** every published theme in one sortable, filterable table.
- **A theme page:** headline facts, the basket against its benchmarks, discussion
  over time, the companies, quoted evidence, and the theme's record.
- **Companies:** every covered company and its themes. A company page adds its
  price links, macro links, stated relationships and quotes.
- **Market structure:** which shares have traded together over the last six
  months, from prices only.
- **Relationships:** how companies connect, by trading together, by sharing
  themes, and by what they say about doing business with each other.
- **Macro:** how shares, baskets and price groups have moved with interest
  rates, oil, inflation and the dollar.
- **Research and validation:** what each measure describes, how it is checked,
  and what has not been established. Read this page before relying on the rest.
- **Methodology and data:** the method, the coverage list, data dates, and
  whether the last update succeeded.

## Words used on the site

| Word | Meaning |
|---|---|
| Simulated | History rebuilt after the fact: what the rule would have held. It uses today's company list, so it flatters the past. Not a track record. |
| Live | Days after the theme was actually published by the monitor. Only these are free of hindsight. |
| Discussion 1.3× | The theme's share of comparable companies' text is 1.3 times what it was three months ago. |
| Headwind / tailwind | Management and filing passages use far more problem (or growth) phrases than usual for that kind of text. A phrase count, not a forecast. |
| Trading together | The basket's shares are more correlated, beyond the market, than random baskets of the same size and sectors. |
| Family, topic | Themes close in meaning, shown together. Nothing is merged. |
| Missing evidence | A month where the theme's companies had no documents: shown as a gap, never as zero. |
| Pending month-end | A month-end waiting for its documents to be downloaded before themes are updated. Prices still update. |

## What is and is not established

- **Nothing has been shown to predict returns.** Not that emerging or tailwind
  baskets beat the market, and not that headwind baskets lag.
- Three forward tests (headwind, emergence, tailwind) were written down before
  any outcome existed. They run on live data only: the monitor logs events as
  they happen, and each test is read once, after 20 events have six months of
  prices behind them. None has been read.
- The simulated history cannot stand in for those tests. It uses today's
  company list, and the monitor has already read the years 2022 to 2026 that
  the research design had set aside as an untouched test period.
- The software is tested end to end on synthetic data, and the website has been
  checked on synthetic data only. Outputs from real data have not yet been
  reviewed ([PLATFORM_PLAN.md](PLATFORM_PLAN.md), stage 3).
- Written titles, summaries and relationships come from Claude reading quoted
  passages. They are display text and change no theme, basket or return.

## Rules we keep

1. Nothing uses information before it was available.
2. Thresholds are set from text, never tuned on returns.
3. Published history is never rewritten; changes to a rule are dated and recorded
   in [DECISIONS.md](DECISIONS.md).
4. Simulated and live are always labelled.
