"""Protocol version 2 of the impact pilot: invited labellers (experimental/impact_crowd.py,
impact_crowd_run.py, the crowd-* commands). Offline: synthetic data, a fake model client."""

import html
import importlib.util
import json
import random
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from themegraph.experimental import impact_crowd as C
from themegraph.experimental import impact_crowd_run as CR
from themegraph.experimental import impact_pilot as P
from themegraph.experimental import impact_run as R

ROOT = Path(__file__).resolve().parents[1]
DOC_V1 = ROOT / "docs" / "experiments" / "ECONOMIC_IMPACT_PILOT.md"
quiet = lambda *a: None  # noqa: E731
CAP = 12                 # small study: 30 passages x 3 labellers / 12 = 8 labellers at least
ANALYST = C.SOURCES["analyst_question"]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V1 = _load("impact_pilot_run_tests", ROOT / "tests" / "test_impact_pilot_run.py")
SCRIPT = _load("impact_pilot_script_v2", ROOT / "scripts" / "impact_pilot.py")
CODES = C.new_codes(10, random.Random(7))


def _truth(item: dict) -> dict:
    """What a careful labeller answers (the synthetic texts are the worked examples)."""
    for key in C.CHECKS:
        c = C.check(key)
        if item["text"].startswith(c["text"]):
            role = "analyst_question" if item["source"] == ANALYST else c["role"]
            if role == "analyst_question":
                return {"speaker_effect": "neutral", "opposite_party": "no",
                        "statement_type": "none"}
            return {f: c[f] for f in C.FIELDS}
    return {"speaker_effect": "neutral", "opposite_party": "no", "statement_type": "none"}


def _answer(study, code: str, change=lambda item, lab: lab, upto: int | None = None,
            comment: str = "") -> Path:
    """Fill in one labeller's set the way the page does, and save the returned file."""
    d = json.loads((study.work / "crowd" / "data" / f"{code}.json").read_text())
    labels = [{"id": it["id"], **change(it, _truth(it)), "comment": comment}
              for it in d["items"][:upto]]
    path = study.returned / f"labels_{code}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({
        "protocol": "2", "pack": d["pack"], "code": code,
        "background": {"role": "buy_side", "years": "over_10"}, "labels": labels}))
    return path


@pytest.fixture
def study(tmp_path):
    work = V1._work(tmp_path)
    R.sample(work, log=quiet, **V1.SMALL)
    codes = tmp_path / "codes.txt"
    codes.write_text("# round one\n" + "\n".join(CODES[:9]) + "\n")
    out = CR.pack(work, codes, cap=CAP, log=quiet)
    return NS(work=work, codes=codes, pack=out, returned=tmp_path / "returned",
              answers=tmp_path / "state" / "answers.jsonl", tmp=tmp_path)


def _collect(study, files=None, **kw):
    files = sorted(study.returned.glob("*.json")) if files is None else files
    return CR.collect(study.work, files, cap=CAP, log=quiet, **kw)


# -- the protocol and the page say what the code does -----------------------------------

def test_protocol_v2_states_the_declared_numbers_and_leaves_version_1_alone():
    doc = " ".join(CR.DOC.read_text().split())
    assert "Version 2 · 2026-11-26" in doc and "awaiting the owner's approval; not run" in doc
    assert "only in section 5 (adjudication) and in the checklist" in doc
    for words in (f"at least {C.LABELS_PER_PASSAGE} of them", f"never more than **{5}**",
                  f"{C.WORKLOAD_CAP} sample passages per labeller", f"**{C.N_CHECKS} each**",
                  f"more than {C.MAX_CHECKS_WRONG} of the {C.N_CHECKS} wrong",
                  f"{C.MIN_RESOLVED_SHARE:.0%} of all passages (240 of 300)",
                  "70 of the 100 in each stratum", f"at or above {C.ALPHA_FLOOR:.2f}",
                  f"fewer than {C.MIN_VALID_LABELS} valid labels", str(P.SAMPLE_SEED),
                  f"at least {C.labellers_needed(300)} labellers",
                  f"about {C.SECONDS_PER_ITEM} seconds"):
        assert words in doc, words
    assert C.ALPHA_FLOOR == P.KAPPA_FLOOR == 0.60 and C.MAX_LABELS_PER_PASSAGE == 5
    assert C.MIN_STRATUM_RESOLVED_SHARE == 0.70
    assert (C.WORKLOAD_CAP + C.N_CHECKS) * C.SECONDS_PER_ITEM / 60 == pytest.approx(36.75)
    # Version 1 is frozen: still version 1, still approved, and its numbers are untouched.
    v1 = DOC_V1.read_text()
    assert v1.startswith("# Economic-impact extraction pilot: protocol\n\nVersion 1 ·")
    assert P.PROTOCOL_VERSION == "1" and C.PROTOCOL_VERSION == "2"


