# Economic-impact extraction pilot: protocol

Version 1 · 2026-11-26 · status: **approved by the owner on 2026-11-26 and frozen; not yet run**. No model answer
exists. From here any change to the sample rules, labels, metrics or thresholds needs a new version. Code:
`src/themegraph/experimental/impact_pilot.py` (wired to no command; calls no model).

## 1. Question

Does a model that reads a passage in context say whether the speaking company gains or loses more accurately than the
phrase lists in `monitor/tone.py`, by enough to justify replacing them?

- **An answer is** a comparison of both methods with two readers' adjudicated labels on a fixed sample, judged by the
  criteria in section 7, all declared here.
- **Returns are never used.** No price, return, basket or forward-test result is an input, a label, a tie-break or a
  reason to prefer either method. The pilot measures reading accuracy only.
- The pilot changes nothing in the monitor. A pass is a reason to propose a change, not a change.

## 2. What is extracted

One answer per passage: `insufficient` (true when the passage is too fragmentary or ambiguous) and a list of 0 to 3
impacts. Each impact:

| Field | Values | In the pilot |
|---|---|---|
| driver | short phrase, at most 12 words | recorded |
| affected party type | speaker · named covered company · named other company · unnamed group. The model answers "named company" and the name; code splits covered from other with the coverage list (`stored_party_type`), so the model is never asked what is covered | scored through the labels below |
| affected party name | as written in the passage; empty for the speaker | recorded |
| effect on that party | benefits · harmed · neutral · unclear | **scored** |
| channel | demand · supply_capacity · pricing · cost · margin · other | recorded |
| direction of conditions | improving · deteriorating · unchanged · unclear | recorded |
| statement type | observed result · guidance or forecast · risk factor · hypothetical | **scored** |
| evidence span | one continuous stretch copied from the passage (at most 400 characters) | checked, section 7(v) |

**Scored, per passage** (rules in `speaker_effect`, `opposite_party`, `statement_type`):

1. **Effect on the speaker**: from impacts on the speaker stated as fact or forecast. Only benefits → benefits; only
   harmed → harmed; both → unclear; none (or only risk and hypothetical statements) → neutral; `insufficient` →
   unclear. This is the pass/fail field.
2. **Opposite party**: yes when the speaker benefits and another party is harmed, or the reverse.
3. **Statement type**: the strongest type about the speaker (observed, then guidance, risk, hypothetical), or none.

Driver, channel, direction and party name are stored but not scored: too fine to label consistently at this size.

## 3. Sample

300 passages from the 12 months ending at the latest registry month-end, drawn by `draw_sample(seed=20261126)` from
passages of published themes. Input: one row per passage and theme (company, sector, evidence role, text), exported
once from the run's assignments, because the registry stores counts, not member IDs.

- **Strata.** 100 passages each from themes whose current tone is headwind, tailwind, mixed.
- **Within each stratum:** 24 hard cases (section 4), then 76 general passages: 26 management remarks, 25 management
  answers, 25 filings, whatever their tags. No investor-relations documents; analyst questions only via section 4.
- **Spread.** Every quota is filled by taking sectors in turn; at most 3 passages per company and theme, none twice.
- **Order** depends only on the seed and the IDs (a hash), not on file or row order.
- **Shortfall.** If any quota cannot be filled the draw stops and lists every shortfall; it never shrinks the sample
  or borrows from another cell. Quotas then change only in a new protocol version, before any model run.
- The list of passage IDs with stratum, role, quota and tags is committed before anyone reads.

## 4. Hard cases

72 of the 300 (24 per stratum) are chosen by fixed text rules, before any model run: `hard_case_tags`. Per stratum: 6
analyst questions, then 3 for each rule below, filled in this order; a passage counts towards the first quota that
takes it. Sentences end at `.`, `!` or `?`.

