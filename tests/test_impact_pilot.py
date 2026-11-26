"""Economic-impact pilot (experimental/impact_pilot.py): offline, no model, no data."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from themegraph.experimental import impact_pilot as P
from themegraph.monitor import tone as T

DOC = Path(__file__).resolve().parents[1] / "docs" / "experiments" / "ECONOMIC_IMPACT_PILOT.md"

# The protocol's worked examples (synthetic; no real company or event):
# id -> (text, role, hard-case tags, phrase-list label, reference effect on the speaker)
EXAMPLES = {
    "S1": ("Supply of advanced substrates is no longer constrained, so we shipped everything "
           "customers ordered this quarter, and we are not seeing any weakness in industrial "
           "orders.", "management_remarks", {"negation"}, "harmed", "benefits"),
    "S2": ("The inventory correction at our distributors was less severe than last quarter, "
           "and the decline in orders is easing.", "management_remarks", {"comparative"},
           "harmed", "harmed"),
    "S3": ("The shortage of high-capacity memory let us raise contract prices again this "
           "quarter; several customers told us they have delayed server builds because of the "
           "shortfall in supply.", "management_answer", {"cross_party"}, "harmed", "benefits"),
    "S4": ("Our foundry suppliers pushed through another round of price increases, which our "
           "own customers would not accept, so gross margin fell two points.",
           "management_answer", {"cross_party"}, "neutral", "harmed"),
    "S5": ("Export controls, a downturn in the semiconductor industry or the loss of a major "
           "customer could materially and adversely affect our business, results of operations "
           "and financial condition.", "filing", {"risk_boilerplate"}, "harmed", "neutral"),
    "S6": ("If tariffs were imposed on imported networking equipment, we would expect to pass "
           "most of the cost on to customers, although some orders might be delayed.",
           "management_answer", {"hypothetical"}, "harmed", "neutral"),
    "S7": ("Data-centre demand accelerated and we had a record quarter in optical components, "
           "but consumer remained challenging and we took a write-down on handset inventory; in "
           "total, revenue and margin were both above our guidance.", "management_remarks",
           {"mixed"}, "neutral", "benefits"),
    "S8": ("Given the pricing pressure everyone is talking about in legacy nodes and the "
           "weakness in China, how should we think about utilisation in the second half?",
           "analyst_question", {"analyst_question"}, "neutral", "neutral"),
    "S9": ("We do not expect the strong demand we saw in the first half to continue, and we are "
           "guiding revenue lower by about ten percent for the fourth quarter.",
           "management_remarks", {"negation"}, "benefits", "harmed"),
    "S10": ("Competitors such as Norvale Devices have announced restructuring and layoffs; our "
            "own bookings and headcount were unchanged in the quarter.", "management_remarks",
            set(), "harmed", "neutral"),
}
PLAIN = "We opened a second design centre and moved the finance team to a new building."


# -- hard-case rules and the protocol ---------------------------------------------------

@pytest.mark.parametrize("key", sorted(EXAMPLES))
def test_worked_examples_tags_and_baseline(key):
    text, role, tags, baseline, _ = EXAMPLES[key]
    assert P.hard_case_tags(text, role) == tags
    assert P.baseline_label(text, role) == baseline


def test_protocol_holds_every_worked_example_and_the_declared_numbers():
    doc = " ".join(DOC.read_text().split())
    for key, (text, *_rest) in EXAMPLES.items():
        assert f"**{key}," in doc and " ".join(text.split()) in doc, key
    for number in (f"{P.KAPPA_FLOOR:.2f}", f"{P.MIN_F1_GAIN:.2f}", f"{P.MIN_PRECISION:.2f}",
                   f"{P.MAX_INVALID_SHARE:.0%}", f"{P.MAX_UNCLEAR_SHARE:.0%}",
                   str(P.SAMPLE_SEED), f"{P.N_BOOT:,}"):
        assert number in doc, number
    assert "to be filled from the current price list before execution" in doc
    assert "$" not in doc   # no price is quoted


def test_rules_do_not_fire_on_plain_text_and_use_role_only_where_declared():
    assert P.hard_case_tags(PLAIN) == set()
    risk = EXAMPLES["S5"][0]
    assert "risk_boilerplate" in P.hard_case_tags(risk)              # role unknown: text rule
    assert "risk_boilerplate" not in P.hard_case_tags(risk, "management_remarks")
    assert P.hard_case_tags(PLAIN, "analyst_question") == {"analyst_question"}
    assert "negation" in P.hard_case_tags("We haven't seen any cancellations this quarter.")
    assert "comparative" in P.hard_case_tags("Pricing pressure has started to moderate.")
    assert "comparative" in P.hard_case_tags("Demand was not as strong as we had hoped.")


def test_module_calls_no_model():
    src = Path(P.__file__).read_text()
    assert "anthropic" not in src and "client" not in src
    P.PROMPT.format(company="X", role="filing", date="2026-01-01", passage="text")
    assert P.PROMPT.rstrip().endswith("</passage>")   # instructions come before the passage


# -- baseline ---------------------------------------------------------------------------

def test_baseline_follows_tone_scoring():
    texts = [e[0] for e in EXAMPLES.values()] + [PLAIN, "Strong demand and record revenue."]
    sign, _ = T.score(texts)
    names = {-1: "harmed", 0: "neutral", 1: "benefits"}
    for t, s in zip(texts, sign, strict=True):
        assert P.baseline_label(t, "filing") == names[int(s)]
        assert P.baseline_label(t) == names[int(s)]
    q = EXAMPLES["S8"][0]
    assert P.baseline_label(q, "analyst_question") == "neutral"       # as in the monitor
    assert P.baseline_label(q, "analyst_question", score_analyst_questions=True) == "harmed"
    assert P.baseline_label(q, "transcript:qa") == "harmed"           # a management answer


# -- sample -----------------------------------------------------------------------------

ROLES = ("management_remarks", "management_answer", "filing", "analyst_question")


def _pool(sectors=4, per_sector=3, drop=lambda r: False) -> tuple[pd.DataFrame, dict]:
    texts = [e[0] for e in EXAMPLES.values()] + [PLAIN, PLAIN + " It was a quiet quarter."]
    rows, themes = [], {}
    for stratum in P.STRATA:
        for th in ("A", "B"):
            tid = f"{stratum}-{th}"
            themes[tid] = stratum
            for ci in range(sectors * per_sector):
                sym = f"C{ci:02d}"
                for k, text in enumerate(texts):
                    for j in range(2):   # every text in two roles per company
                        r = {"passage_id": f"{tid}:{sym}:{k}:{j}", "theme_id": tid,
                             "symbol": sym, "sector": f"sector{ci % sectors}",
                             "role": ROLES[(k + ci + 2 * j) % 4], "text": text,
                             "available_at": pd.Timestamp("2026-10-01", tz="UTC")
                             - pd.Timedelta(days=(ci * 7 + k) % 300)}
                        if not drop(r):
                            rows.append(r)
    themes["unknown-theme"] = "unknown"
    return pd.DataFrame(rows), themes


SMALL = {"n": 30, "hard_per_tag": 1, "analyst_per_stratum": 1}


def test_sample_is_deterministic_and_independent_of_row_order():
    p, themes = _pool()
    a = P.draw_sample(p, themes, seed=7, **SMALL)
    b = P.draw_sample(p.sample(frac=1.0, random_state=3), themes, seed=7, **SMALL)
    pd.testing.assert_frame_equal(a, b)
    c = P.draw_sample(p, themes, seed=8, **SMALL)
    assert set(a["passage_id"]) != set(c["passage_id"])


def test_full_sample_meets_strata_quotas_and_caps():
    p, themes = _pool(sectors=8, per_sector=5)
    s = P.draw_sample(p, themes)
    assert len(s) == 300 and s["passage_id"].is_unique
    assert s["stratum"].value_counts().to_dict() == dict.fromkeys(P.STRATA, 100)
    assert s.groupby(["symbol", "theme_id"]).size().max() <= 3
    hard = s.loc[s["selected_as"] == "hard_case"]
    assert len(hard) == 72
    for stratum in P.STRATA:
        h = hard.loc[hard["stratum"] == stratum]
        assert h["quota_tag"].value_counts().to_dict() == {
            "analyst_question": 6, **dict.fromkeys(P.TEXT_TAGS, 3)}
        g = s.loc[(s["stratum"] == stratum) & (s["selected_as"] == "general")]
        assert g["role"].value_counts().to_dict() == {
            "management_remarks": 26, "management_answer": 25, "filing": 25}
        assert g["sector"].nunique() == 8                    # spread across sectors
    # every hard case carries the tag it was chosen for; analyst questions only via the quota
    assert all(q in t.split(";") for q, t in zip(hard["quota_tag"], hard["hard_case_tags"],
                                                 strict=True))
    assert (s["role"] == "analyst_question").sum() == 18
    assert set(hard.loc[hard["quota_tag"] == "risk_boilerplate", "role"]) == {"filing"}
    assert themes[s["theme_id"].iloc[0]] == s["stratum"].iloc[0]


def test_cap_per_company_and_theme_is_enforced():
    p, themes = _pool()
    s = P.draw_sample(p, themes, seed=1, max_per_company_theme=1, **SMALL)
    assert s.groupby(["symbol", "theme_id"]).size().max() == 1 and len(s) == 30


def test_shortfall_fails_loudly_and_reports_every_cell():
    p, themes = _pool(drop=lambda r: r["theme_id"].startswith("tailwind")
                      and r["role"] == "filing")
    with pytest.raises(P.SampleShortfall) as e:
        P.draw_sample(p, themes, seed=7, **SMALL)
    cells = {(s["stratum"], s["cell"]): (s["wanted"], s["found"]) for s in e.value.shortfalls}
    assert cells == {("tailwind", "filing"): (1, 0), ("tailwind", "risk_boilerplate"): (1, 0)}
    assert "tailwind/filing: wanted 1, found 0" in str(e.value)
    # a cap that cannot be met is also a shortfall, never a smaller sample
    few, themes = _pool(sectors=1, per_sector=1)
    with pytest.raises(P.SampleShortfall):
        P.draw_sample(few, themes, seed=7, **SMALL)


def test_sample_uses_only_the_last_twelve_months_and_known_columns():
    p, themes = _pool()
    old = p.assign(passage_id="old:" + p["passage_id"],
                   available_at=pd.Timestamp("2025-06-01", tz="UTC"))
    s = P.draw_sample(pd.concat([p, old]), themes, seed=7, as_of="2026-10-01", **SMALL)
    assert not s["passage_id"].str.startswith("old:").any()
    with pytest.raises(ValueError, match="missing columns: sector"):
        P.draw_sample(p.drop(columns="sector"), themes, seed=7, **SMALL)
    with pytest.raises(ValueError, match="cannot be split"):
        P.draw_sample(p, themes, seed=7, n=31)


def test_theme_tones_reads_the_latest_present_month():
    reg = [{"id": "th1", "measures": [{"present": True, "tone": {"direction": "headwind"}},
                                      {"present": True, "tone": {"direction": "tailwind"}},
                                      {"present": False}]},
           {"id": "th2", "measures": [{"present": True, "tone": {"direction": "unknown"}}]},
           {"id": "th3", "measures": [{"present": True, "tone": None}]}]
    assert P.theme_tones(reg) == {"th1": "tailwind"}


# -- answers ----------------------------------------------------------------------------

def _impact(**kw) -> dict:
    return {"driver": "memory shortage", "party_type": "speaker", "party_name": "",
            "effect": "benefits", "channel": "pricing", "direction": "improving",
            "statement_type": "observed",
            "evidence_span": "let us raise contract prices again this quarter", **kw}


S3 = EXAMPLES["S3"][0]
GOOD = {"insufficient": False, "impacts": [
    _impact(),
    _impact(party_type="unnamed_group", party_name="customers", effect="harmed",
            channel="supply_capacity", direction="deteriorating",
            evidence_span="they have delayed server builds because of the shortfall in supply")]}


def test_valid_answer_is_accepted_and_reduced_to_labels():
    assert P.validate_answer(GOOD, S3) == []
    assert P.validate_answer(GOOD, S3.replace(" ", "\n  ")) == []     # white space only
    assert P.prediction(GOOD, S3) == {"speaker_effect": "benefits", "opposite_party": True,
                                      "statement_type": "observed", "problems": []}
    assert P.validate_answer({"insufficient": True, "impacts": []}, S3) == []
    assert P.prediction({"insufficient": True, "impacts": []}, S3)["speaker_effect"] == "unclear"
    none = P.prediction({"insufficient": False, "impacts": []}, S3)
    assert (none["speaker_effect"], none["statement_type"]) == ("neutral", "none")


def test_paraphrased_span_is_rejected_and_scored_invalid():
    bad = {"insufficient": False, "impacts": [
        _impact(evidence_span="allowed us to raise contract prices again this quarter")]}
    assert P.validate_answer(bad, S3) == ["impact 1: evidence span is not verbatim in the "
                                          "passage"]
    assert P.prediction(bad, S3)["speaker_effect"] == P.INVALID
    joined = {"insufficient": False, "impacts": [
        _impact(evidence_span="The shortage of high-capacity memory because of the shortfall")]}
    assert P.validate_answer(joined, S3)                               # two parts joined


def test_schema_problems_are_reported():
    def problems(a):
        return " | ".join(P.validate_answer(a, S3))

    assert "not an object" in problems([])
    assert "more than 3" in problems({"insufficient": False, "impacts": [_impact()] * 4})
    assert "insufficient is true" in problems({"insufficient": True, "impacts": [_impact()]})
    assert "effect is not an allowed" in problems(
        {"insufficient": False, "impacts": [_impact(effect="good")]})
    assert "wrong fields" in problems({"insufficient": False, "impacts": [{"driver": "x"}]})
    assert "named company does not appear" in problems({"insufficient": False, "impacts": [
        _impact(party_type="named_company", party_name="Norvale Devices")]})
    assert "party_name must be empty" in problems(
        {"insufficient": False, "impacts": [_impact(party_name="us")]})
    assert "evidence span missing" in problems(
        {"insufficient": False, "impacts": [_impact(evidence_span=" ")]})
    assert "exactly the keys" in problems({"insufficient": False, "impacts": [], "note": "x"})


def test_risk_and_hypothetical_statements_are_no_effect_on_the_speaker():
    text = EXAMPLES["S5"][0]
    a = {"insufficient": False, "impacts": [_impact(
        driver="export controls", effect="harmed", channel="demand", direction="unclear",
        statement_type="risk", evidence_span="could materially and adversely affect our business")]}
    assert P.validate_answer(a, text) == []
    got = P.prediction(a, text)
    assert (got["speaker_effect"], got["statement_type"], got["opposite_party"]) == \
        ("neutral", "risk", False)
    both = {"insufficient": False, "impacts": [_impact(), _impact(effect="harmed")]}
    assert P.speaker_effect(both) == "unclear"                         # good and bad, no net


def test_named_companies_are_split_by_the_coverage_list_and_cache_key_is_stable():
    assert P.stored_party_type("named_company", "Norvale Devices", {"Norvale Devices"}) == \
        "named_covered_company"
    assert P.stored_party_type("named_company", "Norvale Devices", set()) == \
        "named_other_company"
    assert P.stored_party_type("speaker", "", {"x"}) == "speaker"
    k = P.cache_key("text", "model-a")
    assert k == P.cache_key("text", "model-a") and len(k) == 64
    assert len({k, P.cache_key("text.", "model-a"), P.cache_key("text", "model-b"),
                P.cache_key("text", "model-a", prompt_version=2),
                P.cache_key("text", "model-a", schema_version=2)}) == 5


# -- scoring ----------------------------------------------------------------------------

B, H, N, U = "benefits", "harmed", "neutral", "unclear"
GOLD = [B, B, H, H, N, N]
PRED = [B, H, H, H, N, B]


def test_metrics_on_a_hand_computed_case():
    m = P.class_metrics(GOLD, PRED)
    c = m["classes"]
    assert (c[B]["tp"], c[B]["fp"], c[B]["fn"]) == (1, 1, 1)
    assert (c[B]["precision"], c[B]["recall"], c[B]["f1"]) == (0.5, 0.5, 0.5)
    assert c[H]["precision"] == pytest.approx(2 / 3) and c[H]["recall"] == 1.0
    assert c[H]["f1"] == pytest.approx(0.8)
    assert c[N]["precision"] == 1.0 and c[N]["recall"] == 0.5
    assert c[N]["f1"] == pytest.approx(2 / 3)
    assert m["macro_f1"] == pytest.approx((0.5 + 0.8 + 2 / 3) / 3)
    assert m["accuracy"] == pytest.approx(4 / 6)
    # observed 4/6, chance (2*2 + 2*3 + 2*1)/36 = 1/3  ->  kappa 0.5
    assert P.cohen_kappa(GOLD, PRED) == pytest.approx(0.5)
    assert P.cohen_kappa(GOLD, GOLD) == 1.0
    never = P.class_metrics([B, H], [N, N])["classes"][B]
    assert never["precision"] is None and never["f1"] == 0.0
    # an invalid answer is wrong for every class
    assert P.class_metrics([B], [P.INVALID])["macro_f1"] == 0.0


def test_bootstrap_is_reproducible_and_clustered_by_company():
    rng = np.random.default_rng(0)
    gold = rng.choice([B, H, N], size=120)
    worse = np.where(rng.random(120) < 0.5, gold, rng.choice([B, H, N], size=120))
    better = np.where(rng.random(120) < 0.9, gold, N)
    firms = [f"C{i % 30}" for i in range(120)]
    a = P.bootstrap_f1_difference(gold, worse, better, firms, n_boot=500, seed=11)
    assert a == P.bootstrap_f1_difference(gold, worse, better, firms, n_boot=500, seed=11)
    assert a["ci95"] != P.bootstrap_f1_difference(gold, worse, better, firms, n_boot=500,
                                                  seed=12)["ci95"]
    point = P.class_metrics(gold, better)["macro_f1"] - P.class_metrics(gold, worse)["macro_f1"]
    assert a["difference"] == pytest.approx(point)
    assert a["ci95"][0] < a["difference"] < a["ci95"][1] and a["n_companies"] == 30
    # one company holds everything: every resample is the same sample, so no spread
    one = P.bootstrap_f1_difference(gold, worse, better, ["C"] * 120, n_boot=50, seed=1)
    assert one["ci95"] == pytest.approx([point, point])
    same = P.bootstrap_f1_difference(gold, better, better, firms, n_boot=50, seed=1)
    assert same["difference"] == 0 and same["ci95"] == [0.0, 0.0]


def _study(extraction=None, reader_b=None):
    gold = [B, H, N] * 20
    labels = pd.DataFrame({
        "passage_id": [f"p{i}" for i in range(60)], "symbol": [f"C{i % 15}" for i in range(60)],
        "reader_a": gold, "reader_b": reader_b or gold, "adjudicated": gold,
        "hard_case": [i < 12 for i in range(60)]})
    preds = pd.DataFrame({"passage_id": labels["passage_id"], "baseline": [N] * 60,
                          "extraction": extraction or gold})
    return labels, preds


def test_score_applies_the_declared_criteria():
    r = P.score(*_study(), n_boot=200)
    assert r["verdict"] == "pass" and r["passed"] and all(r["criteria"].values())
    assert r["kappa"] == 1.0 and r["n"] == r["n_scored"] == 60
    assert r["extraction"]["macro_f1"] == 1.0
    assert r["baseline"]["macro_f1"] == pytest.approx(0.5 / 3)        # only "neutral" is right
    assert r["difference"]["difference"] == pytest.approx(1 - 0.5 / 3)
    assert r["hard_case"]["n"] == 12
    assert r == P.score(*_study(), n_boot=200)

    # two invalid answers in 60 is over the 2% ceiling, and each is a wrong answer
    bad = [B, H, N] * 20
    bad[0] = bad[1] = P.INVALID
    r = P.score(*_study(extraction=bad), n_boot=200)
    assert r["verdict"] == "fail" and not r["criteria"]["v_spans_verbatim"]
    assert r["extraction"]["classes"][B]["recall"] == pytest.approx(19 / 20)

    # too many "unclear" answers
    shy = [U if i < 12 else g for i, g in enumerate([B, H, N] * 20)]
    r = P.score(*_study(extraction=shy), n_boot=200)
    assert r["extraction_unclear_share"] == 0.2 and not r["criteria"]["vi_unclear_share"]

    # no better than the phrase lists: criterion ii fails, the lists stay
    r = P.score(*_study(extraction=[N] * 60), n_boot=200)
    assert r["verdict"] == "fail" and not r["criteria"]["ii_macro_f1_gain"]
    assert not r["criteria"]["iii_precision_harmed"]                   # never predicted


def test_score_refuses_to_judge_on_poor_labels_or_missing_predictions():
    noisy = [N, B, H] * 20                                             # readers never agree
    r = P.score(*_study(reader_b=noisy), n_boot=100)
    assert r["verdict"] == "labels_not_good_enough" and not r["passed"]
    labels, preds = _study()
    with pytest.raises(ValueError, match="no prediction"):
        P.score(labels, preds.iloc[:-1])
    with pytest.raises(ValueError, match="missing columns: adjudicated"):
        P.score(labels.drop(columns="adjudicated"), preds)


def test_unclear_reference_passages_are_reported_but_not_scored():
    labels, preds = _study()
    labels.loc[:5, ["reader_a", "reader_b", "adjudicated"]] = U
    r = P.score(labels, preds, n_boot=100)
    assert r["n"] == 60 and r["n_scored"] == 54
    assert r["adjudicated_unclear_share"] == pytest.approx(0.1)