def test_attention_checks_are_version_1s_worked_examples_with_its_answers():
    v1 = " ".join(DOC_V1.read_text().split())
    assert sorted(C.CHECKS, key=lambda k: int(k[1:])) == [f"S{i}" for i in range(1, 11)]
    for key in C.CHECKS:
        c = C.check(key)
        m = re.search(re.escape(" ".join(c["text"].split()))
                      + r'" (\w+)[^·]*· (\w+)[^·]*· (\w+)', v1)
        assert m, key
        assert [x.lower() for x in m.groups()] == [c[f] for f in C.FIELDS], key
        assert all(c[f] in C.FIELDS[f] for f in C.FIELDS) and c["role"] in C.SOURCES
    assert len({C.check(k)["company"] for k in C.CHECKS}) == 10


def _rules(text: str, list_id: str) -> list[str]:
    block = re.search(rf'<ol class="rules" id="{list_id}">(.*?)</ol>', text, re.S).group(1)
    return [" ".join(html.unescape(x).split()) for x in re.findall(r"<li>(.*?)</li>", block,
                                                                  re.S)]


def test_page_is_self_contained_and_carries_the_protocols_guidelines():
    page = CR.PAGE.read_text()
    doc = CR.DOC.read_text()
    guide = re.findall(r"^\d\. (.+)$", doc.split("### 5.3 Guidelines")[1].split("###")[0],
                       re.M)
    assert len(guide) == 9 and _rules(page, "full-rules") == guide
    assert 8 <= len(_rules(page, "short-rules")) <= 10
    # No request to anywhere else: no absolute URL, no external script, style, font or image.
    assert not re.search(r"https?:|//\w|<link|<img|<iframe|src=|@import|url\(", page)
    assert "default-src 'none'" in page and "connect-src 'self'" in page
    assert page.count("fetch(") == 1 and 'fetch("data/" + code + ".json"' in page
    assert not re.search(r"XMLHttpRequest|sendBeacon|WebSocket|EventSource|gtag|analytics",
                         page, re.I)
    assert "innerHTML" not in page                      # passage text is never parsed as HTML
    assert "prefers-color-scheme: dark" in page and "gradient" not in page
    # The answer values the page writes are exactly the ones the collector accepts.
    for field, allowed in C.FIELDS.items():
        block = page.split(f'key: "{field}"')[1].split("]] }")[0]
        assert tuple(re.findall(r'\["(\w+)", "', block)) == allowed
    for field, allowed in C.BACKGROUND.items():
        block = page.split(f'id="bg-{field}"')[1].split("</select>")[0]
        assert set(re.findall(r'value="(\w+)"', block)) == set(allowed)
    assert C.CODE.pattern in page.replace("^", "").replace("$", "")
    assert not CR.blindness_problems(CR.PAGE.parent)


# -- codes ------------------------------------------------------------------------------

def test_codes_must_be_opaque_names_and_emails_are_refused(tmp_path):
    codes = C.new_codes(12)
    assert len(set(codes)) == 12 and not C.code_problems(codes)
    for bad, why in (("alice", "name"), ("Maria Lopez", "name"), ("JOHN1985", "name"),
                     ("j.smith@example.com", "email"), ("ML1985", "name"),
                     ("kqzt4827", "name"), ("12345678", "not an opaque code")):
        problems = C.code_problems([bad])
        assert len(problems) == 1 and why in problems[0], bad
    assert "appears 2 times" in C.code_problems([codes[0], codes[0]])[0]
    f = tmp_path / "codes.txt"
    f.write_text(f"{codes[0]}\njane.doe@example.com\nPriya\n")
    with pytest.raises(R.Refused, match="looks like an email address") as e:
        CR.read_codes(f)
    assert "looks like a name" in str(e.value) and "nothing was written" in str(e.value)
    # The command refuses too, writes nothing, and `crowd-codes` makes usable codes.
    assert SCRIPT.main(["--work", str(tmp_path / "w"), "crowd-pack", "--codes", str(f)]) == 1
    assert not (tmp_path / "w").exists()
    out = tmp_path / "new.txt"
    assert SCRIPT.main(["crowd-codes", "--n", "12", "--out", str(out)]) == 0
    assert len(CR.read_codes(out)) == 12
    assert SCRIPT.main(["crowd-codes", "--n", "3", "--out", str(out)]) == 1   # never replaced


# -- assignment -------------------------------------------------------------------------