| Tag | Rule |
|---|---|
| `analyst_question` | Evidence role is analyst question. |
| `negation` | A phrase from either tone list with *no, not, never, without, neither, nor, cannot* or a word ending *n't* among the 5 words before it in the sentence; or *no longer* followed within 3 words by *constrain…, limit…, restrict…, short…, tight…* or *a headwind / tailwind / bottleneck / problem / concern / issue*. |
| `comparative` | A comparative (*less, more, better, worse, weaker, stronger, softer, slower, faster, lower, higher, smaller, larger, milder, fewer*), then *than* within 4 words, then within 4 words *last, prior, previous, preceding, earlier, ago, expected, anticipated, feared, planned, forecast, guided, quarter, year, half*; or *not as … as*; or a headwind phrase with *ease, moderate, abate, lessen, subside, bottomed, stabilise* (any form) within 4 words before or 6 after. |
| `cross_party` | One sentence with a supply or price term (*shortage, tight supply, supply constraint(s), constrained supply, allocation, price increase(s) / hike(s), pricing power / increases / actions, raise … prices, higher prices, sold out, lead time(s)*) and a counterparty (*customer, supplier, vendor, foundry, distributor, OEM, hyperscaler, competitor, partner*). |
| `hypothetical` | One sentence with *if, unless, assuming, in the event that / of* and *would, could, might, were to*. |
| `risk_boilerplate` | A filing with *could / may / might* followed within 3 words by *adversely, materially, negatively, harm*; or *material(ly) adverse*; or *no assurance*. |
| `mixed` | At least one headwind phrase and at least one tailwind phrase. |

The rules pick candidates; readers decide what a passage says. Tags are recorded for all 300. Results are reported for
the whole sample **and** separately for the 72 quota passages, by tag. Hard cases are over-represented on purpose, so
whole-sample figures describe this sample, not the registry.

## 5. Adjudication

- **Readers.** Two people label all 300 independently, seeing the passage, company, evidence role and date, and not
  the theme, its tone, the tags, the phrase-list result or any model answer. No model answer exists until both label
  files are committed.
- **Labels.** Effect on the speaker: benefits / harmed / neutral / unclear. Opposite party: yes / no. Statement type:
  observed / guidance / risk / hypothetical / none.
- **Guidelines.** (a) Label what the passage says, not what you know. (b) Effect is the state described, not the
  change: "less bad" is still harmed; "no longer a problem" is not harmed. (c) Risk-factor language and conditionals
  state no effect: neutral, with type risk or hypothetical. A risk factor that reports something that has happened is
  observed. (d) Good and bad together: label the net effect if the passage states one, otherwise unclear. (e) An
  analyst's premise is not a company statement: neutral and none, unless the passage also holds the company's own
  words. (f) Harm or benefit to someone else is not an effect on the speaker. (g) Unclear is for passages that cannot
  be read, not for hard ones.
- **Disagreements.** Agreement is computed on the independent labels first. The readers then settle each disagreement
  with a one-line reason; failing that, the owner decides. Guidelines are not edited once labelling starts.
- **Agreement.** Cohen's kappa on the effect on the speaker (four labels, all 300); also given for the other fields
  and the hard cases. **Floor 0.60**: below it the labels cannot judge anything, no verdict is reported, and a new
  version must fix the guidelines.

### Worked examples (synthetic)

Written for this protocol: not quotations, and no company or event in them is real. Each is a test in
`tests/test_impact_pilot.py`. Labels: speaker effect · opposite party · statement type. "Lists": the phrase lists.

- **S1, negation.** "Supply of advanced substrates is no longer constrained, so we shipped everything customers
  ordered this quarter, and we are not seeing any weakness in industrial orders." Benefits · no · observed. Lists:
  harmed ("weakness").
- **S2, comparative.** "The inventory correction at our distributors was less severe than last quarter, and the
  decline in orders is easing." Harmed (direction improving) · no · observed. Lists: harmed, correctly.
- **S3, shortage helps the speaker.** "The shortage of high-capacity memory let us raise contract prices again this
  quarter; several customers told us they have delayed server builds because of the shortfall in supply." Benefits ·
  yes (customers harmed) · observed. Lists: harmed.
- **S4, price rises hurt the speaker.** "Our foundry suppliers pushed through another round of price increases, which
  our own customers would not accept, so gross margin fell two points." Harmed · yes (suppliers benefit) · observed.
  Lists: neutral.
- **S5, risk boilerplate (filing).** "Export controls, a downturn in the semiconductor industry or the loss of a major
  customer could materially and adversely affect our business, results of operations and financial condition." Neutral
  · no · risk. Lists: harmed.
