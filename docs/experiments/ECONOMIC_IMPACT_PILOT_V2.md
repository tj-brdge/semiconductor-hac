# Economic-impact extraction pilot: protocol, version 2

Version 2 · 2026-11-26 · status: **designed; awaiting the owner's approval; not run**. No label and no model
answer exists under either version, so this is a new version before any result, not a change after one.

**Scope.** Version 2 supersedes version 1 (`ECONOMIC_IMPACT_PILOT.md`, frozen, unedited) **only in section 5
(adjudication) and in the checklist of section 10**. Sections 1 to 4 and 6 to 9 of version 1 (question, extraction
schema, sample, hard cases, baseline, metrics and thresholds, bootstrap, caching and safeguards, cost) are
incorporated here by reference and are unchanged; where they say "readers" or "kappa", read the terms in the table
below. Code: `src/themegraph/experimental/impact_crowd.py` (rules) and `impact_crowd_run.py` (files); neither calls a
model or the network.

## What changed from version 1, and why

The owner decided to replace the two readers with a group of finance professionals the owner invites (invite-only,
not the public): more independent judgement per passage, and no label settled by discussion or by the owner.

| # | Version 1 | Version 2 | Why |
|---|---|---|---|
| 1 | Two readers each label all 300 | Invited labellers; every passage goes to at least 3 of them | The owner's decision |
| 2 | Speaker effect: benefits / harmed / neutral / unclear | The same four; "unclear" is shown as "cannot tell" and stored as `unclear` | Plainer wording for people who have not read the protocol |
| 3 | Opposite party: yes / no | yes / no / cannot tell | A labeller must not be forced to guess; a "cannot tell" majority is reported and left out of that field's figures |
| 4 | Statement type: observed / guidance / risk / hypothetical / none | Unchanged (five answers) | "None" is needed for analyst questions (version 1, example S8) |
| 5 | Guidelines (a) to (g) | The same seven, reworded only for the new answer names, plus two that restate version 1's section 2 rules for the other two questions | Readers of version 1 had the protocol; labellers have only the page |
| 6 | Disagreements settled by the readers, else the owner | Strict majority of valid labellers; otherwise the passage is **unresolved** for that field. Nobody decides a label | Removes discussion and the owner's judgement from the reference |
| 7 | Agreement: Cohen's kappa, floor 0.60 | Krippendorff's alpha (nominal), floor 0.60 | Kappa needs exactly two readers who label everything |
| 8 | Every passage has a reference label | Not scored at all unless at least 80% of passages (240 of 300), and at least 70 of the 100 in each stratum, are resolved for the speaker effect | A reference with large holes would describe only easy passages |
| 9 | Primary score on passages adjudicated benefits, harmed or neutral | The same, among resolved passages. Unresolved passages are left out, counted and reported by field, stratum and for the hard cases | Follows from 6 |
| 10 | Worked examples S1 to S10 illustrate the protocol | They are also hidden attention checks, 4 per labeller, with an exclusion rule | Invited labellers are unsupervised |
| 11 | Readers see passage, company, evidence role, date | Labellers see passage, company, source type (the evidence role in plain words), date | Same information |
| 12 | Committed: both readers' labels, adjudicated labels with reasons | Committed: assignment, labels by code, majority labels, report. No names, no comments | Follows from 1 and privacy |
| 13 | No model answer until both label files are committed | No model answer until the majority labels are committed | Same safeguard |
| 14 | Checklist of section 10 | Replaced below | |

Not changed, in particular: the 300-passage sample and its seed, the hard-case rules, the extraction prompt and
schema, the baseline mapping, the reduction rules, criteria ii to vi and every threshold, the 2,000 company-level
bootstrap resamples, the cache, the model and the approved cost. Criteria v and vi need no reference label and are
still taken over all 300 passages.

## 5. Adjudication by invited labellers (replaces version 1, section 5)

### 5.1 Labellers

- Finance professionals invited personally by the owner. Not open to the public; no payment platform.
- Each labeller gets a **personal code** (four consonants and four digits, such as KQZT4827, made at random by
  `crowd-codes`). A name, initials or an email address is refused as a code.
- One optional background question, self-declared: role (buy side, sell side, banking or advisory, corporate finance
  or investor relations, academic or other, prefer not to say) and years in finance (under 3, 3 to 10, over 10,
  prefer not to say). **Recorded, reported as totals, never used to filter or weight.**

### 5.2 The three questions

