# Operations: running the monitor

How to install, refresh, view and repair the monitor as it works today. What the
measures mean is in [METHOD.md](METHOD.md); the plain-language overview is in
[HOW_IT_WORKS.md](HOW_IT_WORKS.md).

Run every command in Terminal from the repository folder. Without `--profile`,
commands use the one monitor, `tech-v2` (154 companies): working files in
`monitor_state-tech-v2/` (`<state>` below), website in `site-tech-v2/` (`<site>`).

## Install

You need [uv](https://docs.astral.sh/uv/), which also installs the right Python
(3.12 or later). Repeat `uv sync` after every code update.

```bash
cd /path/to/semiconductor-hac
uv sync
```

## Credentials

Credentials are environment variables. Nothing secret is written to the website.

| Variable | Used for | Needed |
|---|---|---|
| `FMP_API_KEY` | Prices, macro series and call transcripts (Financial Modeling Prep) | Always, for live data |
| `VOYAGE_API_KEY` | Turning new passages into vectors (Voyage) | Always, for live data, even with `--no-ingest` |
| `EDGAR_USER_AGENT` | SEC filings. The SEC requires a name and a contact email in every request; without an `@` in this value the download refuses to start | Whenever documents are downloaded |
| `ANTHROPIC_API_KEY` | Written titles, summaries, topic names and business relationships (Claude) | Optional; without it titles stay as keywords |

Put them in a text file, `~/.themegraph.env`, which the scheduled job also
reads. Use plain quotes, not curly ones:

```bash
export FMP_API_KEY='your-fmp-key'
export VOYAGE_API_KEY='your-voyage-key'
export ANTHROPIC_API_KEY='your-anthropic-key'
export EDGAR_USER_AGENT='Your Name you@example.com'
```

Then run `chmod 600 ~/.themegraph.env` once, and `source ~/.themegraph.env` in
each new Terminal window before a refresh.

## Everyday: refresh and view

```bash
uv run themegraph monitor refresh     # update everything that is due, then publish
uv run themegraph monitor serve       # http://127.0.0.1:8780/  (Ctrl+C stops it)
```

The refresh prints a short result (`ok`, `errors`, `warnings`). Variants:

```bash
uv run themegraph monitor refresh --documents     # download documents now, due or not
uv run themegraph monitor refresh --prices-only   # prices only, never documents
uv run themegraph monitor refresh --no-ingest     # download nothing; recompute and publish
uv run themegraph monitor refresh --full-prices   # download every price history again
```

To try synthetic sample data, give it its own folders so it never touches the
live monitor. `--fixtures` needs `--as-of` (the sample data ends on 2021-12-31),
costs nothing and needs no credentials:

```bash
uv run themegraph monitor refresh --fixtures --as-of 2021-12-31 \
    --state monitor_state-sample --site site-sample
uv run themegraph monitor serve --site site-sample
```

## What a refresh does

In order, each stage recorded in `<site>/data/status.json`:

1. **Prices**, every run, with the macro series (rates, oil, inflation, the
   dollar). Stored history is reused unless the provider restated it, which is
   how a split or dividend shows up.
2. **Documents** (transcripts, SEC filings, listed IR documents), when due: no
   full download yet, the last one is 7 days old, or a month-end has settled.
3. **Themes**, only when an unprocessed month-end is ready, which needs a
   document download made at least 5 days after it. Until then it is *pending*:
   prices still update and the site shows a notice. The very first run replays
   the last 120 month-ends (simulated history).
4. **Titles, topic names and relationships**, if a Claude key is present. A
   failure here is only a warning.
5. **Valuation and export**: every basket is revalued, then a complete release
   is written, checked and made current. The last five releases are kept.

If a required stage fails, the later ones are skipped, nothing is published and
the previous release stays on the site. A second refresh with the same data
changes nothing. Processed month-ends are never rewritten; a document that
arrives later is counted as late and used in later months. The settings quoted
(7 days, 5 days, 120 month-ends) are in `config/monitor.yaml`.

## Titles and summaries

A theme's title comes from the first that applies: a hand-written entry in
`config/theme_labels.yaml` (by theme ID, with `title` and `summary`), Claude
(from the theme's quoted passages), or automatic keywords. With
`ANTHROPIC_API_KEY` set, each refresh writes every waiting title, one Claude
call each, and again only if the theme's keywords change. Without a refresh:

```bash
uv run themegraph monitor titles --count        # how many are waiting; no calls
uv run themegraph monitor titles                # write them all (add --limit N to cap)
uv run themegraph monitor refresh --no-ingest   # publish them
uv run themegraph monitor feed                  # every theme, as text for a reviewer
```

The feed says at the top whether the key is present on this machine.

## Business relationships

Which covered companies say they supply, buy from, partner with, compete with or
own each other, read by Claude from the last 24 months of their own statements.
Each refresh reads up to 50 new pairs; a pair is read again only when a newer
passage appears. Results are in `<state>/relations.json` and survive a rebuild.
For the first, large batch:

```bash
uv run themegraph monitor relations --count     # how many pairs are waiting; no calls
uv run themegraph monitor relations             # read them (add --limit N to cap)
uv run themegraph monitor refresh --no-ingest   # publish
```

## Company IR documents

For a company with few transcripts and filings, documents on its own site can
be listed by hand in `config/ir_documents.yaml` (the file explains the format;
the list is empty as shipped). Each link is read once, by any refresh that
downloads documents or by `uv run themegraph ingest ir`. Month-ends already
processed are not changed.

## Adding companies and rebuilding history

Companies are listed in `config/tech_v2/universe.yaml`. A company added there
with a `covered_from` date is followed from that date; the past is not changed.

A **rebuild** is the other choice: start again and replay ten years with today's
list. In the universe file, set `full_history_through` to the date of the latest
addition (it is currently `2026-11-20`), then:

```bash
uv run themegraph monitor rebuild             # prints the plan; changes nothing
uv run themegraph monitor rebuild --confirm   # does it
```

The current folders are renamed to `monitor_state-tech-v2.archive-<time>` and
`site-tech-v2.archive-<time>`, never deleted, and a fresh refresh runs. Until
then each refresh warns that a rebuild is waiting (`warnings.history`).

The cost: the whole history becomes simulated, built with today's company list;
live days are no longer live. Theme IDs, titles (one Claude call each) and the
forward-test log start again. It takes hours and a lot of memory.

## Watching a long run

Every printed line starts with elapsed time and memory, for example
`[1:42:10 | memory 9.6 GB, peak 10.8 GB]   Text clustering: 61/120, about 0:48:00 left`.
The time-left estimate tends to run short early on. From a second Terminal
window, `uv run themegraph monitor progress` prints the stage, the position,
memory now and the peak so far; it starts nothing. If the run was killed it says
"Stopped without finishing".

## Scheduling

On the Mac, one script installs a job that runs the refresh every weekday:

```bash
scripts/install_schedule.sh            # weekdays at 19:00 local time
scripts/install_schedule.sh 21 30      # weekdays at 21:30
scripts/install_schedule.sh remove
```

Choose a time an hour or two after the US market close. The job reads
`~/.themegraph.env` and appends to `logs/refresh.log`. If the Mac is asleep, it
runs when the Mac next wakes.

To put the site on the web, upload the whole `site-tech-v2/` folder to any
static host after each refresh, including a failed one, so that visitors see the
failure notice beside the last good release.

## When something goes wrong

Start with `<site>/data/status.json`. The fields to read:

| Field | Meaning |
|---|---|
| `ok` | `false` if any required stage failed or was skipped |
| `errors`, `skipped` | Which stage failed and why; which stages did not run because of it |
| `warnings` | Optional parts that failed: `labels`, `group_names`, `relations`, `macro`, `history` |
| `last_attempt` | Each stage's detail and seconds; the prices stage names symbols that returned nothing |
| `published_release`, `data_as_of` | The release on the site, and the dates prices, documents and themes run to |
| `stale`, `price_gaps` | Prices or documents that are out of date, and the symbols behind |
| `pending_snapshots`, `late_documents` | Month-ends waiting for a document download; documents that arrived too late for theirs |
| `metrics` | Total seconds, `peak_total_mb`, `peak_mb_by_stage`, and counts of provider requests |

**A refresh failed.** The previous release is still on the site. Read `errors`,
fix the cause and run it again. A missing key is reported by name, for example
"FMP_API_KEY is not set". If themes lag behind prices, read `pending_snapshots`
and run `uv run themegraph monitor refresh --documents`.

**The site looks unchanged after a code update.** The site's page files are
copied into `<site>` only when a refresh publishes a release. Run
`uv run themegraph monitor refresh --no-ingest`, then hard-refresh the browser
(Cmd+Shift+R). If it is still unchanged, the program may have been installed as
a fixed copy rather than from this folder: run `uv sync` and refresh again.

**Titles are still keywords.** Either `ANTHROPIC_API_KEY` was not in the
environment where the refresh ran (`warnings.labels` says so; for the scheduled
job it must be in `~/.themegraph.env` with `export`), or Claude judged the
theme's passages unclear: those themes keep their keywords and are not asked
again. `uv run themegraph monitor titles --count` shows both numbers.

**Macro tables are unavailable.** They need the macro series, which arrive with
prices; `warnings.macro` reports a failed download. A month with no published
inflation (CPI) figure counts as no change, and a short CPI history from the
provider is replaced from FRED. A factor still too short is left out and named
on the page; the others are shown. Macro never blocks a release.

**A rebuild failed.** The command puts the previous monitor back and moves the
failed attempt to `monitor_state-tech-v2.failed-<time>` and
`site-tech-v2.failed-<time>`. If it prints "RECOVERY WAS NOT COMPLETE", it lists
which folder to rename; nothing has been deleted. If the process was killed
outright, nothing is put back: move the half-built folders away and rename the
two `.archive-<time>` folders to their original names.

**The Mac ran out of memory.** The run is killed and `monitor progress` says it
stopped. An ordinary refresh loses nothing: the last release stays current.
Close other applications and run it again; text already embedded is not paid
for twice. For a rebuild, see above. The last full build (127 companies) peaked
near 11 GB; a ten-year build of 154 has not been measured here.

## Command reference

"Paid" means the command can make charged or metered calls: FMP downloads,
Voyage embeddings for text not yet embedded, or Claude calls. Downloads and
embeddings are cached. Every `monitor` command takes `--profile`, `--state` and
`--site`; `uv run themegraph <command> --help` lists every option. The last four
rows are the earlier research study ([DESIGN.md](DESIGN.md)), not the monitor.

| Command | What it does | Paid |
|---|---|---|
| `monitor refresh` | Update prices, documents when due, themes, titles, baskets; publish | Yes: FMP, Voyage, Claude |
| `monitor refresh --no-ingest` | Recompute and publish from stored data | Claude for waiting titles and pairs; Voyage only if a month-end is processed |
| `monitor refresh --fixtures --as-of DATE` | The same on synthetic sample data | No |
| `monitor serve` | Show the site at `http://127.0.0.1:8780/` (`--port`) | No |
| `monitor progress` | Where a running refresh or rebuild is, and its memory | No |
| `monitor titles` | Write waiting titles (`--count`, `--limit N`) | Yes: Claude (`--count`: no) |
| `monitor relations` | Read waiting company pairs (`--count`, `--limit N`) | Yes: Claude (`--count`: no) |
| `monitor feed`, `monitor review` | Write `<state>/themes_feed.md`; write `<state>/review.md` (similar themes, families, tone) | No |
| `monitor forward` | Write `<state>/forward_tests.md`: progress of the three forward tests. `--read NAME` reads one result, once, and is refused before 20 events are complete | No |
| `monitor rebuild` | Print the rebuild plan; with `--confirm`, rebuild | With `--confirm`: FMP, Voyage, Claude |
| `monitor compare` | Compare this monitor with another profile (`--against`, default `semis`) | No |
| `ingest fmp-prices`, `fmp-macro`, `fmp-transcripts` | Download one source by hand | Yes: FMP |
| `ingest edgar`, `ingest ir` | SEC filings; listed company IR documents (`--symbols`) | No |
| `coverage`, `demo` | Table of stored data per company; research pipeline on synthetic data, into `runs/demo/` | No |
| `run`, `topics`, `forward predict` | Research runs on stored data | Voyage, for text not yet embedded |
| `forward outcomes`, `report`, `adjudicate`, `questions`, `compare`, `freeze`, `labels` | Research bookkeeping on existing runs | No |
| `review RUN...` | Local page for reviewing research candidates | Claude, if a key is set |