def test_assignment_is_deterministic_balanced_and_within_the_cap():
    pids = [f"p{i:03d}" for i in range(300)]
    codes = C.new_codes(20, random.Random(1))
    a, unmet = C.assign(pids, codes)
    b, _ = C.assign(list(reversed(pids)), list(reversed(codes)))
    assert a == b and not unmet                      # depends on neither order
    assert {len(v) for v in a.values()} == {C.WORKLOAD_CAP} and C.labellers_needed(300) == 20
    who = {p: [c for c in codes if p in a[c]] for p in pids}
    assert all(len(v) == len(set(v)) == 3 for v in who.values())
    assert C.assign(pids, codes, seed=1)[0] != a     # and on the seed
    # More labellers: lighter, still balanced; five per passage needs 34.
    a, unmet = C.assign(pids, C.new_codes(26, random.Random(2)))
    assert not unmet and {len(v) for v in a.values()} == {34, 35}
    assert C.labellers_needed(300, 5) == 34
    a, unmet = C.assign(pids, C.new_codes(34, random.Random(3)), 5)
    assert not unmet and max(len(v) for v in a.values()) <= 45
    assert all(sum(p in v for v in a.values()) == 5 for p in pids)
    # Too few: nobody goes over the cap; what could not be placed is reported.
    a, unmet = C.assign(pids, codes[:19])
    assert max(len(v) for v in a.values()) == 45 and sum(unmet.values()) == 900 - 19 * 45


def test_pack_refuses_too_few_labellers(study, tmp_path):
    (tmp_path / "other").mkdir()
    work = V1._work(tmp_path / "other")
    R.sample(work, log=quiet, **V1.SMALL)
    few = tmp_path / "few.txt"
    few.write_text("\n".join(CODES[:7]))
    with pytest.raises(R.Refused, match="needs at least 8 labellers"):
        CR.pack(work, few, cap=CAP, log=quiet)
    assert not (work / "crowd").exists() and not (work / "crowd_assignment.csv").exists()
    with pytest.raises(R.Refused, match="made once and is not replaced"):
        CR.pack(study.work, study.codes, cap=CAP, log=quiet)


def test_every_labeller_gets_balanced_attention_checks_chosen_by_code():
    for code in CODES:
        checks = C.choose_checks(code)
        assert checks == C.choose_checks(code) and len(set(checks)) == C.N_CHECKS == 4
        assert {C.check(k)["speaker_effect"] for k in checks} == set(P.SCORED)
    assert len({tuple(C.choose_checks(c)) for c in CODES}) > 1
    # One answer throughout fails at least two checks, so that labeller is excluded.
    for code in CODES:
        checks = C.choose_checks(code)
        for same in C.FIELDS["speaker_effect"]:
            given = [{"kind": "check", "ref": k, "speaker_effect": same} for k in checks]
            assert C.labeller_status(given, checks)["status"] == "excluded"


# -- the labelling folder ---------------------------------------------------------------

def test_pack_gives_each_labeller_only_their_own_blind_items(study):
    crowd = study.work / "crowd"
    files = sorted(x.relative_to(crowd).as_posix() for x in crowd.rglob("*") if x.is_file())
    assert files == sorted(["index.html", *(f"data/{c}.json" for c in CODES[:9])])
    assert (crowd / "index.html").read_bytes() == CR.PAGE.read_bytes()
    key = pd.read_csv(study.work / "crowd_assignment.csv", dtype=str)
    sample = pd.read_csv(study.work / "sample.csv", dtype=str)
    passages = R._passages(study.work).set_index("passage_id")
    per_passage, ids, orders = {}, set(), []
    for code in CODES[:9]:
        d = json.loads((crowd / "data" / f"{code}.json").read_text())
        assert set(d) == {"protocol", "pack", "code", "items"} and d["code"] == code
        mine = key.loc[key["code"] == code]
        assert [it["id"] for it in d["items"]] == list(mine["item_id"])
        assert len(d["items"]) == 10 + C.N_CHECKS <= CAP + C.N_CHECKS
        assert not ids & {it["id"] for it in d["items"]}       # handles differ by labeller
        ids |= {it["id"] for it in d["items"]}
        for it, k in zip(d["items"], mine.to_dict("records"), strict=True):
            assert tuple(it) == C.BLIND_FIELDS
            if k["kind"] == "check":
                c = C.check(k["ref"])
                assert (it["text"], it["company"]) == (c["text"], c["company"])
                assert it["date"] in set(passages["date"])     # not told apart by its date
            else:
                row = passages.loc[k["ref"]]
                assert (it["text"], it["company"], it["date"]) == (
                    row["text"], row["company"], row["date"])
                assert it["source"] == C.SOURCES[row["role"]]
                per_passage.setdefault(k["ref"], set()).add(code)
        assert sorted(mine.loc[mine["kind"] == "check", "ref"]) == sorted(C.choose_checks(code))
        orders.append([k["ref"] for k in mine.to_dict("records")])
    assert set(per_passage) == set(sample["passage_id"])
    assert all(len(v) == 3 for v in per_passage.values())     # every passage: 3 labellers
    assert sorted(orders[0]) != orders[0]                      # shuffled
    assert study.pack["labels"] == 90 and study.pack["round"] == 1


