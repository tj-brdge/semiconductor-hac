"""Economic-impact pilot, protocol version 2: the invited-labeller steps the owner runs.

Protocol: docs/experiments/ECONOMIC_IMPACT_PILOT_V2.md. The rules are in
`impact_crowd` (pure); this module reads and writes the files. It calls no model
and no network. The sample, the export, the model run and the answer cache are
version 1's (`impact_run`), used unchanged.

- `pack`     the labelling folder: the static page and one data file per labeller;
- `collect`  validates returned files, applies the attention-check rule, writes the
             majority labels (`adjudicated.csv`) and `crowd_report.md`;
- `score`    version 1's comparison against the majority labels (`result_v2.md`).

Files, under the pilot's work folder:
  crowd/                   what is hosted or sent: index.html and data/<code>.json
  crowd_assignment.csv     which code got which passage and check (owner only)
  crowd_labels/            everything `collect` writes
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import pandas as pd

from . import impact_crowd as C
from . import impact_pilot as P
from . import impact_run as R

DOC = R.ROOT / "docs" / "experiments" / "ECONOMIC_IMPACT_PILOT_V2.md"
PAGE = Path(__file__).parent / "crowd_page" / "index.html"
ASSIGNMENT_COLUMNS = ("code", "item_id", "kind", "ref", "round", "position")
# Names that must never appear in the labelling folder outside a passage's own text.
FORBIDDEN = ("theme_id", "theme_tone", "stratum", "selected_as", "quota_tag", "hard_case",
             "sector", "baseline", "extraction", "prediction", "attention", "answers.jsonl",
             *P.STRATA, *(t for t in P.HARD_ORDER if t != "hypothetical"))   # an answer option


def folders(work: Path) -> dict[str, Path]:
    work = Path(work)
    return {"crowd": work / "crowd", "assignment": work / "crowd_assignment.csv",
            "out": work / "crowd_labels"}


def pack_id(work: Path, seed: int = P.SAMPLE_SEED) -> str:
    """Ties a data file and a returned file to this sample and seed."""
    return P._h(seed, "crowd-pack", R._sha(Path(work) / "sample.csv"))[:10]


def read_codes(path: Path) -> list[str]:
    """One labeller code per line; blank lines and lines starting with # are skipped."""
    lines = [x.strip() for x in Path(path).read_text().splitlines()]
    codes = [x for x in lines if x and not x.startswith("#")]
    problems = C.code_problems(codes)
    if problems:
        raise R.Refused("the codes file is not usable; nothing was written:\n  "
                        + "\n  ".join(problems[:20]))
    if not codes:
        raise R.Refused(f"{path} holds no code")
    return codes


def _assignment(work: Path) -> pd.DataFrame:
    path = folders(work)["assignment"]
    if not path.exists():
        raise R.Refused(f"{path} is missing: run `crowd-pack` first")
    return R._csv(path)


# -- 1. the labelling folder ------------------------------------------------------------