Asked for every passage, on one screen, matching version 1's three scored fields:

1. **Effect on the company speaking:** benefits / harmed / neutral / cannot tell.
2. **Is another party affected in the opposite direction?** yes / no / cannot tell.
3. **Statement type:** observed result / guidance or forecast / risk factor / hypothetical / none.

Plus an optional free-text comment, which is never scored and never committed.

### 5.3 Guidelines

Shown in full on the labelling page, and condensed on its welcome screen. Not edited once the first labeller has
been invited.

1. Label what the passage says, not what you know about the company or what happened later.
2. Effect is the state described, not the change: "less bad" is still harmed; "no longer a problem" is not harmed.
3. Risk-factor language and conditionals state no effect: neutral, with statement type risk factor or hypothetical. A risk factor that reports something that has happened is an observed result.
4. Good and bad together: label the net effect if the passage states one, otherwise cannot tell.
5. An analyst's premise is not a company statement: neutral and statement type none, unless the passage also holds the company's own words.
6. Harm or benefit to someone else is not an effect on the company speaking.
7. Cannot tell is for passages that cannot be read, not for hard ones.
8. Another party is affected in the opposite direction only when the company speaking benefits and a customer, supplier, competitor or other party is harmed, or the reverse. If the effect on the company is neutral, the answer is no.
9. Statement type: when the passage holds several kinds of statement about the company, choose the first that applies in this order: observed result, guidance or forecast, risk factor, hypothetical. Choose none when the company makes no statement about itself.

### 5.4 Blindness

A labeller sees, for each passage, **the passage text, the company name, the source type and the date, and nothing
else**: never the theme, its tone, the stratum, the hard-case tags, the phrase lists' label, a model answer or another
labeller's answers or assignment. Each labeller's data file holds only their own passages under handles that differ
between labellers. `crowd-pack` checks the folder it wrote and refuses to finish if anything else is in it. No model
answer exists until the majority labels are committed (5.9).

### 5.5 Assignment and workload

- **Three labellers per passage** in the first round (the target); never more than **5** on one passage.
  `--per-passage 5` gives five from the start; the owner chooses before the first invitation, not after.
- **Deterministic and balanced** (`assign`, seed 20261126): passages are taken in an order fixed by the seed and
  their IDs; each goes to the labellers with the fewest passages so far, ties broken by a hash of seed, code and
  passage. Workloads differ by at most one. Each labeller's order is their own shuffle.
- **Cap: 45 sample passages per labeller**, plus 4 attention checks: 49 items. *Assumption:* about 45 seconds an
  item, so about 37 minutes; the page says 30 to 40. This is a planning figure, not measured.
- **So 300 passages need 900 labels and at least 20 labellers**, with no slack at all: a labeller who does not
  return or is excluded leaves up to 45 passages one label short. Inviting about 24 to 26 for the first round, or
  34 or more with five per passage (1,500 labels), is the practical reading.
- **Top-up.** Passages still unresolved for the speaker effect may be given to labellers invited later, and only
  those passages: one more label for a tie, or as many as bring the passage to 3 valid labels, up to 5 labellers
  who are not excluded. A passage with no room left stays unresolved. Collection closes when the majority labels
  are committed.

### 5.6 Attention checks

- The ten synthetic worked examples of version 1 (S1 to S10: invented, no real company or event, reference answers
  stated there) are mixed into every labeller's set: **4 each**, one whose reference speaker effect is benefits, one
  harmed, one neutral and one more, chosen by a hash of the seed and the code, shown like any other passage under an
  invented company name. Labellers are told that a few passages are quality checks, not which.
- **Rule, fixed now:** a labeller with **more than 1 of the 4 wrong on the effect on the company** is **excluded
  entirely**: none of their labels counts for a majority, for agreement or for anything else. "Cannot tell" on a
  check is wrong. A labeller who has not yet answered enough checks to have 3 right is *incomplete*: not used until
  they finish. The other two questions on a check are not part of the rule.
- The reference answers are in the code and in version 1, never in the labelling folder. Check answers are not part
  of the sample and are never scored.

### 5.7 Majority label, and "unresolved"

- **Valid labels** on a passage: those of labellers who are not excluded and not incomplete.
- **Per field, the label is the one given by more than half of the valid labellers** (2 of 3, 3 of 4, 3 of 5).
  With **fewer than 3 valid labels, or no such majority, the passage is unresolved for that field**: left out of that
  field's figures, counted, and reported by stratum and for the hard cases. No person settles it.