def test_nothing_forbidden_is_anywhere_in_the_labelling_folder(study):
    crowd = study.work / "crowd"
    sample = pd.read_csv(study.work / "sample.csv", dtype=str)
    export = pd.read_parquet(study.work / "export.parquet")
    names = ["theme_id", "theme_tone", "stratum", "selected_as", "quota_tag", "hard_case",
             "hard_case_tags", "sector", "symbol", "baseline", "extraction", "prediction",
             "passage_id", "checks_right", "attention",
             "headwind", "tailwind", "mixed", "negation", "comparative", "cross_party",
             "risk_boilerplate", "analyst_question", "management_remarks", "claude", "opus",
             *sorted(set(export["theme_id"])), *sorted(set(export["sector"])),
             *sorted(set(sample["symbol"])), *sample["passage_id"], *C.CHECKS]
    word = re.compile("|".join(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])"
                               for n in names), re.I)
    answers = {v for allowed in (*C.FIELDS.values(), P.EFFECTS) for v in allowed}
    n = 0
    for path in (x for x in crowd.rglob("*") if x.is_file()):
        hit = word.findall(path.read_text())
        assert not hit, (path.name, sorted(set(hit))[:5])
        if path.suffix == ".json":
            d = json.loads(path.read_text())
            values = [str(v).lower() for it in d["items"] for v in it.values()]
            assert not answers & set(values), path.name     # no label, reference or guess
            assert not re.search(r"speaker_effect|opposite_party|statement_type|kind|check",
                                 path.read_text(), re.I)
            assert not re.search(f'"({"|".join(answers)})"', path.read_text())
            n += 1
    assert n == 9 and not CR.blindness_problems(crowd)
    # The folder's own check catches a stray column, a stray file and a stray word.
    f = crowd / "data" / f"{CODES[0]}.json"
    d = json.loads(f.read_text())
    d["items"][0]["stratum"] = "x"
    f.write_text(json.dumps(d))
    (crowd / "sample.csv").write_text("passage_id,stratum\n")
    problems = CR.blindness_problems(crowd)
    assert len(problems) == 2 and "an item has fields" in problems[0]
    assert "does not belong" in problems[1]
    d["items"][0].pop("stratum")
    d["items"][0]["source"] = "Theme tone: headwind"
    f.write_text(json.dumps(d))
    assert "contains 'headwind'" in CR.blindness_problems(crowd)[0]


# -- collecting -------------------------------------------------------------------------

def test_collect_happy_path_writes_majority_labels_in_the_scoring_format(study):
    for code in CODES[:9]:
        _answer(study, code, comment="I am Jo from Acme" if code == CODES[0] else "")
    out = _collect(study)
    assert out["status"] == "adjudicated" and out["rule"]["met"]
    assert out["labellers"] == {"valid": 9} and not out["rejected"]
    assert out["alpha"]["all"] == dict.fromkeys(C.FIELDS, pytest.approx(1.0))
    folder = study.work / "crowd_labels"
    adj = pd.read_csv(folder / "adjudicated.csv", dtype=str, keep_default_na=False)
    sample = pd.read_csv(study.work / "sample.csv", dtype=str)
    assert list(adj["passage_id"]) == list(sample["passage_id"])
    # The columns version 1's tooling uses for adjudicated labels, in version 1's words.
    assert {"passage_id", "reason", *(f"adjudicated_{c}" for c in R.LABELS)} <= set(adj)
    for c, allowed in R.LABELS.items():
        assert set(adj[f"adjudicated_{c}"]) <= set(allowed)
    assert set(adj["n_valid"]) == {"3"} and set(adj["reason"]) == {"majority 3 of 3"}
    p = R._passages(study.work)
    gold = [V1._gold(t, r) for t, r in zip(p["text"], p["role"], strict=True)]
    assert list(adj["adjudicated_speaker_effect"]) == gold
    who = pd.read_csv(folder / "labellers.csv")
    assert (who["labelled"] == who["passages"]).all() and (who["checks_right"] == 4).all()
    report = (folder / "crowd_report.md").read_text()
    assert "**Yes.**" in report and "| all | 30 | 30 | 24 | PASS |" in report
    assert "| headwind | 10 | 10 | 7 | PASS |" in report and "role: buy_side (9)" in report
    # Codes only; no comment text, no passage text, no name in anything written.
    written = "".join(x.read_text() for x in folder.iterdir())
    assert "Acme" not in written and "Note " not in written
    assert CODES[0] in written and int(who["comments"].sum()) == 14
    assert "comment" not in pd.read_csv(folder / "labels_long.csv").columns


