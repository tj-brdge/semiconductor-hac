"""Economic-impact pilot, protocol version 2: labelling by invited finance professionals.

Protocol: docs/experiments/ECONOMIC_IMPACT_PILOT_V2.md (version 2, 2026-11-26). It
replaces only version 1's adjudication (two readers, Cohen's kappa) with invited
labellers, a majority label and Krippendorff's alpha. The sample, the extraction
schema, the baseline, the metrics and the thresholds are version 1's
(`impact_pilot`), used here unchanged. The protocol is the authority; the constants
below repeat its numbers and a test keeps them in step with it.

EXPERIMENTAL and pure: no file, network, clock or model is used here. The file
handling is in `impact_crowd_run`.

Parts:
- `code_problems`, `new_codes`   opaque labeller codes (never names or emails);
- `assign`, `choose_checks`, `labeller_items`   the deterministic balanced
                                 assignment with attention checks, and the blind items;
- `read_returned`                validation of one returned label file (all or nothing);
- `labeller_status`              the attention-check rule;
- `adjudicate`, `labels_needed`, `resolved_rule`   majority labels and what is missing;
- `krippendorff_alpha`           nominal alpha for unequal numbers of labellers;
- `score`                        version 1's comparison on the resolved passages.
"""

from __future__ import annotations

import math
import re
import secrets
from collections import Counter
from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd

from . import impact_pilot as P

PROTOCOL_VERSION = "2"

# -- declared numbers (protocol version 2, section 5) ----------------------------------

LABELS_PER_PASSAGE = 3        # target: labellers assigned to every passage
MAX_LABELS_PER_PASSAGE = 5    # never more labellers than this on one passage
MIN_VALID_LABELS = 3          # fewer valid labels than this: unresolved
WORKLOAD_CAP = 45             # sample passages per labeller, attention checks not counted
N_CHECKS = 4                  # attention checks mixed into every labeller's set
MAX_CHECKS_WRONG = 1          # more wrong than this on the speaker effect: excluded
SECONDS_PER_ITEM = 45         # planning assumption for the 30 to 40 minutes
MIN_RESOLVED_SHARE = 0.80     # of all passages, resolved for the speaker effect
MIN_STRATUM_RESOLVED_SHARE = 0.70   # of each stratum's passages
ALPHA_FLOOR = P.KAPPA_FLOOR   # the same 0.60 as version 1's kappa floor

# -- labels ---------------------------------------------------------------------------

CANNOT_TELL = "cannot_tell"
UNRESOLVED = "unresolved"
FIELDS = {"speaker_effect": ("benefits", "harmed", "neutral", CANNOT_TELL),
          "opposite_party": ("yes", "no", CANNOT_TELL),
          "statement_type": (*P.STATEMENT_TYPES, "none")}
PRIMARY = "speaker_effect"
TO_V1 = {CANNOT_TELL: "unclear"}      # version 1's word for the same answer
BACKGROUND = {
    "role": ("buy_side", "sell_side", "banking_or_advisory", "corporate_finance_or_ir",
             "academic_or_other", "prefer_not_to_say"),
    "years": ("under_3", "3_to_10", "over_10", "prefer_not_to_say")}
MAX_COMMENT_CHARS = 1000

# What a labeller is shown, and nothing else (the item `id` is an opaque handle).
BLIND_FIELDS = ("id", "company", "source", "date", "text")
SOURCES = {"management_remarks": "Earnings call: prepared remarks by management",
           "management_answer": "Earnings call: management's answer to a question",
           "analyst_question": "Earnings call: an analyst's question",
           "filing": "Regulatory filing",
           "ir_document": "Investor-relations document"}

# Labeller codes: four consonants then four digits (no vowels, so no names or words;
# no 0 or 1, which are misread). 20^4 * 8^4 = 655 million possible codes.
_LETTERS, _DIGITS = "BCDFGHJKLMNPQRSTVWXZ", "23456789"
CODE = re.compile(rf"[{_LETTERS}]{{4}}[2-9]{{4}}")     # the page holds the same pattern

