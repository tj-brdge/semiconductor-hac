"""Offline tests of the experimental first-reported fundamentals logic, on small
synthetic SUB/NUM frames shaped like the SEC Financial Statement Data Sets."""

import io
import zipfile

import pandas as pd

from themegraph.data import edgar
from themegraph.experimental import fundamentals as F

CIK = 111


def sub_row(adsh, form, period, accepted, cik=CIK, filed=None, prevrpt="0"):
    return {"adsh": adsh, "cik": str(cik), "name": f"CO {cik}", "form": form, "period": period,
            "fy": "1999", "fp": "Q9",  # deliberately wrong: fy/fp must never be read
            "filed": filed or accepted[:10].replace("-", ""), "accepted": accepted,
            "prevrpt": prevrpt}


def num_row(adsh, tag, ddate, qtrs, value, uom="USD", segments="", coreg="",
            version="us-gaap/2024"):
    return {"adsh": adsh, "tag": tag, "version": version, "ddate": ddate, "qtrs": str(qtrs),
            "uom": uom, "segments": segments, "coreg": coreg, "value": str(value),
            "footnote": ""}


def run(subs, nums):
    sub = F.prepare_sub(pd.DataFrame(subs))
    num, info = F.prepare_num(pd.DataFrame(nums))
    facts = F.resolve_metrics(num, sub)
    first, rev = F.first_reported(facts, sub)
    return sub, first, rev, F.single_quarters(first, rev), info


def pick(df, **eq):
    for k, v in eq.items():
        df = df.loc[df[k] == (pd.Timestamp(v) if k == "period_end" else v)]
    return df


def test_accepted_time_matches_the_project_rule_and_month_end_rounding():
    s = pd.Series(["2025-01-30 16:05:00.0", "2025-07-30 18:10:00.0"])
    assert F.accepted_utc(s).tolist() == edgar.accepted_utc(s).tolist()
    assert F.accepted_utc(s)[0] == pd.Timestamp("2025-01-30 21:05", tz="UTC")
    got = F.month_end(pd.Series(["20240928", "20230930", "20250201", "20250126", "20241231"]))
    assert [d.date().isoformat() for d in got] == [
        "2024-09-30", "2023-09-30", "2025-01-31", "2025-01-31", "2024-12-31"]


def test_first_reported_beats_a_later_restated_comparative():
    subs = [sub_row("A-24Q1", "10-Q", "20240331", "2024-05-02 16:10:00.0"),
            sub_row("A-25Q1", "10-Q", "20250331", "2025-05-01 16:20:00.0")]
    nums = [num_row("A-24Q1", "Revenues", "20240331", 1, 1000),
            num_row("A-25Q1", "Revenues", "20250331", 1, 1300),
            num_row("A-25Q1", "Revenues", "20240331", 1, 950)]  # restated comparative
    _, first, rev, _, _ = run(subs, nums)
    f = pick(first, metric="revenue", period_end="2024-03-31").iloc[0]
    assert f["value"] == 1000 and f["adsh"] == "A-24Q1" and f["n_revisions"] == 1
    assert f["available_at"] == pd.Timestamp("2024-05-02 20:10", tz="UTC")
    assert bool(f["own_period"]) and not bool(f["first_is_comparative"])
    r = rev.iloc[0]
    assert len(rev) == 1 and r["kind"] == "later_comparative" and r["value"] == 950
    assert r["first_value"] == 1000 and r["change"] == -50 and r["rel_change"] == -0.05
    assert r["available_at"] == pd.Timestamp("2025-05-01 20:20", tz="UTC")


def test_unchanged_comparative_is_not_a_revision_and_repeat_counts_once():
    subs = [sub_row("A1", "10-Q", "20240331", "2024-05-02 16:10:00.0"),
            sub_row("A2", "10-Q", "20250331", "2025-05-01 16:20:00.0"),
            sub_row("A3", "10-K", "20251231", "2026-02-01 16:20:00.0")]
    nums = [num_row("A1", "InventoryNet", "20240331", 0, 500),
            num_row("A2", "InventoryNet", "20240331", 0, 500),
            num_row("A3", "InventoryNet", "20240331", 0, 500)]
    _, first, rev, _, _ = run(subs, nums)
    assert rev.empty and first.iloc[0]["n_filings"] == 3