def test_a_malformed_file_is_rejected_whole_with_the_reason(study):
    good = [_answer(study, c) for c in CODES[1:9]]
    path = _answer(study, CODES[0])
    base = json.loads(path.read_text())
    stranger = json.loads(good[0].read_text())["labels"][0]["id"]

    def broken(change) -> str:
        d = json.loads(json.dumps(base))
        change(d)
        path.write_text(json.dumps(d))
        out = _collect(study, [path, *good])
        assert out["labellers"]["not returned"] == 1 and len(out["rejected"]) == 1
        assert out["rejected"][0][0] == path.name
        long = pd.read_csv(study.work / "crowd_labels" / "labels_long.csv")
        assert CODES[0] not in set(long["code"])               # not partially used
        return out["rejected"][0][1]

    assert "was not assigned to code" in broken(
        lambda d: d["labels"][0].update(id=stranger))
    assert "labelled twice" in broken(lambda d: d["labels"].append(d["labels"][0]))
    assert "speaker_effect is 'good'" in broken(
        lambda d: d["labels"][3].update(speaker_effect="good"))
    assert "statement_type is None" in broken(lambda d: d["labels"][3].pop("statement_type"))
    assert "was not given any passages" in broken(lambda d: d.update(code=CODES[9]))
    assert "not a valid code" in broken(lambda d: d.update(code="Jo Smith"))
    assert "another labelling folder" in broken(lambda d: d.update(pack="0000000000"))
    assert "protocol version" in broken(lambda d: d.update(protocol="1"))
    assert "no list of labels" in broken(lambda d: d.pop("labels"))
    path.write_text("{not json")
    assert _collect(study, [path, *good])["rejected"] == [(path.name, "not a JSON file")]
    # Two files under one code: neither is used.
    path.write_text(json.dumps(base))
    twin = study.tmp / "copy.json"
    twin.write_text(json.dumps(base))
    out = _collect(study, [path, twin, *good])
    assert len(out["rejected"]) == 2 and "two files carry code" in out["rejected"][0][1]
    assert "Rejected files" in (study.work / "crowd_labels" / "crowd_report.md").read_text()


def test_excluded_labeller_then_refusal_then_top_up_resolves(study, capsys):
    bad = CODES[0]
    _answer(study, bad, lambda it, lab: {**lab, "speaker_effect": "neutral"})
    for code in CODES[1:9]:
        _answer(study, code)
    out = _collect(study)
    folder = study.work / "crowd_labels"
    assert out["labellers"] == {"excluded": 1, "valid": 8}
    # Their 10 passages are one valid label short: 20 of 30 resolved, 24 required.
    assert out["status"] == "more_labels_needed" and not out["rule"]["met"]
    assert out["rule"]["groups"]["all"] == {"resolved": 20, "passages": 30, "required": 24}
    assert not (folder / "adjudicated.csv").exists()
    need = pd.read_csv(folder / "labels_needed.csv", dtype=str, keep_default_na=False)
    key = pd.read_csv(study.work / "crowd_assignment.csv", dtype=str)
    theirs = set(key.loc[(key["code"] == bad) & (key["kind"] == "passage"), "ref"])
    assert set(need["passage_id"]) == theirs and set(need["new_labels"]) == {"1"}
    assert set(need["valid_labels"]) == {"2"} and set(need["waiting_for"]) == {""}
    assert out["needed"] == {"passages": 10, "new_labels": 10, "new_labellers_at_least": 1,
                             "resend_to": [], "closed": 0}
    report = (folder / "crowd_report.md").read_text()
    assert "No: adjudicated.csv was not written" in report and f"| {bad} | 10 | 10 |" in report
    assert "10 more labels are needed from new labellers (at least 1 more people)" in report
    long = pd.read_csv(folder / "labels_long.csv")
    assert set(long.loc[long["code"] == bad, "labeller"]) == {"excluded"}
    # The command says so and exits non-zero.
    files = [str(x) for x in sorted(study.returned.glob("*.json"))]
    assert SCRIPT.main(["--work", str(study.work), "crowd-collect", *files]) == 2
    said = capsys.readouterr().out
    assert "NOT ENOUGH RESOLVED PASSAGES" in said and "labels_needed.csv" in said
    with pytest.raises(R.Refused, match="adjudicated.csv is missing"):
        CR.score(study.work, study.answers, log=quiet)

    # Top-up: one new code gets exactly those passages; nobody else's file changes.
    before = {x.name: x.read_bytes() for x in (study.work / "crowd" / "data").iterdir()}
    study.codes.write_text("\n".join(CODES[:10]))
    top = CR.pack(study.work, study.codes, cap=CAP, log=quiet)
    assert top["round"] == 2 and top["labellers"] == 1 and top["labels"] == 10
    data = study.work / "crowd" / "data"
    assert all((data / n).read_bytes() == b for n, b in before.items())
    key = pd.read_csv(study.work / "crowd_assignment.csv", dtype=str)
    new = key.loc[key["code"] == CODES[9]]
    assert set(new.loc[new["kind"] == "passage", "ref"]) == theirs
    assert set(new["round"]) == {"2"} and (new["kind"] == "check").sum() == 4
    with pytest.raises(R.Refused, match="run `crowd-collect`"):   # the list is now stale
        study.codes.write_text("\n".join([*CODES[:10], "BCDF2345"]))
        CR.pack(study.work, study.codes, cap=CAP, log=quiet)
    out = _collect(study)                       # invited, not back yet: waiting, not needed
    assert out["needed"]["resend_to"] == [CODES[9]] and out["needed"]["new_labels"] == 0
    _answer(study, CODES[9])
    out = _collect(study)
    assert out["status"] == "adjudicated" and out["labellers"] == {
        "excluded": 1, "valid": 9}
    adj = pd.read_csv(folder / "adjudicated.csv", dtype=str)
    assert set(adj["n_valid"]) == {"3"} and "unresolved" not in set(
        adj["adjudicated_speaker_effect"])