- **S6, hypothetical.** "If tariffs were imposed on imported networking equipment, we would expect to pass most of the
  cost on to customers, although some orders might be delayed." Neutral · no · hypothetical. Lists: harmed.
- **S7, mixed with a stated net.** "Data-centre demand accelerated and we had a record quarter in optical components,
  but consumer remained challenging and we took a write-down on handset inventory; in total, revenue and margin were
  both above our guidance." Benefits · no · observed. Lists: neutral (two phrases each way). Without the last clause:
  unclear.
- **S8, analyst question.** "Given the pricing pressure everyone is talking about in legacy nodes and the weakness in
  China, how should we think about utilisation in the second half?" Neutral · no · none. Lists: left out by the
  monitor, so neutral (raw phrase score: harmed).
- **S9, negated good news.** "We do not expect the strong demand we saw in the first half to continue, and we are
  guiding revenue lower by about ten percent for the fourth quarter." Harmed · no · guidance. Lists: benefits ("strong
  demand").
- **S10, someone else's problem (no tag).** "Competitors such as Norvale Devices have announced restructuring and
  layoffs; our own bookings and headcount were unchanged in the quarter." Neutral · no · observed. Lists: harmed.

## 6. Baseline

`baseline_label` applies `tone.score` to the passage, unchanged: more headwind than tailwind phrases → harmed; the
reverse → benefits; a tie or no phrase → neutral.

- Analyst questions are **neutral**, because the monitor leaves them out of tone; their raw phrase score is a
  sensitivity check only.
- Genre changes nothing else per passage: tone uses it only to compare a whole theme with its genre's usual rate.
- The lists cannot answer "unclear", cannot name another party and cannot give a statement type. On opposite party and
  statement type they **score zero by construction**. Those two fields are reported for extraction alone and are
  **not** part of the pass/fail comparison.

## 7. Metrics and acceptance criteria

Primary metric: **macro-F1 on the effect on the speaker** over benefits, harmed and neutral, on passages whose
adjudicated label is one of those three. Adjudicated "unclear" passages are counted and reported but not scored, since
the lists cannot give that answer. An "unclear" or invalid extraction answer on a scored passage is wrong.

Extraction passes only if **all** hold (`score` computes them; seed 20261126):

| | Criterion | Why this number |
|---|---|---|
| i | Reader kappa ≥ **0.60** | The usual lower edge of "substantial" agreement; below it the reference is noisier than the difference sought. |
| ii | Macro-F1 gain over the lists ≥ **0.10**, and the 95% interval of the gain excludes zero (2,000 bootstrap resamples of whole companies) | Smaller than this would not repay a paid, slower, harder-to-audit method; it is also about the smallest gain 300 passages can show. |
| iii | Precision on harmed ≥ **0.80** and on benefits ≥ **0.80** | These labels would feed the Headwinds list; more than one wrong in five is too many for a list people act on. |
| iv | On the 72 hard cases, macro-F1 not below the lists' | Hard cases are the reason for the method; losing there defeats it. A point estimate only (see power). |
| v | Answers failing validation ≤ **2%** (6 of 300); each failure is a wrong answer | A span that is not in the passage is an invented quotation; a few format slips are tolerated, a pattern is not. |
| vi | Extraction "unclear" (including `insufficient`) ≤ **15%** of the 300 | A theme needs 20 readable passages; abstaining on more than about one in seven would leave many themes unjudged. |

Also reported, not criteria: accuracy and per-class precision, recall and F1 for both methods; four-label macro-F1;
opposite-party precision and recall; statement-type accuracy; results by stratum, role and tag.

**Power, stated before the data.** With about 300 scored passages and the two methods disagreeing on roughly a third,
the gain's standard error is about 0.035 to 0.045 once company clustering is allowed for. A true gain under about 0.08
will probably not be told from zero. Criterion ii needs the estimate itself to reach 0.10, so a true gain of exactly
0.10 passes only about half the time; passing four times in five needs a true gain near 0.13 to 0.15. So the pilot can
confirm a large improvement, not a modest one. On the 72 hard cases the interval is about ±0.15 to 0.20: criterion iv
guards against a clear loss and proves nothing about a gain. These are planning approximations.

**On failure** the phrase lists stay, and the result is recorded here as a negative finding, with the same tables as a
pass. **Nothing here changes after results are seen**: sample, guidelines, label reduction rules, metrics, numbers and
seeds are fixed by version 1. Any change needs a new version with a new sample and labels; the earlier result stays.