def test_amendment_is_a_dated_revision_and_never_replaces_the_first_value():
    subs = [sub_row("K", "10-K", "20241231", "2025-02-20 16:30:00.0", prevrpt="1"),
            sub_row("KA", "10-K/A", "20241231", "2025-04-10 08:05:00.0")]
    nums = [num_row("K", "OperatingIncomeLoss", "20241231", 4, 400),
            num_row("KA", "OperatingIncomeLoss", "20241231", 4, 380)]
    _, first, rev, _, _ = run(subs, nums)
    f = pick(first, metric="operating_income").iloc[0]
    assert f["value"] == 400 and f["form"] == "10-K"
    assert f["available_at"] == pd.Timestamp("2025-02-20 21:30", tz="UTC")
    r = rev.iloc[0]
    assert (r["kind"], r["form"], r["value"], r["adsh"]) == ("amendment", "10-K/A", 380, "KA")
    assert r["available_at"] == pd.Timestamp("2025-04-10 12:05", tz="UTC")  # EDT
    assert bool(r["own_period"])


def test_q4_is_full_year_minus_nine_months_with_the_later_availability():
    subs = [sub_row("Q3", "10-Q", "20240930", "2024-11-01 16:00:00.0"),
            sub_row("FY", "10-K", "20241231", "2025-02-15 17:00:00.0")]
    nums = [num_row("Q3", "Revenues", "20240930", 3, 2700),
            num_row("Q3", "Revenues", "20240930", 1, 950),
            num_row("FY", "Revenues", "20241231", 4, 3800)]
    _, _, _, q, _ = run(subs, nums)
    q4 = pick(q, metric="revenue", period_end="2024-12-31").iloc[0]
    assert q4["value"] == 1100 and q4["method"] == "fy_minus_9m"
    assert q4["available_at"] == pd.Timestamp("2025-02-15 22:00", tz="UTC")
    assert (q4["adsh"], q4["adsh_prior"]) == ("FY", "Q3")
    q3 = pick(q, metric="revenue", period_end="2024-09-30").iloc[0]
    assert q3["value"] == 950 and q3["method"] == "reported"


def test_q4_uses_the_nine_month_figure_as_known_when_the_year_was_published():
    subs = [sub_row("Q3", "10-Q", "20240930", "2024-11-01 16:00:00.0"),
            sub_row("Q3A", "10-Q/A", "20240930", "2024-12-10 16:00:00.0"),
            sub_row("FY", "10-K", "20241231", "2025-02-15 17:00:00.0"),
            sub_row("NEXT", "10-Q", "20250930", "2025-11-01 16:00:00.0")]
    nums = [num_row("Q3", "Revenues", "20240930", 3, 2700),
            num_row("Q3A", "Revenues", "20240930", 3, 2600),   # known before the 10-K
            num_row("FY", "Revenues", "20241231", 4, 3800),
            num_row("NEXT", "Revenues", "20240930", 3, 2500)]  # published after: not used
    _, first, _, q, _ = run(subs, nums)
    assert pick(first, qtrs=3).iloc[0]["value"] == 2700
    assert pick(q, period_end="2024-12-31").iloc[0]["value"] == 3800 - 2600