def test_incomplete_labeller_is_waited_for_not_excluded(study):
    for code in CODES[1:9]:
        _answer(study, code)
    _answer(study, CODES[0], upto=3)
    out = _collect(study)
    who = pd.read_csv(study.work / "crowd_labels" / "labellers.csv").set_index("code")
    assert who.at[CODES[0], "labelled"] <= 3
    assert who.at[CODES[0], "status"] == "incomplete" and out["needed"]["new_labels"] == 0
    assert out["needed"]["resend_to"] == [CODES[0]] and not out["rule"]["met"]


def test_a_tie_is_unresolved_for_that_field_only(study):
    key = pd.read_csv(study.work / "crowd_assignment.csv", dtype=str)
    rows = key.loc[key["kind"] == "passage"]
    pid = rows["ref"].iloc[0]
    order = list(rows.loc[rows["ref"] == pid, "code"])
    assert len(order) == 3
    split = dict(zip(order, ("benefits", "harmed", "cannot_tell"), strict=True))
    handle = {(r["code"], r["item_id"]) for r in rows.to_dict("records") if r["ref"] == pid}

    def change_for(code):
        return lambda it, lab: {**lab, "speaker_effect": split[code]} \
            if (code, it["id"]) in handle else lab
    for code in CODES[:9]:
        _answer(study, code, change_for(code) if code in split else (lambda it, lab: lab))
    out = _collect(study)
    assert out["status"] == "adjudicated"                    # 29 of 30 is enough
    adj = pd.read_csv(study.work / "crowd_labels" / "adjudicated.csv", dtype=str,
                      keep_default_na=False).set_index("passage_id")
    assert adj.at[pid, "adjudicated_speaker_effect"] == C.UNRESOLVED
    assert adj.at[pid, "reason"] == "no majority among 3"
    assert adj.at[pid, "votes_speaker_effect"] == "benefits:1;cannot_tell:1;harmed:1"
    assert adj.at[pid, "adjudicated_opposite_party"] in ("yes", "no")   # still a majority
    assert (adj["adjudicated_speaker_effect"] == C.UNRESOLVED).sum() == 1
    assert sum(out["unresolved"]["speaker_effect"].values()) == 1
    assert sum(out["unresolved"]["opposite_party"].values()) == 0
    assert out["alpha"]["all"]["speaker_effect"] < 1 == out["alpha"]["all"]["opposite_party"]
    need = pd.read_csv(study.work / "crowd_labels" / "labels_needed.csv", dtype=str)
    assert list(need["passage_id"]) == [pid] and list(need["new_labels"]) == ["1"]


