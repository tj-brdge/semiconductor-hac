# Economic-impact extraction pilot: runbook

How to carry out the frozen protocol, `ECONOMIC_IMPACT_PILOT.md` (version 1). The protocol is the
authority. Tooling: `scripts/impact_pilot.py` (logic in `experimental/impact_run.py`).

## Running the pilot

Run on the machine that holds the monitor's data, from the repository root. Every command prints
what it did and the next step; `status` shows where things stand at any time.

```bash
PILOT=".venv/bin/python scripts/impact_pilot.py"     # add --profile semis for the other monitor
$PILOT status
```

| # | Command | What it does | Cost |
|---|---|---|---|
| 1 | `$PILOT export` | Links stored passages to published themes for the registry's latest 12 month-ends. Read-only: no download, no model; the registry is not written (checked by hashing the state folder before and after). | None, unless embeddings are missing from the cache: then it stops. `--allow-embed` computes them (a paid embedding call). Minutes of local clustering. |
| 2 | `$PILOT sample` | Draws the 300 passages once (seed 20261126); writes the two reader sheets and the guide. | None |
| 3 | Readers label | Each reader fills their own sheet alone. | Readers' time |
| 4 | `$PILOT run --confirm-cost` | One call per sampled passage to `claude-opus-5-5`, low effort, no fallback model. | About $5.60 (range $3 to $8), protocol section 9; the actual tokens and dollars are printed at the end. |
| 5 | `$PILOT score` | Reader agreement, adjudication, both methods' labels, the criteria, `result.md`. | None |

### 1. Export

- Do not run it while a refresh is running. Do it once: after `sample`, the export is not replaced.
- A theme-month is linked only when the rebuilt cluster equals the registry's own record of it
  exactly; nothing is matched approximately. If some cannot be linked (usually documents
  downloaded after that month-end was processed) the command lists them and writes nothing.
  `--allow-partial` writes the exact links only; the result then states this limitation.

### 2. Sample, and what must be committed before the model run

- A shortfall is printed cell by cell and nothing is written. The quotas are not changed here:
  that needs a new protocol version.
- **Commit the sample IDs before any model answer exists:**

  ```bash
  mkdir -p docs/experiments/impact_pilot
  cp runs/impact_pilot/sample.csv docs/experiments/impact_pilot/
  git add docs/experiments/impact_pilot/sample.csv && git commit -m "Impact pilot: sample IDs"
  ```

  `sample.csv` holds IDs, strata and tags, no passage text. `run` refuses to start until the
  sample and both reader sheets exist; committing is the owner's step.

### 3. Readers

- Give reader A only `labels_reader_a.csv` and `READER_GUIDE.md`; reader B only
  `labels_reader_b.csv` and the guide. The sheets hold the passage, company, role and date, in a
  different order for each reader, and nothing about themes, tone, tags or either method.
- **Readers must not see `sample.csv`, `answers.jsonl`, `predictions.csv`, `disagreements.csv`
  or `result.md` until both sheets are final.** Safest: run step 4 only after both filled sheets
  are back and their labels committed (protocol section 5).

### 4. Model run