def test_year_to_date_cash_flow_is_differenced_into_quarters():
    tag = "PaymentsToAcquirePropertyPlantAndEquipment"
    subs = [sub_row("Q1", "10-Q", "20240331", "2024-05-01 16:00:00.0"),
            sub_row("Q2", "10-Q", "20240630", "2024-08-01 16:00:00.0"),
            sub_row("Q3", "10-Q", "20240930", "2024-11-01 16:00:00.0"),
            sub_row("FY", "10-K", "20241231", "2025-02-15 16:00:00.0")]
    nums = [num_row("Q1", tag, "20240331", 1, 100), num_row("Q2", tag, "20240630", 2, 250),
            num_row("Q3", tag, "20240930", 3, 450), num_row("FY", tag, "20241231", 4, 700)]
    _, _, _, q, _ = run(subs, nums)
    capex = pick(q, metric="capex").set_index("period_end")
    assert capex["value"].tolist() == [100, 150, 200, 250]
    assert capex["method"].tolist() == ["reported", "ytd_diff", "ytd_diff", "fy_minus_9m"]
    assert capex.loc[pd.Timestamp("2024-06-30"), "available_at"] == pd.Timestamp(
        "2024-08-01 20:00", tz="UTC")
    # A cumulative figure with no shorter one before it gives no quarter (not a guess).
    _, _, _, q2, _ = run(subs[1:2], nums[1:2])
    assert pick(q2, metric="capex").empty