def test_majority_needs_more_than_half_of_at_least_three():
    assert C.majority(["a", "a", "b"]) == "a" and C.majority(["a", "b", "c"]) == C.UNRESOLVED
    assert C.majority(["a", "a"]) == C.UNRESOLVED            # fewer than 3 valid labels
    assert C.majority(["a", "a", "b", "b"]) == C.UNRESOLVED  # 2 of 4 is not more than half
    assert C.majority(["a", "a", "a", "b"]) == "a"
    assert C.majority(["a", "a", "b", "b", "c"]) == C.UNRESOLVED
    assert C.majority(["a", "b", "a", "c", "a"]) == "a"
    lab = pd.DataFrame([{"passage_id": "p", "code": c, "speaker_effect": "cannot_tell",
                         "opposite_party": "cannot_tell", "statement_type": "none"}
                        for c in "xyz"])
    adj = C.adjudicate(["p", "q"], lab).set_index("passage_id")
    assert adj.at["p", "adjudicated_speaker_effect"] == "unclear"     # version 1's word
    assert adj.at["q", "adjudicated_speaker_effect"] == C.UNRESOLVED
    assert adj.at["q", "reason"] == "fewer than 3 valid labels (0)"
    # A passage with five labellers and no majority can get no more: closed.
    need = C.labels_needed(adj.reset_index(), {}, {"q": 5})
    assert need.to_dict("records") == [{"passage_id": "q", "valid_labels": 0,
                                        "waiting_for": "", "new_labels": 0, "closed": True}]
    rule = C.resolved_rule(adj.reset_index(), {"p": "headwind", "q": "tailwind"})
    assert not rule["met"] and rule["groups"]["all"] == {
        "resolved": 1, "passages": 2, "required": 2}


# -- Krippendorff's alpha ---------------------------------------------------------------

def test_alpha_on_hand_computed_cases():
    # Three passages, two labels each: aa, ab, bb. Coincidences: aa 2, ab 1, ba 1, bb 2;
    # n = 6, both labels used 3 times. Observed disagreement 2/6; expected
    # (36 - 9 - 9) / (6 * 5) = 0.6; alpha = 1 - (1/3) / 0.6 = 4/9.
    assert C.krippendorff_alpha([["a", "a"], ["a", "b"], ["b", "b"]]) == pytest.approx(4 / 9)
    # Unequal numbers of labellers: aaa, ab, bbbb. A passage with m labels contributes
    # each of its ordered pairs with weight 1/(m-1): aa 6/2 = 3; ab and ba 1 each;
    # bb 12/3 = 4. n = 9 (a 4, b 5). Observed 2/9; expected (81 - 16 - 25) / 72 = 5/9;
    # alpha = 1 - (2/9) / (5/9) = 0.6.
    units = [["a"] * 3, ["a", "b"], ["b"] * 4]
    assert C.krippendorff_alpha(units) == pytest.approx(0.6)
    assert C.krippendorff_alpha(units + [["a"], []]) == pytest.approx(0.6)  # nothing to pair
    assert C.krippendorff_alpha([["a", "a"], ["b", "b"], ["c", "c", "c"]]) == 1.0
    # Systematic disagreement is below zero: ab, ab -> observed 1, expected 4/6.
    assert C.krippendorff_alpha([["a", "b"], ["a", "b"]]) == pytest.approx(-0.5)
    assert C.krippendorff_alpha([["a", "a"], ["a", "a"]]) is None      # one label only
    assert C.krippendorff_alpha([["a"], ["b"]]) is None


def test_alpha_matches_the_published_examples():
    def units(*coders):
        return [[v for v in col if v != "*"] for col in zip(*coders, strict=True)]
    # Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability", example C:
    # four observers, twelve units, missing data; nominal alpha = 0.743.
    assert C.krippendorff_alpha(units("12332141 2***".replace(" ", ""),
                                      "123322412 5*3".replace(" ", ""),
                                      "*3332342251*", "123324412 51*".replace(" ", ""))
                                ) == pytest.approx(0.743, abs=5e-4)
    # The three-coder, fifteen-unit example widely reproduced from Krippendorff's
    # worked computation (e.g. Wikipedia, "Krippendorff's alpha"): nominal alpha = 0.691.
    assert C.krippendorff_alpha(units("*****34121133*3", "1*213343*******",
                                      "**2134 4*21133*4".replace(" ", ""))
                                ) == pytest.approx(0.691, abs=5e-4)


def test_alpha_is_close_to_kappa_for_two_readers_and_never_much_above():
    """What the protocol says about the floor: with two readers who label everything,
    1 - alpha = (1 - pi)(n - 1)/n for Scott's pi over n labels, and kappa >= pi."""
    rng = random.Random(3)
    a = [rng.choice(P.EFFECTS) for _ in range(300)]
    b = [x if rng.random() < 0.75 else rng.choice(P.EFFECTS[:2]) for x in a]
    alpha = C.krippendorff_alpha(zip(a, b, strict=True))
    kappa = P.cohen_kappa(a, b)
    counts = pd.Series(a + b).value_counts() / 600
    agree = sum(x == y for x, y in zip(a, b, strict=True)) / 300
    pi = (agree - (counts ** 2).sum()) / (1 - (counts ** 2).sum())
    assert 1 - alpha == pytest.approx((1 - pi) * 599 / 600)
    assert kappa >= pi and abs(alpha - kappa) < 0.02