# -- attention checks: version 1's worked examples (synthetic; no real company) ---------
# id -> text, evidence role, an invented company name, and the three reference answers
# as the version 1 protocol states them. The answers stay here: they are never written
# to the labelling folder.

CHECKS = {
    "S1": ("Supply of advanced substrates is no longer constrained, so we shipped everything "
           "customers ordered this quarter, and we are not seeing any weakness in industrial "
           "orders.", "management_remarks", "Tessamar Substrates",
           "benefits", "no", "observed"),
    "S2": ("The inventory correction at our distributors was less severe than last quarter, "
           "and the decline in orders is easing.", "management_remarks", "Quillon Analog",
           "harmed", "no", "observed"),
    "S3": ("The shortage of high-capacity memory let us raise contract prices again this "
           "quarter; several customers told us they have delayed server builds because of the "
           "shortfall in supply.", "management_answer", "Verrick Memory",
           "benefits", "yes", "observed"),
    "S4": ("Our foundry suppliers pushed through another round of price increases, which our "
           "own customers would not accept, so gross margin fell two points.",
           "management_answer", "Dunmarrow Fabless", "harmed", "yes", "observed"),
    "S5": ("Export controls, a downturn in the semiconductor industry or the loss of a major "
           "customer could materially and adversely affect our business, results of operations "
           "and financial condition.", "filing", "Aldwynne Semiconductor",
           "neutral", "no", "risk"),
    "S6": ("If tariffs were imposed on imported networking equipment, we would expect to pass "
           "most of the cost on to customers, although some orders might be delayed.",
           "management_answer", "Kestrin Networks", "neutral", "no", "hypothetical"),
    "S7": ("Data-centre demand accelerated and we had a record quarter in optical components, "
           "but consumer remained challenging and we took a write-down on handset inventory; in "
           "total, revenue and margin were both above our guidance.", "management_remarks",
           "Orlath Optical", "benefits", "no", "observed"),
    "S8": ("Given the pricing pressure everyone is talking about in legacy nodes and the "
           "weakness in China, how should we think about utilisation in the second half?",
           "analyst_question", "Brennick Foundry Services", "neutral", "no", "none"),
    "S9": ("We do not expect the strong demand we saw in the first half to continue, and we are "
           "guiding revenue lower by about ten percent for the fourth quarter.",
           "management_remarks", "Halvane Microsystems", "harmed", "no", "guidance"),
    "S10": ("Competitors such as Norvale Devices have announced restructuring and layoffs; our "
            "own bookings and headcount were unchanged in the quarter.", "management_remarks",
            "Corwenna Signal", "neutral", "no", "observed"),
}
CHECK_KEYS = ("text", "role", "company", *FIELDS)


def check(key: str) -> dict:
    return dict(zip(CHECK_KEYS, CHECKS[key], strict=True))


# -- labeller codes --------------------------------------------------------------------

def new_codes(n: int, rng=secrets) -> list[str]:
    """`n` distinct random codes. Random on purpose: a code must not be guessable."""
    out: set[str] = set()
    while len(out) < n:
        out.add("".join(rng.choice(_LETTERS) for _ in range(4))
                + "".join(rng.choice(_DIGITS) for _ in range(4)))
    return sorted(out)


def code_problems(codes: Iterable[str]) -> list[str]:
    """Why a list of codes is refused. A code must be opaque: the committed files
    hold codes, so a name, initials with a birth year or an email must never be one."""
    codes, out = list(codes), []
    for c in codes:
        if not CODE.fullmatch(c):
            what = "looks like an email address" if "@" in c else \
                "looks like a name or a word" if re.search(r"[A-Za-z]{2}", c) else \
                "is not an opaque code"
            out.append(f"'{c}' {what}: a code is four letters from {_LETTERS} then four "
                       f"digits from {_DIGITS}, e.g. KQZT4827 (`crowd-codes` makes them)")
    out += [f"'{c}' appears {k} times" for c, k in Counter(codes).items() if k > 1]
    return out


# -- assignment ------------------------------------------------------------------------

def labellers_needed(n_passages: int, per_passage: int = LABELS_PER_PASSAGE,
                     cap: int = WORKLOAD_CAP) -> int:
    return max(per_passage, math.ceil(n_passages * per_passage / cap))