- Needs `ANTHROPIC_API_KEY` in the shell (as for the monitor's titles). Re-check the prices in
  protocol section 9 on the day; the command prints that section before it starts.
- `--limit 20` asks 20 passages first, to measure tokens. A rerun asks only what is unanswered;
  refusals and truncated answers are stored as invalid and are not asked again.

### 5. Score

- Refuses until both sheets are fully labelled. Kappa below 0.60: `result.md` gives no verdict.
- If the readers differ, it writes `disagreements.csv` and stops. The readers settle each row
  (fill the empty `adjudicated_*` cells and a one-line `reason`; the owner decides where they
  cannot), save it as `adjudicated.csv` in the same folder, and `score` is run again.
- `result.md` lists every criterion with its threshold, the measured value and PASS or FAIL,
  the hard cases separately, and the verdict. Record it in the protocol's section 10, pass or
  fail, with no change to the criteria.

## Where files go

| File | Where | In git? |
|---|---|---|
| `export.parquet`, `export_report.json`, reader sheets, `READER_GUIDE.md`, `disagreements.csv`, `adjudicated.csv` | `runs/impact_pilot/` | No (passage text) |
| `answers.jsonl` (the answer cache) | `<state>/experimental/impact_pilot/` | No |
| `sample.csv`, `labels_final.csv`, `predictions.csv`, `result.md` | `runs/impact_pilot/`, then copied to `docs/experiments/impact_pilot/` | Yes: `sample.csv` before the model run, the rest after `score` |

## Version 2: invited labellers

Protocol: `ECONOMIC_IMPACT_PILOT_V2.md` (awaiting the owner's approval). It replaces steps 3 and 5
above; `export`, `sample` and `run` are the same commands. The two reader sheets that `sample`
writes are not used, and `score` is not run. Logic: `experimental/impact_crowd.py` (rules) and
`impact_crowd_run.py` (files). None of the `crowd-*` commands uses a model or the network.

| # | Command | What it does |
|---|---|---|
| 1 | `$PILOT export`, `$PILOT sample` | As above. Commit `sample.csv` as above. |
| 2 | `$PILOT crowd-codes --n 24 --out runs/impact_pilot/codes.txt` | Makes random labeller codes (four consonants, four digits). 300 passages need at least 20 labellers at 3 labels a passage and 45 passages each; invite 24 to 26. |
| 3 | `$PILOT crowd-pack --codes runs/impact_pilot/codes.txt` | Writes `runs/impact_pilot/crowd/` (the page and one data file per code) and `crowd_assignment.csv`. Refuses a code that looks like a name or an email, too few codes, or a folder that fails its own blindness check. `--per-passage 5` needs 34 codes. |
| 4 | Invite | See below. |
| 5 | `$PILOT crowd-collect runs/impact_pilot/returned/labels_*.json` | Checks every returned file, applies the attention-check rule, writes `crowd_labels/`. Exit code 2 and no `adjudicated.csv` while too few passages are resolved. Pass every file returned so far, each time. |
| 6 | (if needed) add new codes to `codes.txt`, then `crowd-pack` again, invite, `crowd-collect` again | New labellers get only the passages still needing labels. Existing data files are not changed. |
| 7 | Commit the labels (below), then `$PILOT run --confirm-cost` | The model run, as above, only after the commit. |
| 8 | `$PILOT crowd-score` | Version 1's criteria against the majority labels; writes `result_v2.md` and `predictions.csv`. Refuses a hand-edited `adjudicated.csv`. |

### What to send each labeller

- Keep, outside the repository, your own list of who has which code. No tool here reads or writes it.
- **Hosted:** put the contents of `runs/impact_pilot/crowd/` on a static host that does not list
  folders and is not indexed, and send each person their own link: `<address>/index.html?code=<their code>`.
- **By file instead:** send `index.html` and that person's `data/<code>.json` only. They open
  `index.html`, and choose the data file when the page asks.
- Tell them: about 30 to 40 minutes; they can stop and continue on the same device; at the end the
  page downloads `labels_<code>.json`, which they send back to you.
- Never send `sample.csv`, `crowd_assignment.csv`, another person's data file or anything from
  `crowd_labels/`. Do not discuss passages with labellers while collection is open.

### What to collect

- Save each returned `labels_<code>.json` in `runs/impact_pilot/returned/`. If someone sends a
  newer file, replace the old one (two files with one code are both rejected).
- Read `crowd_labels/crowd_report.md` after each `crowd-collect`: rejected files and why, who is
  valid, excluded, incomplete or not returned, agreement, unresolved passages, and how many labels
  are still needed and from whom. `labels_needed.csv` lists the passages.
- Do not edit a returned file or `adjudicated.csv`. A rejected file goes back to its labeller.
- Collection closes when the labels are committed. Then remove the folder from the host.

### What is committed, and what is not

| File | In git? |
|---|---|
| `crowd_assignment.csv`; from `crowd_labels/`: `labellers.csv`, `labels_long.csv`, `adjudicated.csv`, `crowd_report.md`, `crowd_summary.json` (codes and labels only) | Yes, to `docs/experiments/impact_pilot/`, **before the model run** |
| `predictions.csv`, `result_v2.md` | Yes, after `crowd-score` |
| `codes.txt` | May be committed (codes only); not needed |
| The list of who has which code | **Never** |
| `returned/labels_*.json` (they hold the free-text comments) | **Never** |
| `crowd/` (passage text), `export.parquet` | No |
