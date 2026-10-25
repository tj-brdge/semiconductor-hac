# Evidence summary: window vs anchored topics (development period)

Status on 2026-10-25. Holdout not run. Outcome returns are not used for any
judgement here.

## Runs on record (confirmation of `anchor_assign_similarity` 0.40)

Both runs were made by the user from one clean checkout. Values are copied from
their `manifest.json` files as supplied; the run directories are on the user's
machine, not in this repository.

| | window | anchored (0.40) |
|---|---|---|
| git commit | 1b3113a0524f | 1b3113a0524f |
| git_dirty | false | false |
| source_sha256 | 8b7786c181c4… | 8b7786c181c4… |
| pipeline.yaml sha256 | 53a6a71500f8… | 53a6a71500f8… |
| universe.yaml sha256 | 170a94e1c58e… | 170a94e1c58e… |
| labels sha256 | 59e86f285458… (blank markers) | same |
| config override | none | `text.topic_mode: anchored` |
| inputs (scheme 2) prices | 0dba2415b56a… | same |
| inputs documents | 77516434179b… | same |
| vintages | prices 2, transcripts 2, edgar 4, macro 2 | same |
| period / snapshots / end | dev / 144 / 2021-12-31 | same |
| embedder | voyage-finance-2 | same |
| environment | Python 3.12.8, macOS-15.1.1, numpy 2.5.4, pandas 3.1.0, scipy 1.18.1, scikit-learn 1.9.1 | same |
| ledger_sha256 | c8ab5efd3156… | b5b3bcc92ba4… |
| results_sha256 | bcd792fabe3e… | 4128493bab63… |

These runs are the record of the failed 0.40 confirmation (DESIGN.md). Their
provenance is historical and is not to be edited.

## Metric definitions

Window-level figures come from the 144 monthly windows of 180 days each.

- **Assigned (unassigned) share, median:** the median over windows of (passages in
  a topic / all passages in the window). This is the basis of the confirmation
  criterion.
- **Pooled share:** total assigned / total passages over all windows. Pooled shares
  reconcile across stages; medians do not.
- **Tightness:** the median over (topic, window) of the mean cosine between member
  passages and their centroid. Passages are centred on run-wide genre means; this
  representation is used for diagnostics only.
- **Month-to-month cosine:** cosine between a topic's member centroids in
  consecutive windows. **First-to-last cosine:** for topics living more than 14
  windows.
- **Candidates:** first flags in the ledger. **Near-duplicate pair:** first flags
  within 365 days whose member centroids have cosine ≥ 0.8.
- **Anchored stage totals:** integer counts per window of passages claimed by
  existing topics, rejected by the size/issuer rule, discovered in unclaimed text,
  and assigned. They satisfy claimed - rejected + discovered = assigned exactly.

## Measured, estimated, judged

| | window | anchored 0.40 | basis |
|---|---|---|---|
| assigned share | 0.583 | 0.475 | measured, median |
| assigned share | not recorded | 0.4844 | measured, pooled (481,658 / 994,341) |
| tightness | 0.632 | 0.611 | measured, median |
| candidates | 115 | 44 | measured |
| near-duplicate pairs | 28 | 0 | measured |
| month-to-month cosine | 0.843 | 0.982 | measured, median |
| topics outliving a window | 6.9% | 44.5% | measured |

- **Confirmation:** no tested threshold (0.45, 0.40, 0.35) met both criteria.
  Measured.
- **Thresholds between the tested values:** linear interpolation suggests none
  would meet both. This is an estimate, not a measurement.
- **Coverage samples:** in one reader's judgement, the samples do not separate the
  modes.

The window run's pooled share can be computed from its saved `windows.parquet`:

```
python -c "import pandas as pd; w=pd.read_parquet('runs/cmp-window/windows.parquet'); print(1-w.n_clustered.sum()/w.n_passages.sum())"
```

`themegraph compare` now prints it for old runs too.

## Known limitations

- Labels were blank when these runs were made, so no theme was evaluable in them.
  The labels were approved afterwards (PR #25; labels sha256 now b5c4a9992…).
- Their manifests have no `effective_universe`, so `compare` refuses them. This is
  by design: a missing record is not a pass.
- The outcome-return fix (venue closures) changes Q2 returns only for baskets that
  hit a quote-free stretch of a foreign venue. Q2 plays no part in choosing the
  topic mode.
- Availability is reconstructed from publication times plus lags. It cannot undo
  survivorship or revisions in present-day data.

## What must be regenerated, and why

| item | needs regenerating? | reason |
|---|---|---|
| text topics, candidates, diagnostics | no change expected | no fix touches the text or market layers |
| Q1 detection, adjudication packets | yes | labels changed from blank to approved |
| `compare` | yes | `effective_universe` is now required |
| Q2 outcome returns | yes (if read) | venue-closure rule |

All of this comes from rerunning both modes once. That is the only regeneration
needed.

- **Cost:** about 25 minutes (text stage 246 s window, 1,128 s anchored), using the
  cached embeddings. No paid embedding calls are expected if the cache is complete.
- **Why not an evaluation-only rerun:** it would join new evaluation code to text
  output made by older code under one manifest, which is the retrofit this record
  avoids.
- **Check:** the reruns should reproduce the ledger hashes above (c8ab5efd3156…
  window, b5b3bcc92ba4… anchored), because the fixes do not touch candidate
  generation. A different hash means something else changed. Stop and report it
  before adjudicating.

## Commands (development data only; never `--period holdout`)

```
git pull                       # clean checkout at the merged commit
git status                     # must be clean
themegraph labels              # expect: no problems
themegraph run --period dev --out runs/cmp2-window
themegraph run --period dev --topic-mode anchored --out runs/cmp2-anchored
themegraph adjudicate runs/cmp2-window     # writes adjudication_packet.md + worksheet
themegraph adjudicate runs/cmp2-anchored
# adjudicate from the packets (text only) BEFORE opening either report.html
themegraph compare runs/cmp2-window runs/cmp2-anchored
```

## Human decisions outstanding

1. **Adjudication of both reruns.** Every worksheet entry stays `pending` until a
   person records a decision. The entries are: matched themes, negative-control
   matches, and unmatched candidates (noise / duplicate / real but unlabelled).
2. **The topic-mode decision**, by the rule declared in DESIGN.md ("Window vs
   anchored comparison"), recorded there together with the `compare` output.
3. **Labels:** none outstanding. All 17 entries were approved on 2026-10-25,
   including the eight `review_needed` calls, accepted as drafted. Markers that rest
   on press sources only depend on that approval, not on SEC verification. They can
   still be edited before the freeze, but not after seeing adjudication outcomes
   that would motivate the edit.
4. **Then:** `themegraph freeze`, then the single holdout run.