def assign(passage_ids: Iterable[str], codes: Iterable[str],
           need: int | Mapping[str, int] = LABELS_PER_PASSAGE, *,
           seed: int = P.SAMPLE_SEED, cap: int = WORKLOAD_CAP,
           ) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Which labeller gets which passage: ({code: [passage_id]}, {passage_id: unmet}).

    Passages are taken in an order fixed by the seed and their IDs. Each goes to the
    `need` labellers with the fewest passages so far (ties broken by a hash of the
    seed, the code and the passage), so workloads never differ by more than one and
    no labeller exceeds `cap`. The result depends only on the seed, the set of codes
    and the set of passage IDs, not on their order. `unmet` is empty when every
    passage got what it needed.
    """
    codes = sorted(set(codes))
    pids = sorted(set(passage_ids), key=lambda p: P._h(seed, "crowd-passage", p))
    want = (lambda p: need) if isinstance(need, int) else (lambda p: int(need.get(p, 0)))
    out: dict[str, list[str]] = {c: [] for c in codes}
    unmet: dict[str, int] = {}
    for p in pids:
        free = sorted((c for c in codes if len(out[c]) < cap),
                      key=lambda c, p=p: (len(out[c]), P._h(seed, "crowd-pair", c, p)))
        for c in free[:want(p)]:
            out[c].append(p)
        if want(p) > len(free):
            unmet[p] = want(p) - len(free)
    return out, unmet


def choose_checks(code: str, seed: int = P.SAMPLE_SEED, n: int = N_CHECKS) -> list[str]:
    """The labeller's attention checks: one whose reference speaker effect is
    benefits, one harmed, one neutral, then the rest, each chosen by a hash of the
    seed and the code. A labeller who gives one answer throughout fails at least two."""
    order = sorted(CHECKS, key=lambda k: P._h(seed, "crowd-check", code, k))
    first = [next(k for k in order if CHECKS[k][3] == e) for e in P.SCORED]
    return (first + [k for k in order if k not in first])[:n]


def item_id(seed: int, code: str, ref: str) -> str:
    """The handle a labeller's file uses for one item. It differs between labellers
    and says nothing about the passage or whether the item is a check."""
    return P._h(seed, "crowd-item", code, ref)[:12]


def labeller_items(code: str, passages: list[dict], checks: Iterable[str], *,
                   seed: int = P.SAMPLE_SEED) -> tuple[list[dict], list[dict]]:
    """(items, key) for one labeller, in the labeller's own shuffled order.

    `passages`: dicts with passage_id, company, role, date, text. `items` hold
    exactly `BLIND_FIELDS`. `key` (item id, kind, ref) stays with the owner. A
    check borrows the date of one of the labeller's own passages, so that it is
    not told apart by its date.
    """
    dates = sorted(p["date"] for p in passages) or [""]
    rows = [("passage", p["passage_id"], p) for p in passages]
    for k in checks:
        c = check(k)
        date = dates[int(P._h(seed, "crowd-date", code, k), 16) % len(dates)]
        rows.append(("check", k, {**c, "date": date}))
    rows.sort(key=lambda r: P._h(seed, "crowd-order", code, r[1]))
    items = [{"id": item_id(seed, code, ref), "company": str(p["company"]),
              "source": SOURCES.get(p["role"], "Company document"), "date": str(p["date"]),
              "text": str(p["text"])} for _kind, ref, p in rows]
    key = [{"code": code, "item_id": item_id(seed, code, ref), "kind": kind, "ref": ref}
           for kind, ref, _p in rows]
    return items, key


# -- returned files --------------------------------------------------------------------

def read_returned(obj, key: pd.DataFrame, pack: str) -> tuple[str | None, list[dict], dict]:
    """(code, labels, background) from one returned file, or raise ValueError with
    the reason. A file is used whole or not at all.

    `key`: the owner's assignment (code, item_id, kind, ref). An unanswered item is
    simply absent; an item present must carry all three answers.
    """
    if not isinstance(obj, dict) or not isinstance(obj.get("labels"), list):
        raise ValueError("not a label file: no list of labels")
    if str(obj.get("protocol")) != PROTOCOL_VERSION:
        raise ValueError(f"made for protocol version {obj.get('protocol')!r}, not "
                         f"{PROTOCOL_VERSION}")
    code = obj.get("code")
    if not isinstance(code, str) or not CODE.fullmatch(code):
        raise ValueError("its labeller code is missing or is not a valid code")
    mine = key.loc[key["code"] == code]
    if mine.empty:
        raise ValueError(f"code {code} was not given any passages")
    if obj.get("pack") != pack:
        raise ValueError(f"made from another labelling folder (pack {obj.get('pack')!r}, "
                         f"expected {pack!r})")
    ref = dict(zip(mine["item_id"], zip(mine["kind"], mine["ref"], strict=True), strict=True))
    seen, labels = set(), []
    for i, row in enumerate(obj["labels"]):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError(f"label {i + 1} has no item id")
        if row["id"] not in ref:
            raise ValueError(f"item {row['id']} was not assigned to code {code}")
        if row["id"] in seen:
            raise ValueError(f"item {row['id']} is labelled twice")
        seen.add(row["id"])
        for f, allowed in FIELDS.items():
            if row.get(f) not in allowed:
                raise ValueError(f"item {row['id']}: {f} is {row.get(f)!r}, not one of "
                                 f"{', '.join(allowed)}")
        comment = row.get("comment", "")
        if not isinstance(comment, str) or len(comment) > MAX_COMMENT_CHARS:
            raise ValueError(f"item {row['id']}: the comment is not text of at most "
                             f"{MAX_COMMENT_CHARS} characters")
        kind, r = ref[row["id"]]
        labels.append({"code": code, "kind": kind, "ref": r, "has_comment": bool(comment),
                       **{f: row[f] for f in FIELDS}})
    bg = obj.get("background") or {}
    if not isinstance(bg, dict):
        raise ValueError("the background answers are not readable")
    background = {k: bg.get(k) if bg.get(k) in allowed else "prefer_not_to_say"
                  for k, allowed in BACKGROUND.items()}
    return code, labels, background


def labeller_status(labels: list[dict], checks: Iterable[str], *,
                    max_wrong: int = MAX_CHECKS_WRONG) -> dict:
    """The attention-check rule, on the speaker effect only.

    excluded:   more than `max_wrong` checks answered wrongly (final: every label of
                this labeller is left out of everything);
    incomplete: not excluded, but too few checks answered yet to be sure (labels are
                not used until the labeller finishes);
    valid:      at most `max_wrong` checks can still be wrong.
    """
    checks = list(checks)
    given = {r["ref"]: r[PRIMARY] for r in labels if r["kind"] == "check"}
    right = sum(given.get(k) == check(k)[PRIMARY] for k in checks)
    wrong = sum(k in given and given[k] != check(k)[PRIMARY] for k in checks)
    status = "excluded" if wrong > max_wrong else \
        "valid" if len(checks) - right <= max_wrong else "incomplete"
    return {"status": status, "checks": len(checks), "checks_right": right,
            "checks_wrong": wrong}


# -- majority labels -------------------------------------------------------------------

def majority(values: Iterable[str], min_valid: int = MIN_VALID_LABELS) -> str:
    """The label more than half of the valid labellers gave, else `UNRESOLVED`
    (also when there are fewer than `min_valid` labels)."""
    values = list(values)
    if len(values) < min_valid:
        return UNRESOLVED
    label, k = Counter(values).most_common(1)[0]
    return label if 2 * k > len(values) else UNRESOLVED


def adjudicate(passage_ids: Iterable[str], labels: pd.DataFrame, *,
               min_valid: int = MIN_VALID_LABELS) -> pd.DataFrame:
    """One row per passage: the number of valid labels and, per field, the majority
    label in version 1's words (`cannot_tell` becomes `unclear`) or `unresolved`,
    in the columns the version 1 tooling uses (`adjudicated_<field>`, `reason`).

    `labels`: the valid labellers' labels on sample passages (passage_id, code and
    the three fields).
    """
    by = {p: g for p, g in labels.groupby("passage_id")} if len(labels) else {}
    rows = []
    for p in passage_ids:
        g = by.get(p)
        n = 0 if g is None else len(g)
        row = {"passage_id": p, "n_valid": n}
        for f in FIELDS:
            v = [] if g is None else list(g[f])
            m = majority(v, min_valid)
            row[f"adjudicated_{f}"] = TO_V1.get(m, m)
            row[f"votes_{f}"] = ";".join(f"{k}:{c}" for k, c in sorted(Counter(v).items()))
        top = max(Counter(g[PRIMARY]).values()) if n else 0
        row["reason"] = f"fewer than {min_valid} valid labels ({n})" if n < min_valid else \
            f"majority {top} of {n}" if 2 * top > n else f"no majority among {n}"
        rows.append(row)
    return pd.DataFrame(rows)


def labels_needed(adjudicated: pd.DataFrame, pending: Mapping[str, list[str]],
                  active: Mapping[str, int], *, min_valid: int = MIN_VALID_LABELS,
                  max_labels: int = MAX_LABELS_PER_PASSAGE) -> pd.DataFrame:
    """For every passage not resolved for the speaker effect: what would resolve it.

    `pending[passage]`: codes that were given the passage, are not excluded and have
    not labelled it yet (re-send to them). `active[passage]`: labellers given the
    passage and not excluded. `new_labels`: labels needed from people not yet
    invited, beyond the pending ones, never taking a passage past `max_labels`
    labellers. A passage with nothing pending and no room left is `closed`.
    """
    rows = []
    for r in adjudicated.to_dict("records"):
        if r[f"adjudicated_{PRIMARY}"] != UNRESOLVED:
            continue
        p = r["passage_id"]
        waiting = sorted(pending.get(p, []))
        short = max(min_valid - r["n_valid"], 1)   # a tie needs one more label
        new = max(0, min(short - len(waiting), max_labels - int(active.get(p, 0))))
        rows.append({"passage_id": p, "valid_labels": r["n_valid"],
                     "waiting_for": ";".join(waiting), "new_labels": new,
                     "closed": not waiting and not new})
    return pd.DataFrame(rows, columns=["passage_id", "valid_labels", "waiting_for",
                                       "new_labels", "closed"])


def resolved_rule(adjudicated: pd.DataFrame, strata: Mapping[str, str], *,
                  share: float = MIN_RESOLVED_SHARE,
                  stratum_share: float = MIN_STRATUM_RESOLVED_SHARE) -> dict:
    """Is enough of the sample resolved for the speaker effect to score the pilot?
    At least `share` of all passages and `stratum_share` of every stratum."""
    done = adjudicated[f"adjudicated_{PRIMARY}"] != UNRESOLVED
    s = adjudicated["passage_id"].map(strata)
    rows = {"all": (int(done.sum()), len(done), math.ceil(share * len(done) - 1e-9))}
    for name in sorted(set(s)):
        m = (s == name).to_numpy()
        rows[name] = (int(done[m].sum()), int(m.sum()),
                      math.ceil(stratum_share * m.sum() - 1e-9))
    return {"met": all(got >= want for got, _n, want in rows.values()),
            "groups": {k: {"resolved": got, "passages": n, "required": want}
                       for k, (got, n, want) in rows.items()}}


# -- agreement -------------------------------------------------------------------------

def krippendorff_alpha(units: Iterable[Iterable[str]]) -> float | None:
    """Krippendorff's alpha for nominal labels: 1 - observed / expected disagreement.

    `units`: for each passage, the labels it received (any number; a passage with
    fewer than two is left out, as it has nothing to compare). Every pair of labels
    within a passage counts, weighted 1 / (m - 1) for a passage with m labels, so
    each label counts once however many labellers a passage had. Expected
    disagreement comes from the pooled label frequencies, drawn without
    replacement. None when it cannot be computed (no pairable labels, or one label
    only was ever used).
    """
    units = [u for u in (list(u) for u in units) if len(u) >= 2]
    index = {c: i for i, c in enumerate(sorted({v for u in units for v in u}))}
    o = np.zeros((len(index), len(index)))        # coincidence matrix
    for u in units:
        c = np.bincount([index[v] for v in u], minlength=len(index)).astype(float)
        o += (np.outer(c, c) - np.diag(c)) / (len(u) - 1)
    totals, n = o.sum(axis=1), float(o.sum())
    if n <= 1:
        return None
    observed = (n - np.trace(o)) / n
    expected = (n * n - float((totals ** 2).sum())) / (n * (n - 1))
    return None if expected == 0 else float(1 - observed / expected)


def alphas(labels: pd.DataFrame, subset: Iterable[str] | None = None) -> dict:
    """Alpha per field over the valid labels (optionally only some passages)."""
    d = labels if subset is None else labels.loc[labels["passage_id"].isin(set(subset))]
    return {f: krippendorff_alpha(g[f].tolist() for _p, g in d.groupby("passage_id"))
            for f in FIELDS}


# -- scoring ---------------------------------------------------------------------------

def score(labels: pd.DataFrame, predictions: pd.DataFrame, alpha: float | None, *,
          n_boot: int = P.N_BOOT, seed: int = P.BOOTSTRAP_SEED) -> dict:
    """Version 1's comparison, with the majority label as the reference.

    `labels`: passage_id, symbol, adjudicated (`P.EFFECTS` or `unresolved`),
    hard_case. `predictions`: passage_id, baseline, extraction. `alpha`: on the
    speaker effect. Metrics, thresholds, bootstrap and seeds are `impact_pilot`'s.
    Unresolved passages and those whose majority is "unclear" (cannot tell) are
    counted and reported, not scored. Criteria v and vi need no reference label and
    are taken over every sampled passage, as in version 1.
    """
    d = labels.merge(predictions, on="passage_id", how="left", validate="one_to_one")
    if d[["baseline", "extraction"]].isna().any().any():
        raise ValueError("a labelled passage has no prediction")
    s = d.loc[d["adjudicated"].isin(P.SCORED)]
    out = {"protocol_version": PROTOCOL_VERSION, "n": len(d), "n_scored": len(s),
           "n_unresolved": int((d["adjudicated"] == UNRESOLVED).sum()),
           "n_unclear": int((d["adjudicated"] == "unclear").sum()), "alpha": alpha}
    if alpha is None or alpha < ALPHA_FLOOR:
        return {**out, "criteria": {"i_labeller_agreement": False}, "passed": False,
                "verdict": "labels_not_good_enough"}
    base = P.class_metrics(s["adjudicated"], s["baseline"])
    ext = P.class_metrics(s["adjudicated"], s["extraction"])
    diff = P.bootstrap_f1_difference(s["adjudicated"], s["baseline"], s["extraction"],
                                     s["symbol"], n_boot=n_boot, seed=seed)
    h = s.loc[s["hard_case"].astype(bool)]
    hard = {"n": int(d["hard_case"].astype(bool).sum()), "n_scored": len(h),
            "baseline_macro_f1": P.class_metrics(h["adjudicated"], h["baseline"])["macro_f1"],
            "extraction_macro_f1": P.class_metrics(h["adjudicated"],
                                                   h["extraction"])["macro_f1"]}
    invalid = float((d["extraction"] == P.INVALID).mean())
    unclear = float((d["extraction"] == "unclear").mean())

    def precise(c: str) -> bool:
        v = ext["classes"][c]["precision"]
        return v is not None and v >= P.MIN_PRECISION

    criteria = {
        "i_labeller_agreement": True,
        "ii_macro_f1_gain": diff["difference"] >= P.MIN_F1_GAIN and diff["ci95"][0] > 0,
        "iii_precision_harmed": precise("harmed"),
        "iii_precision_benefits": precise("benefits"),
        "iv_hard_cases_not_worse": hard["extraction_macro_f1"] >= hard["baseline_macro_f1"],
        "v_spans_verbatim": invalid <= P.MAX_INVALID_SHARE,
        "vi_unclear_share": unclear <= P.MAX_UNCLEAR_SHARE,
    }
    passed = all(criteria.values())
    return {**out, "baseline": base, "extraction": ext, "difference": diff,
            "hard_case": hard, "extraction_invalid_share": invalid,
            "extraction_unclear_share": unclear, "criteria": criteria, "passed": passed,
            "verdict": "pass" if passed else "fail"}