def blindness_problems(folder: Path, forbidden=FORBIDDEN) -> list[str]:
    """Everything in the labelling folder that a labeller must not be given.

    The folder may hold only the page and `data/<code>.json`; a data file may hold
    only its own code, the pack id and items with exactly `impact_crowd.BLIND_FIELDS`;
    and no forbidden name may appear in the page or in any field other than a
    passage's text and company name (which are the source's own words).
    """
    folder, out = Path(folder), []
    word = re.compile("|".join(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])"
                               for w in forbidden), re.I)
    for path in sorted(x for x in folder.rglob("*") if x.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel == "index.html":
            out += [f"{rel}: contains '{m}'" for m in sorted(set(word.findall(path.read_text())))]
            continue
        m = re.fullmatch(r"data/(\w+)\.json", rel)
        if not m or not C.CODE.fullmatch(m.group(1)):
            out.append(f"{rel}: a file that does not belong in the folder")
            continue
        d = json.loads(path.read_text())
        if set(d) != {"protocol", "pack", "code", "items"} or d["code"] != m.group(1):
            out.append(f"{rel}: unexpected top-level fields {sorted(d)}")
            continue
        for it in d["items"]:
            if tuple(it) != C.BLIND_FIELDS:
                out.append(f"{rel}: an item has fields {sorted(it)}")
                break
            hit = word.findall(" ".join(str(it[k]) for k in ("id", "source", "date")))
            if hit:
                out.append(f"{rel}: contains '{hit[0]}' outside a passage")
                break
    return out


def pack(work: Path, codes_file: Path, *, per_passage: int = C.LABELS_PER_PASSAGE,
         seed: int = P.SAMPLE_SEED, cap: int = C.WORKLOAD_CAP, n_checks: int = C.N_CHECKS,
         page: Path = PAGE, log=print) -> dict:
    """Write the labelling folder for the codes in `codes_file`.

    First time: every sampled passage goes to `per_passage` labellers
    (`impact_crowd.assign`); refuses if the codes are too few for the workload cap.
    Later (a top-up): codes not yet in `crowd_assignment.csv` are given only the
    passages the last `collect` listed as needing new labels; nothing already
    assigned changes, and existing data files are not rewritten.
    """
    work, f = Path(work), folders(work)
    codes = read_codes(codes_file)
    if not (work / "sample.csv").exists():
        raise R.Refused(f"{work / 'sample.csv'} is missing: run `sample` first")
    p = R._passages(work)
    if not per_passage <= C.MAX_LABELS_PER_PASSAGE or per_passage < C.LABELS_PER_PASSAGE:
        raise R.Refused(f"labellers per passage must be {C.LABELS_PER_PASSAGE} to "
                        f"{C.MAX_LABELS_PER_PASSAGE}")
    if f["assignment"].exists():
        old = R._csv(f["assignment"])
        new = sorted(set(codes) - set(old["code"]))
        if not new:
            raise R.Refused("every code in the file already has its passages: the "
                            "assignment is made once and is not replaced. To invite more "
                            "labellers, add new codes to the file after `crowd-collect`")
        summary = _summary(work)
        if summary is None or summary["assignment_sha256"] != R._sha(f["assignment"]):
            raise R.Refused("run `crowd-collect` on the files returned so far first: new "
                            "labellers are given only the passages it lists as needing "
                            "labels (crowd_labels/labels_needed.csv)")
        need = R._csv(f["out"] / "labels_needed.csv")
        need = {r["passage_id"]: int(r["new_labels"]) for r in need.to_dict("records")
                if int(r["new_labels"]) > 0}
        if not need:
            raise R.Refused("no passage needs a label from a new labeller: re-send to the "
                            "labellers `crowd-collect` lists as waiting instead")
        assignment, unmet = C.assign(need, new, need, seed=seed, cap=cap)
        rnd = int(old["round"].astype(int).max()) + 1
    else:
        old, rnd = pd.DataFrame(columns=list(ASSIGNMENT_COLUMNS)), 1
        wanted = C.labellers_needed(len(p), per_passage, cap)
        if len(codes) < wanted:
            raise R.Refused(
                f"{len(codes)} codes are too few: {len(p)} passages x {per_passage} "
                f"labellers at no more than {cap} passages each needs at least {wanted} "
                "labellers. Nothing was written")
        assignment, unmet = C.assign(p["passage_id"], codes, per_passage, seed=seed, cap=cap)
    by_id = {r["passage_id"]: r for r in p.to_dict("records")}
    (f["crowd"] / "data").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(page, f["crowd"] / "index.html")
    pid, keys = pack_id(work, seed), []
    for code, pids in sorted(assignment.items()):
        if not pids:
            log(f"  {code}: no passage left to give; no file written")
            continue
        items, key = C.labeller_items(code, [by_id[x] for x in pids],
                                      C.choose_checks(code, seed, n_checks), seed=seed)
        (f["crowd"] / "data" / f"{code}.json").write_text(json.dumps(
            {"protocol": C.PROTOCOL_VERSION, "pack": pid, "code": code, "items": items},
            ensure_ascii=False, indent=1))
        keys += [{**k, "round": rnd, "position": i + 1} for i, k in enumerate(key)]
    problems = blindness_problems(f["crowd"])
    if problems:
        raise R.Refused("the labelling folder failed its own blindness check and must not "
                        "be sent:\n  " + "\n  ".join(problems[:20]))
    pd.concat([old, pd.DataFrame(keys, columns=list(ASSIGNMENT_COLUMNS))]).to_csv(
        f["assignment"], index=False)
    sizes = [len(v) for v in assignment.values() if v]
    out = {"round": rnd, "labellers": len(sizes), "labels": sum(sizes),
           "largest_set": max(sizes) + n_checks, "unmet": unmet, "pack": pid}
    minutes = max(sizes) + n_checks
    log(f"round {rnd}: {len(sizes)} labellers, {sum(sizes)} passage labels asked for; each "
        f"set has {min(sizes)} to {max(sizes)} passages plus {n_checks} checks (about "
        f"{minutes * C.SECONDS_PER_ITEM // 60} minutes at {C.SECONDS_PER_ITEM} seconds "
        f"each). Wrote {f['crowd']} and {f['assignment'].name}")
    if unmet:
        log(f"  {sum(unmet.values())} labels on {len(unmet)} passages could not be placed: "
            f"invite {-(-sum(unmet.values()) // cap)} more labeller(s) at least")
    return out


# -- 2. returned files ------------------------------------------------------------------

def _summary(work: Path) -> dict | None:
    path = folders(work)["out"] / "crowd_summary.json"
    return json.loads(path.read_text()) if path.exists() else None


def _load(paths, key: pd.DataFrame, pack: str) -> tuple[dict, list[tuple[str, str]]]:
    """({code: (labels, background, file name)}, [(file name, why it was rejected)])."""
    good, bad = {}, []
    for path in sorted(Path(x) for x in paths):
        try:
            code, labels, bg = C.read_returned(json.loads(path.read_text()), key, pack)
        except (OSError, ValueError) as e:      # JSONDecodeError is a ValueError
            bad.append((path.name, str(e) if not isinstance(e, json.JSONDecodeError)
                        else "not a JSON file"))
            continue
        if code in good:
            bad += [(good.pop(code)[2], f"two files carry code {code}: pass only the latest"),
                    (path.name, f"two files carry code {code}: pass only the latest")]
            good[code] = None
        else:
            good[code] = (labels, bg, path.name)
    return {c: v for c, v in good.items() if v is not None}, bad


def collect(work: Path, returned, *, min_valid: int = C.MIN_VALID_LABELS,
            max_wrong: int = C.MAX_CHECKS_WRONG, share: float = C.MIN_RESOLVED_SHARE,
            stratum_share: float = C.MIN_STRATUM_RESOLVED_SHARE,
            cap: int = C.WORKLOAD_CAP, log=print) -> dict:
    """Read the returned label files and write, under `crowd_labels/`:

    crowd_report.md, labellers.csv, labels_long.csv (codes and labels; no comments,
    no text), labels_needed.csv, crowd_summary.json and, only when the
    resolved-share rule is met, adjudicated.csv. Pass every file returned so far
    each time: nothing is remembered between runs. A file with any problem is
    rejected whole and named with the reason.
    """
    work, f = Path(work), folders(work)
    key, sample = _assignment(work), R._csv(work / "sample.csv")
    good, bad = _load(returned, key, pack_id(work))
    per_code = {c: g for c, g in key.groupby("code")}
    rows, labels, background = [], [], Counter()
    for code, g in sorted(per_code.items()):
        given = good.get(code)
        checks = list(g.loc[g["kind"] == "check", "ref"])
        st = C.labeller_status(given[0] if given else [], checks, max_wrong=max_wrong)
        status = st["status"] if given else "not returned"
        n = int((g["kind"] == "passage").sum())
        mine = [r for r in (given[0] if given else []) if r["kind"] == "passage"]
        rows.append({"code": code, "passages": n, "labelled": len(mine),
                     "checks_right": st["checks_right"], "checks_wrong": st["checks_wrong"],
                     "checks": st["checks"], "status": status,
                     "comments": sum(r["has_comment"] for r in (given[0] if given else []))})
        labels += [{"passage_id": r["ref"], "code": code, "labeller": status,
                    **{k: r[k] for k in C.FIELDS}} for r in mine]
        if status == "valid":
            background.update((k, v) for k, v in given[1].items())
    who = pd.DataFrame(rows)
    long = pd.DataFrame(labels, columns=["passage_id", "code", "labeller", *C.FIELDS])
    valid = long.loc[long["labeller"] == "valid"]
    adj = C.adjudicate(sample["passage_id"], valid, min_valid=min_valid)
    status = dict(zip(who["code"], who["status"], strict=True))
    done = set(zip(long["code"], long["passage_id"], strict=True))
    pending, active = {}, {}
    for r in key.loc[key["kind"] == "passage"].to_dict("records"):
        if status[r["code"]] == "excluded":
            continue
        active[r["ref"]] = active.get(r["ref"], 0) + 1
        if status[r["code"]] != "valid" or (r["code"], r["ref"]) not in done:
            pending.setdefault(r["ref"], []).append(r["code"])
    needed = C.labels_needed(adj, pending, active, min_valid=min_valid)
    strata = dict(zip(sample["passage_id"], sample["stratum"], strict=True))
    rule = C.resolved_rule(adj, strata, share=share, stratum_share=stratum_share)
    hard = sample.loc[sample["selected_as"] == "hard_case", "passage_id"]
    alpha = {"all": C.alphas(valid), "hard": C.alphas(valid, hard)}
    unresolved = {
        fld: {s: int(((adj[f"adjudicated_{fld}"] == C.UNRESOLVED)
                      & (adj["passage_id"].map(strata) == s)).sum()) for s in sorted(
                          set(strata.values()))} for fld in C.FIELDS}
    hard_unresolved = int(((adj[f"adjudicated_{C.PRIMARY}"] == C.UNRESOLVED)
                           & adj["passage_id"].isin(set(hard))).sum())

    out = f["out"]
    out.mkdir(parents=True, exist_ok=True)
    who.to_csv(out / "labellers.csv", index=False)
    long.to_csv(out / "labels_long.csv", index=False)
    needed.to_csv(out / "labels_needed.csv", index=False)
    target = out / "adjudicated.csv"
    if rule["met"]:
        adj.to_csv(target, index=False)
    elif target.exists():
        target.unlink()
    new = int(needed["new_labels"].sum()) if len(needed) else 0
    waiting = sorted({c for w in needed["waiting_for"] for c in w.split(";") if c})
    res = {
        "protocol_version": C.PROTOCOL_VERSION, "pack": pack_id(work),
        "assignment_sha256": R._sha(f["assignment"]),
        "adjudicated_sha256": R._sha(target) if rule["met"] else None,
        "passages": len(adj), "rule": rule, "alpha": alpha, "unresolved": unresolved,
        "hard_unresolved": hard_unresolved, "hard_passages": int(len(hard)),
        "labellers": dict(Counter(who["status"])), "rejected": bad,
        "needed": {"passages": int(len(needed)), "new_labels": new,
                   "new_labellers_at_least": -(-new // cap), "resend_to": waiting,
                   "closed": int(needed["closed"].sum()) if len(needed) else 0},
        "background": {f"{k}: {v}": n for (k, v), n in sorted(background.items())}}
    (out / "crowd_summary.json").write_text(json.dumps(res, indent=1))
    (out / "crowd_report.md").write_text(report_markdown(res, who, needed))

    for name, why in bad:
        log(f"REJECTED {name}: {why}")
    g = rule["groups"]["all"]
    log(f"labellers: {res['labellers']}; resolved for the effect on the company: "
        f"{g['resolved']} of {g['passages']} (required {g['required']}); alpha "
        f"{R._f(alpha['all'][C.PRIMARY])} (floor {C.ALPHA_FLOOR:.2f})")
    if rule["met"]:
        log(f"wrote {target} and {out / 'crowd_report.md'}")
    else:
        log("NOT ENOUGH RESOLVED PASSAGES: adjudicated.csv was not written. "
            + needed_sentence(res["needed"]))
        log(f"The passages are listed in {out / 'labels_needed.csv'} (first: "
            f"{', '.join(needed['passage_id'].head(10))}). Report: {out / 'crowd_report.md'}")
    return {"status": "adjudicated" if rule["met"] else "more_labels_needed", **res}


def needed_sentence(n: dict) -> str:
    return (f"{n['passages']} passages are unresolved. Waiting for labels from "
            f"{len(n['resend_to'])} labeller(s) already invited"
            + (f" ({', '.join(n['resend_to'][:12])})" if n["resend_to"] else "")
            + f"; beyond those, {n['new_labels']} more labels are needed from new "
            f"labellers (at least {n['new_labellers_at_least']} more people); "
            f"{n['closed']} passages cannot be resolved any more.")


def report_markdown(res: dict, who: pd.DataFrame, needed: pd.DataFrame) -> str:
    rule, alpha = res["rule"], res["alpha"]
    out = ["# Economic-impact pilot: invited labellers", "",
           f"Protocol version {res['protocol_version']} "
           "(docs/experiments/ECONOMIC_IMPACT_PILOT_V2.md, section 5). Labellers appear by "
           "code only. No model answer, phrase-list label, price or return was used here.",
           "",
           "## Is the sample resolved enough to score?", "",
           f"**{'Yes' if rule['met'] else 'No: adjudicated.csv was not written'}.** A passage "
           f"is resolved for the effect on the company when at least {C.MIN_VALID_LABELS} "
           "valid labellers answered it and more than half of them agree.", ""]
    out += R._table(["Group", "Passages", "Resolved", "Required", "Result"],
                    [[k, v["passages"], v["resolved"], v["required"],
                      "PASS" if v["resolved"] >= v["required"] else "FAIL"]
                     for k, v in rule["groups"].items()])
    out += ["## Agreement (Krippendorff's alpha, nominal, valid labellers only)", "",
            f"Floor on the effect on the company: {C.ALPHA_FLOOR:.2f}. Below it the pilot "
            "gives no verdict.", ""]
    out += R._table(["Field", "Alpha, all passages", "Alpha, hard cases"],
                    [[k, R._f(alpha["all"][k]), R._f(alpha["hard"][k])] for k in C.FIELDS])
    out += ["## Unresolved passages, by field and stratum", "",
            "Unresolved passages are left out of that field's score.", ""]
    strata = sorted(next(iter(res["unresolved"].values())))
    out += R._table(["Field", *strata, "all"],
                    [[k, *(v[s] for s in strata), sum(v.values())]
                     for k, v in res["unresolved"].items()])
    out += [f"Hard-case quota passages unresolved for the effect on the company: "
            f"{res['hard_unresolved']} of {res['hard_passages']}.", "",
            "## Labellers", "",
            f"A labeller with more than {C.MAX_CHECKS_WRONG} of the attention checks wrong "
            "on the effect on the company is excluded entirely; one who has not answered "
            "enough checks yet is incomplete and not used until they finish.", ""]
    out += R._table(["Code", "Passages", "Labelled", "Checks right", "Checks wrong", "Status",
                     "Comments"],
                    [[r["code"], r["passages"], r["labelled"],
                      f"{r['checks_right']} of {r['checks']}", r["checks_wrong"], r["status"],
                      r["comments"]] for r in who.to_dict("records")])
    if res["background"]:
        out += ["Self-declared background of the valid labellers (recorded, never used to "
                "filter): " + "; ".join(f"{k} ({v})" for k, v in res["background"].items())
                + ".", ""]
    if res["rejected"]:
        out += ["## Rejected files (not used at all)", ""]
        out += R._table(["File", "Reason"], [list(x) for x in res["rejected"]])
    out += ["## Labels still needed", "", needed_sentence(res["needed"]), ""]
    if len(needed):
        out += R._table(["Passage", "Valid labels", "Waiting for", "New labels needed",
                         "Closed"], [[r["passage_id"], r["valid_labels"],
                                      r["waiting_for"].replace(";", ", ") or "-",
                                      r["new_labels"], "yes" if r["closed"] else ""]
                                     for r in needed.to_dict("records")])
    return "\n".join(out)


# -- 3. scoring -------------------------------------------------------------------------

def score(work: Path, answers: Path, *, model: str = R.MODEL, n_boot: int = P.N_BOOT,
          log=print) -> dict:
    """Version 1's criteria against the majority labels; writes `result_v2.md` and
    `predictions.csv`. Refuses without an `adjudicated.csv` that is exactly the one
    `collect` wrote (hand edits are not accepted)."""
    work, out = Path(work), folders(work)["out"]
    summary, target = _summary(work), out / "adjudicated.csv"
    if summary is None or not target.exists() or not summary["rule"]["met"]:
        raise R.Refused(f"{target} is missing: `crowd-collect` writes it once enough "
                        "passages are resolved")
    if hashlib.sha256(target.read_bytes()).hexdigest() != summary["adjudicated_sha256"]:
        raise R.Refused(f"{target} is not the file `crowd-collect` wrote: majority labels "
                        "are not edited by hand. Run `crowd-collect` again")
    p = R._passages(work)
    adj = R._csv(target).set_index("passage_id").reindex(p["passage_id"])
    if adj[f"adjudicated_{C.PRIMARY}"].isna().any():
        raise R.Refused("adjudicated.csv does not cover the sample")
    d, reasons = R.predictions(p, answers, model)
    d["adjudicated"] = adj[f"adjudicated_{C.PRIMARY}"].to_numpy()
    d["hard_case"] = (p["selected_as"] == "hard_case").to_numpy()
    alpha = summary["alpha"]["all"][C.PRIMARY]
    res = C.score(d[["passage_id", "symbol", "adjudicated", "hard_case"]],
                  d[["passage_id", "baseline", "extraction"]], alpha, n_boot=n_boot)
    extra = {"model": model, "summary": summary}
    if res["verdict"] != "labels_not_good_enough":
        opp = adj["adjudicated_opposite_party"].to_numpy()
        typ = adj["adjudicated_statement_type"].to_numpy()
        yes, known = opp == "yes", pd.Series(opp).isin(["yes", "no"]).to_numpy()
        said = (d["extraction_opposite_party"] == "yes").to_numpy() & known
        scored, settled = d["adjudicated"].isin(P.SCORED), typ != C.UNRESOLVED
        four = d.loc[d["adjudicated"] != C.UNRESOLVED]
        extra.update({
            "partial_export": len(R._export_report(work).get("unlinked", [])),
            "four_label": {m: P.class_metrics(four["adjudicated"], four[m],
                                              P.EFFECTS)["macro_f1"]
                           for m in ("baseline", "extraction")},
            "baseline_raw_analyst": P.class_metrics(
                d.loc[scored, "adjudicated"],
                d.loc[scored, "baseline_raw_analyst"])["macro_f1"],
            "opposite": {
                "precision": float((yes & said).sum() / said.sum()) if said.any() else None,
                "recall": float((yes & said).sum() / yes.sum()) if yes.any() else None,
                "n_yes": int(yes.sum()), "n": int(known.sum())},
            "statement": {
                "accuracy": float((d["extraction_statement_type"].to_numpy()[settled]
                                   == typ[settled]).mean()) if settled.any() else None,
                "n": int(settled.sum())},
            "invalid_reasons": "; ".join(
                f"{k} ({v})" for k, v in
                pd.Series(reasons, dtype=object).value_counts().items())})
    (work / "result_v2.md").write_text(result_markdown(res, d, extra))
    d[["passage_id", "baseline", "extraction", "extraction_opposite_party",
       "extraction_statement_type"]].to_csv(work / "predictions.csv", index=False)
    log(f"verdict: {res['verdict'].upper()}. Wrote {work / 'result_v2.md'} and "
        "predictions.csv")
    return {"status": "scored", **res, "extra": extra}


def result_markdown(res: dict, d: pd.DataFrame, extra: dict) -> str:
    s = extra["summary"]
    g = s["rule"]["groups"]["all"]
    head = ["# Economic-impact extraction pilot: result (protocol version 2)", "",
            "Protocol: docs/experiments/ECONOMIC_IMPACT_PILOT_V2.md, which changes only "
            "version 1's adjudication. **The sample, labels, metrics and thresholds below "
            "were fixed before any result existed; none was changed after results were "
            "seen.** No price or return was used.", "",
            f"Reference labels: the majority of invited labellers ({s['labellers']}). "
            f"Passages: {res['n']}; resolved for the effect on the company: {g['resolved']} "
            f"(required {g['required']}); unresolved and left out: {res['n_unresolved']}; "
            f"majority \"cannot tell\" (left out of the primary score, as version 1's "
            f"\"unclear\"): {res['n_unclear']}; scored: {res['n_scored']}.", ""]
    agreement = R._table(["Field", "Alpha, all passages", "Alpha, hard cases"],
                         [[k, R._f(s["alpha"]["all"][k]), R._f(s["alpha"]["hard"][k])]
                          for k in C.FIELDS])
    if res["verdict"] == "labels_not_good_enough":
        return "\n".join([
            *head, "## Verdict: none", "",
            f"Labeller agreement (Krippendorff's alpha on the effect on the company) is "
            f"{R._f(res['alpha'])}, below the floor of {C.ALPHA_FLOOR:.2f}: criterion i "
            "FAILS. By the protocol the labels cannot judge anything: no comparison is "
            "reported, and a new protocol version must fix the guidelines.", "",
            *agreement])
    c, diff, ext, hard = res["criteria"], res["difference"], res["extraction"], res["hard_case"]

    def mark(ok) -> str:
        return "PASS" if ok else "FAIL"
    out = [*head, f"Model: {extra['model']}.", ""]
    if extra.get("partial_export"):
        out += [f"**Limitation.** {extra['partial_export']} theme-months of the registry "
                "could not be linked exactly by the export (export_report.json).", ""]
    out += [f"## Verdict: {'PASS' if res['passed'] else 'FAIL'}", "",
            "Extraction passes only if every criterion holds." if res["passed"] else
            "At least one criterion failed, so the phrase lists stay (version 1, section 7).",
            "", "## Acceptance criteria", ""]
    out += R._table(["", "Criterion", "Threshold", "Measured", "Result"], [
        ["i", "Labeller alpha, effect on the company", f">= {C.ALPHA_FLOOR:.2f}",
         R._f(res["alpha"]), mark(c["i_labeller_agreement"])],
        ["ii", "Macro-F1 gain over the phrase lists",
         f">= {P.MIN_F1_GAIN:.2f} and 95% interval above zero",
         f"{R._f(diff['difference'])} (interval {R._f(diff['ci95'][0])} to "
         f"{R._f(diff['ci95'][1])}; {diff['n_boot']} resamples of {diff['n_companies']} "
         "companies)", mark(c["ii_macro_f1_gain"])],
        ["iii", "Precision on harmed", f">= {P.MIN_PRECISION:.2f}",
         R._f(ext["classes"]["harmed"]["precision"]), mark(c["iii_precision_harmed"])],
        ["iii", "Precision on benefits", f">= {P.MIN_PRECISION:.2f}",
         R._f(ext["classes"]["benefits"]["precision"]), mark(c["iii_precision_benefits"])],
        ["iv", "Hard cases: macro-F1 not below the lists'", ">= the lists' macro-F1",
         f"{R._f(hard['extraction_macro_f1'])} against {R._f(hard['baseline_macro_f1'])} "
         f"({hard['n_scored']} scored of {hard['n']})", mark(c["iv_hard_cases_not_worse"])],
        ["v", "Answers failing validation (all sampled passages)",
         f"<= {100 * P.MAX_INVALID_SHARE:.0f}%",
         f"{R._f(100 * res['extraction_invalid_share'], 1)}%", mark(c["v_spans_verbatim"])],
        ["vi", "Extraction unclear, including insufficient (all sampled passages)",
         f"<= {100 * P.MAX_UNCLEAR_SHARE:.0f}%",
         f"{R._f(100 * res['extraction_unclear_share'], 1)}%", mark(c["vi_unclear_share"])]])
    out += ["## Hard cases (the quota passages), separately", "",
            "Point estimates on small groups; version 1's power note applies, and "
            f"{s['hard_unresolved']} of {s['hard_passages']} are unresolved.", ""]
    hm = d["hard_case"]
    cols = ["Group", "Passages", "Scored", "Lists macro-F1", "Extraction macro-F1"]
    out += R._table(cols, R._by(d, {"all hard cases": hm, **{
        t: hm & (d["quota_tag"] == t) for t in P.HARD_ORDER}}))
    out += ["## Also reported (not criteria)", ""]
    rows = []
    for name, m in (("Phrase lists", res["baseline"]), ("Extraction", ext)):
        for k in P.SCORED:
            x = m["classes"][k]
            rows.append([name, k, R._f(x["precision"]), R._f(x["recall"]), R._f(x["f1"])])
        rows.append([name, "macro-F1 / accuracy", "", "",
                     f"{R._f(m['macro_f1'])} / {R._f(m['accuracy'])}"])
    out += R._table(["Method", "Class", "Precision", "Recall", "F1"], rows)
    out += [f"- Four-label macro-F1 (resolved passages, cannot tell included): lists "
            f"{R._f(extra['four_label']['baseline'])}, extraction "
            f"{R._f(extra['four_label']['extraction'])}.",
            f"- Phrase lists with analyst questions given their raw phrase score "
            f"(sensitivity check): macro-F1 {R._f(extra['baseline_raw_analyst'])}.",
            f"- Opposite party (extraction only; {extra['opposite']['n']} passages with a "
            f"majority yes or no): precision {R._f(extra['opposite']['precision'])}, recall "
            f"{R._f(extra['opposite']['recall'])} ({extra['opposite']['n_yes']} yes).",
            f"- Statement type (extraction only; {extra['statement']['n']} resolved "
            f"passages): accuracy {R._f(extra['statement']['accuracy'])}.",
            f"- Invalid answers by reason: {extra['invalid_reasons'] or 'none'}.", ""]
    out += agreement
    out += R._table(cols, R._by(d, {
        **{f"stratum {x}": d["stratum"] == x for x in P.STRATA},
        **{f"role {r}": d["role"] == r for r in sorted(set(d["role"]))},
        **{f"tag {t}": d["hard_case_tags"].str.split(";").map(lambda v, t=t: t in v)
           for t in P.HARD_ORDER}}))
    return "\n".join(out)