# -- scoring ----------------------------------------------------------------------------

def test_crowd_score_end_to_end_and_hand_edits_are_refused(study):
    for code in CODES[:9]:
        _answer(study, code)
    _collect(study)
    R.run(study.work, study.answers, create=V1.Fake(), confirm_cost=True, log=quiet)
    out = CR.score(study.work, study.answers, n_boot=200, log=quiet)
    assert out["verdict"] == "pass" and out["protocol_version"] == "2"
    assert out["n"] == out["n_scored"] == 30 and out["n_unresolved"] == 0
    assert out["alpha"] == pytest.approx(1.0) and all(out["criteria"].values())
    text = (study.work / "result_v2.md").read_text()
    assert "## Verdict: PASS" in text and "ECONOMIC_IMPACT_PILOT_V2.md" in text
    rows = [x for x in text.splitlines() if x.startswith(("| i", "| v"))]
    assert [x.split(" | ")[0] for x in rows] == ["| i", "| ii", "| iii", "| iii", "| iv",
                                                 "| v", "| vi"]
    assert all(x.rstrip(" |").endswith("PASS") for x in rows)
    assert "Labeller alpha, effect on the company | >= 0.60" in text
    for threshold in (">= 0.10 and 95% interval above zero", ">= 0.80", "<= 2%", "<= 15%"):
        assert threshold in text                    # version 1's thresholds, unchanged
    assert "kappa" not in text.lower() and not (study.work / "result.md").exists()
    assert "text" not in pd.read_csv(study.work / "predictions.csv").columns
    # Version 1's `score` is untouched and still wants its own reader sheets.
    with pytest.raises(R.Refused, match="reader sheets are not fully labelled"):
        R.score(study.work, study.answers, log=quiet)
    target = study.work / "crowd_labels" / "adjudicated.csv"
    target.write_text(target.read_text().replace("harmed", "benefits", 1))
    with pytest.raises(R.Refused, match="not edited by hand"):
        CR.score(study.work, study.answers, log=quiet)


def test_score_leaves_out_unresolved_and_cannot_tell_and_gates_on_alpha():
    n = 60
    gold = ["benefits", "harmed", "neutral"] * 20
    labels = pd.DataFrame({"passage_id": [f"p{i}" for i in range(n)],
                           "symbol": [f"C{i % 15}" for i in range(n)],
                           "adjudicated": gold, "hard_case": [i % 4 == 0 for i in range(n)]})
    labels.loc[:5, "adjudicated"] = C.UNRESOLVED
    labels.loc[6:8, "adjudicated"] = "unclear"
    preds = pd.DataFrame({"passage_id": labels["passage_id"], "extraction": gold,
                          "baseline": ["neutral"] * n})
    out = C.score(labels, preds, 0.72, n_boot=200)
    assert (out["n"], out["n_scored"], out["n_unresolved"], out["n_unclear"]) == (60, 51, 6, 3)
    assert out["verdict"] == "pass" and out["extraction"]["macro_f1"] == 1.0
    assert out["extraction_invalid_share"] == 0                  # over all 60, as version 1
    preds.loc[:5, "extraction"] = P.INVALID                      # on unresolved passages
    out = C.score(labels, preds, 0.72, n_boot=200)
    assert out["extraction_invalid_share"] == pytest.approx(0.1)
    assert not out["criteria"]["v_spans_verbatim"] and out["verdict"] == "fail"
    assert out["extraction"]["macro_f1"] == 1.0                  # but not scored against
    for alpha in (0.59, None):
        out = C.score(labels, preds, alpha, n_boot=200)
        assert out["verdict"] == "labels_not_good_enough" and "difference" not in out
    assert C.score(labels, preds, 0.60, n_boot=200)["criteria"]["i_labeller_agreement"]


def test_existing_commands_still_parse_and_new_ones_are_listed(capsys):
    with pytest.raises(SystemExit) as e:
        SCRIPT.main(["--help"])
    assert e.value.code == 0
    text = capsys.readouterr().out
    for cmd in ("export", "sample", "run", "score", "status", "crowd-codes", "crowd-pack",
                "crowd-collect", "crowd-score"):
        assert cmd in text
    src = Path(C.__file__).read_text() + Path(CR.__file__).read_text()
    assert "anthropic" not in src and "requests" not in src and "urllib" not in src
