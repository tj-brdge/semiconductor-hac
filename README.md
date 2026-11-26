# semiconductor-hac · themegraph

`themegraph` reads what listed companies say in earnings calls and SEC filings
and finds the subjects that several of them are discussing more than before: the
themes. Each month it measures every theme again, and it tracks an equal-weight
basket of the theme's companies against market and sector benchmarks. The results
are published as a static website that one command keeps up to date.

Discussion and prices are measured separately and never mixed into one score.

## Status

The monitor runs on one list of companies, the default profile `tech-v2`: 154
companies in semiconductors, software and security, electrical and data-centre
infrastructure, and materials. What is and is not known:

- **Nothing here has been shown to predict returns.** Three forward tests are
  registered and collect events from live refreshes; none has been read.
- **Simulated history uses today's company list.** It leaves out companies that
  no longer exist and includes companies nobody would have picked at the time,
  so it flatters the past. Only days after a theme was actually published are
  live, and the site labels every figure as simulated or live.
- **The site has been verified on synthetic sample data only.** The build
  environment cannot reach the data providers. Refreshes on real data run on the
  owner's machine, and those outputs have not yet been reviewed.
- The code is covered by offline automated tests (`uv run pytest -q`).

## Quick start

You need [uv](https://docs.astral.sh/uv/). This runs one refresh on synthetic
sample data, in its own folders, and shows the site. It needs no keys and costs
nothing.

```bash
uv sync
uv run themegraph monitor refresh --fixtures --as-of 2021-12-31 \
    --state monitor_state-sample --site site-sample
uv run themegraph monitor serve --site site-sample    # http://127.0.0.1:8780/
```

`--fixtures` needs `--as-of`; the sample data ends on 2021-12-31. The sample
site says that its data is synthetic.

For real data you need API keys and one command, `uv run themegraph monitor
refresh`. See [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Where to read

| File | What it is |
|---|---|
| [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | The whole process in plain language. Start here. |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Running it: install, keys, refresh, scheduling, what to do when something fails, every command. |
| [docs/METHOD.md](docs/METHOD.md) | How each measure works today. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | What changed and when. |
| [docs/PLATFORM_PLAN.md](docs/PLATFORM_PLAN.md) | The plan, and proposals waiting for a decision. |

Research records, kept for the record. They are not needed to run or understand
the monitor:

- [docs/DESIGN.md](docs/DESIGN.md): the original research design and its
  declared tests.
- [docs/EVIDENCE.md](docs/EVIDENCE.md): what the development runs found.
- [docs/TECH_PILOT_V2.md](docs/TECH_PILOT_V2.md): how the software and security
  companies were chosen.
- [docs/archive/](docs/archive/): superseded documents.

## Repository layout

```
config/            company lists, rules and thresholds; profiles/ names each monitor
labels/            hand-labelled themes for the research tests
scripts/           the scheduled refresh for macOS
src/themegraph/
  data/ market/ text/ graph/ eval/ report/   the research pipeline
  monitor/         the monthly monitor; monitor/site/ is the website
  cli.py           every command
tests/             offline tests and sample data
docs/              see "Where to read"
```

Created when you run it, and not in git: `.venv/`, `data/` (downloads and
caches), `runs/` (research runs), `logs/`, `monitor_state-tech-v2/` (the
monitor's working files) and `site-tech-v2/` (the website).
