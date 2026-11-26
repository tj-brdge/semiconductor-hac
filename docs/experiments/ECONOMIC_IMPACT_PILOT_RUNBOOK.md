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
