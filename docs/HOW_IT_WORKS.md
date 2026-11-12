# How it works, in plain language

One page for someone new to the project. Details and every threshold are in
[MONITOR.md](MONITOR.md) (the website) and [DESIGN.md](DESIGN.md) (the research).

## The question

What are companies talking about more than before, which companies are involved,
and how have those companies' stocks done? Discussion and prices are measured
separately and never mixed into one score.

## The steps

| # | Step | What happens | Code |
|---|---|---|---|
| 1 | Collect | Download daily prices, earnings-call transcripts and SEC filings for a fixed list of companies. Every record keeps the time it became available, so nothing is used before it existed. | `ingest.py`, `data/` |
| 2 | Split | Cut each document into passages (a speaker's turn, a filing section). Tables and legal boilerplate are dropped. | `text/passages.py` |
| 3 | Embed | Turn each passage into a vector that captures its meaning (Voyage finance model). Vectors are cached, so each passage is paid for once. | `text/embed.py` |
| 4 | Find topics | At each month-end, take the last 180 days of passages and group passages that mean similar things. A group discussed by several companies is a topic. | `text/cluster.py` |
| 5 | Keep identity | Match this month's topics to last month's (shared passages, or similar meaning after a gap), so a theme keeps one ID over time. Record splits and merges. | `monitor/registry.py` |
| 6 | Decide what to publish | A topic becomes a published theme when it is **emerging** (its share of discussion grew fast across comparable companies), **established** (present in 6 of the last 9 months, 4+ companies) or **newly covered** (long-running among companies just added). | `monitor/registry.py` |
| 7 | Describe | Measure each theme every month: share of discussion, growth over 3 months, companies, persistence, whether the language is about problems or growth (headwind / tailwind), and quote the passages closest to its centre. | `monitor/registry.py`, `tone.py`, `clusters.py` |
| 8 | Build a basket | Every company with at least 5% of its text in the theme goes into an equal-weight basket, bought at the next close and held. A membership change needs two months in a row and starts a new basket version; the index is chain-linked. | `monitor/baskets.py` |
| 9 | Compare | Basket return against SPY, a technology benchmark and a sector benchmark where one fits; whether the basket's stocks move together beyond the market; which stocks cluster together on prices alone. | `baskets.py`, `comovement.py`, `market_clusters.py` |
| 10 | Publish | Write a complete release of JSON files, check it, then switch the website to it. A failed step leaves the last good release in place and says what failed. | `monitor/refresh.py`, `export.py` |

## What the website shows

- **Overview:** emerging themes, headwinds (candidates for short ideas),
  established themes, and lists of baskets gaining or losing against the market.
- **Theme page:** four quick facts, the basket chart against benchmarks, the
  discussion chart with points where it strengthened or weakened, returns by
  period, the companies with their weights and contributions, and quoted evidence.
- **Clusters:** which stocks have traded together over the last six months, from
  prices only.
- **Method:** coverage, data dates, and whether the last update succeeded.

## Words used on the site

| Word | Meaning |
|---|---|
| Simulated | History rebuilt after the fact: what the rule would have held. Uses today's company list, so it flatters the past. Not a track record. |
| Live | Days after the theme was actually published by the monitor. Only these are free of hindsight. |
| Discussion 1.3× | The theme's share of comparable companies' text is 1.3 times what it was three months ago. |
| Headwind / tailwind | Management and filing passages use far more problem (or growth) phrases than usual for that kind of text. A phrase count, not a forecast. |
| Trading together | The basket's stocks are more correlated, beyond the market, than random baskets of the same size and sectors. |
| Family | Themes so close in meaning that the overview shows one card for them. Nothing is merged. |
| Missing evidence | A month where the theme's companies had no documents: shown as a gap, never as zero. |

## What is and is not established

- Tested on synthetic data end to end (about 200 automated tests).
- The research design reserved 2022–2026 as an untouched test period; the monitor
  reads through today, so that period now counts as inspected. Clean evidence can
  only come from the forward log of themes published from now on.
- Not yet shown: that emerging or tailwind themes' baskets beat the market, or
  that headwind baskets lag. Each is a registered forward test: the monitor logs
  events live and each test is read once, after 20 events have six months of
  prices behind them (Method page, "Forward tests").

## Rules we keep

1. Nothing uses information before it was available.
2. Thresholds are set from text, never tuned on returns.
3. Published history is never rewritten; changes to a rule are dated and recorded.
4. Simulated and live are always labelled.