- **The pilot is not scored** unless at least **80% of all passages (240 of 300)** and at least **70 of the 100 in
  each stratum** are resolved for the effect on the company. Below that, `crowd-collect` writes no majority labels and
  lists the passages and how many more labels each needs.
- A majority of "cannot tell" is a resolved label. As with version 1's "unclear", such passages are **excluded from
  the primary macro-F1**, counted and reported.
- **Honest limit.** Unresolved passages are likely to be the hardest ones, so scores on the resolved passages may
  flatter both methods, and criterion iv rests on fewer hard cases. With as few as 240 scored passages the standard
  error in version 1's power note grows by about a tenth. The thresholds are not moved to compensate.

### 5.8 Agreement

**Krippendorff's alpha for nominal labels**, per field, on the valid labels of all sampled passages (attention checks
excluded), before any majority is taken; also for the hard cases. Alpha is 1 minus observed over expected
disagreement and allows a different number of labellers on each passage. **Criterion i becomes: alpha on the effect
on the company at or above 0.60.** Below it, as in version 1, no verdict is reported and a new version must fix the
guidelines.

How this relates to version 1's kappa, plainly: both are chance-corrected agreement on the same scale. With two
readers who label everything, alpha is Scott's pi with a small-sample correction, and differs from Cohen's kappa
only in how chance agreement is estimated: alpha pools all labels, kappa uses each reader's own label frequencies, so
kappa is equal or slightly higher when the readers' habits differ. With 300 passages the difference is usually in
the second decimal. The floor therefore **means much the same but not exactly the same**: 0.60 on alpha is, if
anything, slightly stricter. It also describes the reliability of a single labeller's label, not of the majority
label, which is more reliable than any one of them.

### 5.9 Order of events

Sample committed (version 1) → codes made → labelling folder written and checked → labellers invited → returned
files collected (repeat, with top-ups if needed) → **assignment, labels by code, majority labels and report
committed** → only then the model run → `crowd-score` → result recorded.

### 5.10 Privacy

- **Committed files hold labeller codes only.** Who has which code is known to the owner alone and is never
  committed, nor written by any tool here.
- **Free-text comments are never committed.** They stay in the returned files on the owner's machine; the report
  gives only how many each labeller wrote. (Simpler and safer than judging which comment could identify someone.)
- Background answers are committed only as totals in the report.
- The labelling page makes no network request except loading the labeller's own data file, and has no analytics.
  Answers leave the labeller's browser only in the file they send back themselves.
- The labelling folder holds passage text: it is hosted unlisted (or sent directly) for the invited labellers, is
  not committed, and is removed from the host when collection closes. A data file can be read by anyone who has its
  code, so a code is as private as the invitation.

## 10. What the owner must approve (replaces version 1's checklist)

Carried over from version 1 and still standing: the model, the cost of the pilot, the export, and sending passage
text to the model provider.

- [ ] Version 2 as a whole: invited labellers instead of two readers; majority labels with no human tie-break.
- [ ] The numbers: 3 labellers per passage (at most 5); 45 passages a labeller plus 4 checks; exclusion at more
      than 1 of 4 checks wrong; resolved for at least 240 of 300 and 70 of 100 per stratum; alpha floor 0.60.
- [ ] The three questions as worded, including "cannot tell" for the opposite party and "none" for statement type,
      and the nine guidelines.
- [ ] Using version 1's worked examples as hidden checks, and telling labellers that checks exist.
- [ ] The invented company names on the checks (in `impact_crowd.CHECKS`) belong to no real company.
- [ ] The data licence covers showing passage text to the invited labellers on an unlisted page.
- [ ] How the folder is hosted or sent, and that the code-to-person list stays with the owner.
- [ ] Three or five labellers per passage in the first round, decided before the first invitation.
- [ ] At least 20 labellers invited (24 to 26 advised); codes made; none is a name.
- [ ] Sample drawn without shortfall; ID list committed (as version 1).
- [ ] Assignment, labels by code, majority labels and report committed; resolved-share rule met; alpha at or above
      0.60.
- [ ] Model answers produced only after that commit.
- [ ] Result recorded here, pass or fail, with no change to the criteria.

Tooling: `scripts/impact_pilot.py crowd-codes`, `crowd-pack`, `crowd-collect`, `crowd-score` (see the runbook).
`score` remains version 1's and is not used under version 2; the two reader sheets `sample` writes are unused.