## 8. Reproducibility and safeguards

- **One model, one prompt.** A single model ID, fixed before the run. No fallback model: an answer served by any other
  model is discarded and counted as invalid.
- **Cache.** Key: SHA-256 of passage text, prompt version, schema version and model ID (`cache_key`). One line per
  answer in `<state>/experimental/impact_pilot/answers.jsonl` (outside git): key, passage ID, model requested and
  served, prompt and schema version, time, token counts, raw answer, validation problems. A rerun calls nothing.
- **Committed** under `docs/experiments/impact_pilot/`: sample IDs with strata and tags, both readers' labels,
  adjudicated labels with reasons, both methods' predictions and the result; not passage text (source licences).
- **Scoring** is deterministic: `score` has fixed seeds and no network or clock.
- **Passage text is untrusted data, never instructions.** The prompt puts all instructions before the passage,
  encloses the passage in `<passage>` tags and says that anything inside which reads like an instruction is text to
  analyse. A passage containing `</passage>` is not sent (counted invalid). The model has no tools, and its output is
  limited by the schema to fixed fields, so the worst a hostile passage can do is a wrong label, which is scored.
  Answers are never executed.

## 9. Cost (token estimates only)

Per call: prompt about 600 tokens (396 words), schema about 250, passage up to about 500 (350 words): **about 1,400
input tokens**. Output: 100 to 300 visible tokens; the project's default model always reasons first and that is billed
as output, so **plan on about 600 output tokens** (range 200 to 1,000). No saving from prompt caching is assumed.

| Run | Calls | Input tokens | Output tokens |
|---|---|---|---|
| Pilot (300 passages, up to 5% retried) | about 315 | about 0.45 million | about 0.19 million (0.06 to 0.32) |
| First run over active themes | about 3,500 | about 4.9 million | about 2.1 million |
| Each later month (new passages only) | about 600 | about 0.85 million | about 0.36 million |

The last two rows rest on assumptions, to be replaced by registry counts: 44 active themes (as on 2026-11-21), about
80 company passages each in the 180-day window, about a sixth of the window new each month. Tokens are measured on the
first 20 calls.

**Prices used (list prices as read on 2026-11-26; re-check on the day of the run).**
Claude Opus 5.5, the model the monitor already uses: 4 US dollars per million input tokens and 20
per million output tokens.

| Run | Input cost | Output cost | Total |
|---|---|---|---|
| Pilot (about 315 calls) | about $1.80 | about $3.80 (range $1.20 to $6.40) | about $5.60 (range $3 to $8) |
| First run over active themes | about $20 | about $42 | about $62 |
| Each later month | about $3.40 | about $7.20 | about $11 |

The last two rows rest on the unverified passage counts above and are not approved by this
protocol: only the pilot is. The pilot is not run until the owner approves this cost.

## 10. Approvals and what remains

Recorded from the owner's reply of 2026-11-26:

- [x] This protocol, version 1, including its design choices: 72 hard cases, risk factors and
      hypotheticals labelled neutral, passages adjudicated unclear left out of the primary score,
      analyst questions scored neutral for the phrase lists.
- [x] Model: Claude Opus 5.5 (`claude-opus-5-5`), low effort as the monitor uses, with no
      fallback model, so every answer comes from one model. A refusal is recorded as an invalid
      answer.
- [x] Cost: the pilot only, about $5.60 (range $3 to $8) at the prices in section 9.
- [x] A one-off export from the owner's machine linking stored passages to themes.
- [x] The owner states the data licence covers distribution; sending passage text to the model
      provider is on the same footing as the titles and relationship readings already run.
- [ ] The two readers named (the owner agreed to supply them; not yet named here).
- [ ] Sample drawn without shortfall; ID list committed before any model answer.
- [ ] Both label files and the adjudicated labels committed; kappa at or above 0.60.
- [ ] Model answers produced (after the sample is committed; readers stay blind to them).
- [ ] Result recorded here, pass or fail, with no change to the criteria.

The tooling for the export, the sample, the labelling sheets, the model run and the scoring is
`scripts/impact_pilot.py` (see its `--help`). Nothing in it is used by the monitor.