def test_tag_fallback_order_and_derived_gross_profit():
    subs = [sub_row("A", "10-Q", "20240331", "2024-05-01 16:00:00.0"),
            sub_row("B", "10-Q", "20240331", "2024-05-02 16:00:00.0", cik=222),
            sub_row("C", "20-F", "20231231", "2024-04-01 16:00:00.0", cik=333)]
    nums = [
        # A: both revenue tags: the earlier candidate wins; no GrossProfit tag: derived.
        num_row("A", "RevenueFromContractWithCustomerExcludingAssessedTax", "20240331", 1, 90),
        num_row("A", "Revenues", "20240331", 1, 100),
        num_row("A", "CostOfGoodsAndServicesSold", "20240331", 1, 60),
        # B: only the fallback tag, and a GrossProfit tag that beats the derivation.
        num_row("B", "RevenueFromContractWithCustomerExcludingAssessedTax", "20240331", 1, 80),
        num_row("B", "CostOfRevenue", "20240331", 1, 50),
        num_row("B", "GrossProfit", "20240331", 1, 31),
        # C: IFRS names.
        num_row("C", "Revenue", "20231231", 4, 7000, uom="EUR", version="ifrs/2023"),
        num_row("C", "CostOfSales", "20231231", 4, 4000, uom="EUR", version="ifrs/2023"),
    ]
    _, first, _, _, _ = run(subs, nums)
    a = pick(first, cik=CIK).set_index("metric")
    assert a.loc["revenue", "value"] == 100 and a.loc["revenue", "tag"] == "Revenues"
    assert a.loc["gross_profit", "value"] == 40
    assert a.loc["gross_profit", "tag"] == "derived:Revenues-CostOfGoodsAndServicesSold"
    b = pick(first, cik=222).set_index("metric")
    assert b.loc["revenue", "tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert b.loc["gross_profit", "value"] == 31 and b.loc["gross_profit", "tag"] == "GrossProfit"
    c = pick(first, cik=333).set_index("metric")
    assert c.loc["revenue", "tag"] == "Revenue" and c.loc["gross_profit", "value"] == 3000
    assert c.loc["gross_profit", "uom"] == "EUR"


def test_53_week_non_december_year_is_keyed_on_dates_not_fy_fp():
    # Fiscal year ends on the last Saturday of September: 2023-09-30 (53 weeks), then
    # 2024-09-28. Quarter ends 2023-12-30, 2024-03-30, 2024-06-29. fy/fp are wrong.
    subs = [sub_row("K23", "10-K", "20230930", "2023-11-03 18:05:00.0"),
            sub_row("Q1", "10-Q", "20231230", "2024-02-02 06:01:00.0"),
            sub_row("Q2", "10-Q", "20240330", "2024-05-03 06:01:00.0"),
            sub_row("Q3", "10-Q", "20240629", "2024-08-02 06:01:00.0"),
            sub_row("K24", "10-K", "20240928", "2024-11-01 06:01:00.0")]
    nums = [num_row("K23", "Revenues", "20230930", 4, 3830),
            num_row("Q1", "Revenues", "20231230", 1, 1196),
            num_row("Q2", "Revenues", "20240330", 1, 908),
            num_row("Q2", "Revenues", "20240330", 2, 2104),
            num_row("Q3", "Revenues", "20240629", 1, 858),
            num_row("Q3", "Revenues", "20240629", 3, 2962),
            num_row("K24", "Revenues", "20240928", 4, 3910),
            num_row("K24", "Revenues", "20230930", 4, 3830)]
    _, first, rev, q, _ = run(subs, nums)
    assert rev.empty
    rq = pick(q, metric="revenue").set_index("period_end")
    assert [d.date().isoformat() for d in rq.index] == [
        "2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30"]
    assert rq["value"].tolist() == [1196, 908, 858, 3910 - 2962]
    assert rq["method"].tolist() == ["reported", "reported", "reported", "fy_minus_9m"]
    # The prior year's annual figure is not mistaken for a quarter of this one.
    assert pd.Timestamp("2023-09-30") not in rq.index
    assert len(pick(first, qtrs=4)) == 2


def test_segment_coregistrant_and_custom_rows_are_ignored():
    subs = [sub_row("A", "10-Q", "20240331", "2024-05-01 16:00:00.0")]
    nums = [num_row("A", "Revenues", "20240331", 1, 1000),
            num_row("A", "Revenues", "20240331", 1, 400, segments="BusinessSegments=Memory;"),
            num_row("A", "Revenues", "20240331", 1, 700, coreg="SubsidiaryCo"),
            num_row("A", "InventoryNet", "20240331", 0, 55, segments="ProductOrService=Wafers;"),
            num_row("A", "OperatingIncomeLoss", "20240331", 1, 9, version="A")]
    _, first, _, _, info = run(subs, nums)
    assert first["metric"].tolist() == ["revenue"] and first.iloc[0]["value"] == 1000
    assert info["dropped_segment_or_coreg"] == 3 and info["dropped_custom_tags"] == 1
    assert info["has_segments_column"]


def test_two_values_for_one_fact_in_a_filing_are_dropped_not_guessed():
    nums = [num_row("A", "Revenues", "20240331", 1, 1000),
            num_row("A", "Revenues", "20240331", 1, 400)]
    frame = pd.DataFrame(nums).drop(columns=["segments"])  # an older file layout
    num, info = F.prepare_num(frame)
    assert num.empty and info["dropped_ambiguous_in_filing"] == 2
    assert not info["has_segments_column"]


def test_units_are_kept_apart():
    subs = [sub_row("F23", "20-F", "20231231", "2024-04-15 06:10:00.0"),
            sub_row("F24", "20-F", "20241231", "2025-04-15 06:10:00.0")]
    nums = [num_row("F23", "Revenue", "20231231", 4, 2_161_736, uom="TWD", version="ifrs/2023"),
            num_row("F23", "Revenue", "20231231", 4, 70_600, uom="USD", version="ifrs/2023"),
            num_row("F24", "Revenue", "20241231", 4, 2_894_308, uom="TWD", version="ifrs/2024"),
            num_row("F24", "Revenue", "20231231", 4, 2_161_736, uom="TWD", version="ifrs/2024"),
            num_row("F24", "Revenue", "20231231", 4, 65_900, uom="USD", version="ifrs/2024")]
    sub, first, rev, q, _ = run(subs, nums)
    f23 = pick(first, period_end="2023-12-31").set_index("uom")
    assert f23.loc["TWD", "value"] == 2_161_736 and f23.loc["USD", "value"] == 70_600
    # The convenience translation changed; the reporting-currency figure did not.
    assert rev["uom"].tolist() == ["USD"] and rev.iloc[0]["first_value"] == 70_600
    assert q.empty  # annual-only filer: no single quarters are invented
    s = F.company_summary([CIK], first, rev, q).set_index("metric").loc["revenue"]
    assert s["unit"] == "TWD" and s["other_units"] == "USD"
    assert s["n_annual"] == 2 and s["n_quarters"] == 0 and s["n_revisions"] == 0
    assert F.filer_types(sub, [CIK, 999]).to_dict() == {CIK: "20-F/40-F", 999: "none"}


def test_missing_metric_is_reported_missing_never_zero():
    subs = [sub_row("A", "10-Q", "20240331", "2024-05-01 16:00:00.0")]
    nums = [num_row("A", "Revenues", "20240331", 1, 1000)]  # no cost, no inventory
    _, first, rev, q, _ = run(subs, nums)
    assert set(first["metric"]) == {"revenue"}  # gross profit is not revenue minus nothing
    s = F.company_summary([CIK, 999], first, rev, q).set_index(["cik", "metric"])
    assert len(s) == 2 * len(F.METRICS)
    assert bool(s.loc[(CIK, "revenue"), "resolved"])
    for metric in ("gross_profit", "operating_income", "inventory", "capex"):
        row = s.loc[(CIK, metric)]
        assert not row["resolved"]
        assert pd.isna(row["n_quarters"]) and pd.isna(row["n_revisions"]) and pd.isna(row["tags"])
    assert not s.loc[999, "resolved"].any()


def test_first_value_seen_only_as_a_comparative_is_flagged():
    subs = [sub_row("Q25", "10-Q", "20250331", "2025-05-01 16:00:00.0")]
    nums = [num_row("Q25", "Revenues", "20240331", 1, 950)]
    _, first, _, _, _ = run(subs, nums)
    assert bool(first.iloc[0]["first_is_comparative"]) and not bool(first.iloc[0]["own_period"])


def test_timestamp_checks_and_tag_usage():
    subs = [sub_row("A", "10-Q", "20240331", "2024-05-01 16:00:00.0"),
            # Accepted Friday 18:10 Eastern, dated the following Monday: expected.
            sub_row("B", "10-K", "20231231", "2024-03-01 18:10:00.0", filed="20240304"),
            # Accepted the day after its filing date: not expected.
            sub_row("C", "10-Q", "20240630", "2024-08-02 09:00:00.0", filed="20240801")]
    sub = F.prepare_sub(pd.DataFrame(subs))
    stats, per = F.timestamp_checks(sub, max_lag_days=45)
    assert stats["filings"] == 3 and stats["accepted_same_day_as_filed"] == 1
    assert stats["accepted_before_filed_date"] == 1
    assert stats["accepted_before_filed_and_not_after_1730"] == 0
    assert stats["accepted_after_filed_date"] == 1
    assert per.set_index("adsh").loc["B", "lag_days"] == 61
    assert stats["share_lag_over_threshold"] == 1 / 3
    num, _ = F.prepare_num(pd.DataFrame([
        num_row("A", "Revenues", "20240331", 1, 1), num_row("C", "Revenues", "20240630", 1, 1),
        num_row("A", "RevenueNotFromContractWithCustomer", "20240331", 1, 1)]))
    use = F.tag_usage(num, sub).set_index("tag")
    assert use.loc["Revenues", "companies"] == 1 and use.loc["Revenues", "filings"] == 2
    assert use.loc["SalesRevenueNet", "companies"] == 0 and use.loc["SalesRevenueNet", "candidate"]
    assert not use.loc["RevenueNotFromContractWithCustomer", "candidate"]


def test_read_dataset_filters_a_zip_to_the_companies(tmp_path):
    sub = pd.DataFrame([sub_row("A", "10-Q", "20240331", "2024-05-01 16:00:00.0"),
                        sub_row("Z", "10-Q", "20240331", "2024-05-01 16:00:00.0", cik=999)])
    num = pd.DataFrame([num_row("A", "Revenues", "20240331", 1, 1000),
                        num_row("A", "Assets", "20240331", 0, 5),
                        num_row("Z", "Revenues", "20240331", 1, 7)])
    path = tmp_path / "2024q2.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, df in (("sub.txt", sub), ("num.txt", num)):
            buf = io.StringIO()
            df.to_csv(buf, sep="\t", index=False)
            zf.writestr(name, buf.getvalue())
    s, n = F.read_dataset(path, {CIK}, chunksize=2)
    assert s["adsh"].tolist() == ["A"] and n["tag"].tolist() == ["Revenues"]
    _, first, _, _, _ = run(s.to_dict("records"), n.to_dict("records"))
    assert first.iloc[0]["value"] == 1000
