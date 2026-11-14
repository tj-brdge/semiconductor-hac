"""Forward tests: logged live, counted without overlap, read once
(monitor/forward_tests.py)."""

import numpy as np
import pandas as pd
import pytest

from themegraph.monitor import forward_tests as FT
from themegraph.monitor.baskets import PriceBook


def _book(T=400, lag=-0.0015):
    """SPY drifts up; L* stocks lag it every day, W* stocks beat it."""
    cal = pd.bdate_range("2024-01-02", periods=T, tz="UTC") + pd.Timedelta(hours=22)
    rng = np.random.default_rng(2)
    m = rng.normal(0.0004, 0.008, T)
    px = {"SPY": 100 * np.cumprod(1 + m)}
    for i in range(60):
        px[f"L{i}"] = 100 * np.cumprod(1 + m + lag + rng.normal(0, 0.004, T))
        px[f"W{i}"] = 100 * np.cumprod(1 + m - lag + rng.normal(0, 0.004, T))
    df = pd.DataFrame(px, index=cal)
    return PriceBook(df, "SPY", {c: "USD" for c in df}, None, None, 5)


def _event(book, day, basket, theme="th-1", **kw):
    t = str(book.cal[day].normalize() + pd.Timedelta(hours=12))
    return {"theme": theme, "snapshot": t, "recorded_at": t, "entry_after": t,
            "basket": basket, "benchmark": "SPY", "sector_benchmark": None, **kw}


def _headwinds(book, n, start=5, step=3, prefix="L"):
    return [_event(book, start + step * k, [f"{prefix}{2 * k}", f"{prefix}{2 * k + 1}"],
                   theme=f"th-{k}") for k in range(n)]


def test_progress_shows_counts_and_never_returns(tmp_path):
    book = _book()
    reg = {"headwind_log": _headwinds(book, 12),
           "forward_log": [{"test": "emergence", **_event(book, 300, ["W0", "W1"])}]}
    rows = {r["test"]: r for r in FT.progress(reg, book, tmp_path)}
    assert rows["headwind"]["logged"] == 12 and rows["headwind"]["complete"] == 12
    assert rows["headwind"]["status"] == "collecting" and rows["headwind"]["result"] is None
    assert rows["emergence"]["logged"] == 1 and rows["emergence"]["complete"] == 0
    assert rows["tailwind"]["logged"] == 0
    assert "median" not in FT.report(list(rows.values()))  # counts only
    with pytest.raises(RuntimeError, match="12 of 20"):
        FT.read("headwind", reg, book, tmp_path, "2026-01-01T00:00:00+00:00")
    assert not (tmp_path / "forward_reads.json").exists()


def test_overlapping_events_count_once():
    book = _book()
    evs = [_event(book, 10, ["L0", "L1", "L2", "L3"], theme="a"),
           _event(book, 40, ["L0", "L1", "L9"], theme="b"),    # 2 of 3 shared, 30 sessions on
           _event(book, 60, ["L0", "L7", "L8", "L9"], theme="c"),  # 1 of 4 shared with a
           _event(book, 150, ["L0", "L1", "L2", "L3"], theme="d")]  # same basket, 140 on
    assert [k["theme"] for k in FT._counted(evs, book)] == ["a", "c", "d"]


def test_a_test_is_read_once_and_the_result_is_kept(tmp_path):
    book = _book()
    reg = {"headwind_log": _headwinds(book, 24)}
    assert FT.progress(reg, book, tmp_path)[0]["status"] == "ready to read"
    res = FT.read("headwind", reg, book, tmp_path, "2026-01-01T00:00:00+00:00")
    p = res["primary"]
    assert p["n_valid"] == 24 and p["n_sign"] == 24 and p["n_ties"] == 0
    assert p["median_excess"] < 0 and p["wins"] >= 20 and p["p_value"] < 0.05
    assert "supported" not in res  # a single read decides nothing
    # Hand check of one event: equal-weight, buy and hold, against SPY.
    k = FT._counted(reg["headwind_log"], book)[0]
    a, z = book.cal[k["_i"]], book.cal[k["_i"] + 126]
    want = np.mean([book.carry[s][z] / book.carry[s][a] - 1 for s in k["basket"]]) - (
        book.carry["SPY"][z] / book.carry["SPY"][a] - 1)
    assert FT._excess(k, book, 126, "SPY")[0] == pytest.approx(want)
    # More events arrive later: the stored raw read does not change.
    stored = (tmp_path / "forward_reads.json").read_text()
    more = {"headwind_log": reg["headwind_log"] + _headwinds(book, 5, start=200, prefix="W")}
    again = FT.read("headwind", more, book, tmp_path, "2027-01-01T00:00:00+00:00")
    assert again == res and (tmp_path / "forward_reads.json").read_text() == stored
    row = FT.progress(more, book, tmp_path)[0]
    assert row["status"] == "read" and row["adjusted"] is None
    text = FT.report(FT.progress(more, book, tmp_path))
    assert "nominal one-sided p" in text and "Not available" in text
    assert "supported at" not in text.lower().replace("not supported", "")


def test_windows_without_a_usable_outcome_do_not_make_a_test_ready(tmp_path):
    """Before: 20 elapsed windows made a test ready even when some primary excess
    returns could not be computed."""
    book = _book()
    evs = _headwinds(book, 24)
    for e in evs[:6]:
        e["benchmark"] = "NOPE"  # no prices: the excess return cannot be computed
    row = FT.progress({"headwind_log": evs}, book, tmp_path)[0]
    assert row["complete"] == 24 and row["usable"] == 18 and row["status"] == "collecting"
    assert row["unusable"] == {"no prices for NOPE": 6}
    with pytest.raises(RuntimeError, match="18 of 20"):
        FT.read("headwind", {"headwind_log": evs}, book, tmp_path, "2026-01-01T00:00:00+00:00")
    assert "no prices for NOPE (6)" in FT.report([row])
    # Secondary results report their own unusable outcomes (no sector benchmark here).
    ok = FT.read("headwind", {"headwind_log": _headwinds(book, 24)}, book, tmp_path / "b",
                 "2026-01-01T00:00:00+00:00") if (tmp_path / "b").mkdir() is None else None
    assert ok["secondary_sector"]["n_valid"] == 0
    assert ok["secondary_sector"]["unusable"] == {"no benchmark recorded": 24}
    assert ok["secondary_63"]["n_valid"] == 24


def test_exact_ties_are_valid_outcomes_outside_the_sign_test():
    st = FT._stat([-0.02, -0.01, 0.0, 0.0, 0.03], "less")
    assert st["n_valid"] == 5 and st["n_ties"] == 2 and st["n_sign"] == 3 and st["wins"] == 2
    from scipy.stats import binomtest
    assert st["p_value"] == pytest.approx(binomtest(2, 3, 0.5, alternative="greater").pvalue)
    shown = FT._fmt(st)
    assert "2 of 3 non-tied outcomes" in shown and "5 valid, 2 exact ties" in shown
    assert FT._stat([0.0, 0.0], "greater")["p_value"] is None  # nothing to test


def _read(p):
    return {"read_at": "2027-01-01T00:00:00+00:00",
            "primary": {"sessions": 126, "p_value": p, "n_valid": 20, "n_ties": 0,
                        "n_sign": 20, "wins": 15, "median_excess": 0.01},
            "secondary_63": {"n_valid": 0}, "secondary_sector": {"n_valid": 0}}


def test_holm_needs_all_three_and_steps_down():
    assert FT.family({"headwind": _read(0.001)}) is None
    assert FT.family({"headwind": _read(0.001), "emergence": _read(0.04)}) is None
    fam = FT.family({"headwind": _read(0.01), "emergence": _read(0.04),
                     "tailwind": _read(0.03)})["tests"]
    # sorted 0.01, 0.03, 0.04 against 0.05/3, 0.05/2, 0.05: only the first passes
    assert fam["headwind"] == {"p_value": 0.01, "p_adjusted": 0.03, "supported": True}
    assert fam["tailwind"]["p_adjusted"] == 0.06 and not fam["tailwind"]["supported"]
    assert fam["emergence"]["p_adjusted"] == 0.06 and not fam["emergence"]["supported"]
    # A nominal pass that does not survive: 0.04 alone is below 5%, 3 x 0.04 is not.
    weak = FT.family({k: _read(0.04) for k in FT.TESTS})["tests"]
    assert all(v["p_adjusted"] == 0.12 and not v["supported"] for v in weak.values())
    strong = FT.family({k: _read(0.001) for k in FT.TESTS})["tests"]
    assert all(v["supported"] for v in strong.values())


def test_report_and_progress_show_the_family_result_only_when_complete(tmp_path):
    import json
    book = _book()
    reads = {"headwind": {**_read(0.01), "test": "headwind"}}
    (tmp_path / "forward_reads.json").write_text(json.dumps(reads))
    rows = FT.progress({}, book, tmp_path)
    assert [r["adjusted"] for r in rows] == [None, None, None]
    text = FT.report(rows)
    assert "Not available: emergence, tailwind not read yet" in text and "unadjusted" in text
    reads |= {"emergence": _read(0.04), "tailwind": _read(0.03)}
    (tmp_path / "forward_reads.json").write_text(json.dumps(reads))
    rows = {r["test"]: r for r in FT.progress({}, book, tmp_path)}
    assert rows["headwind"]["adjusted"]["supported"] and not rows["emergence"]["adjusted"][
        "supported"]
    text = FT.report(list(rows.values()))
    assert "headwind: nominal p = 0.01, adjusted p = 0.03: supported" in text
    assert "emergence: nominal p = 0.04, adjusted p = 0.06: not supported" in text
    assert "independent" in text  # the remaining assumption is stated


def test_the_claim_can_fail(tmp_path):
    book = _book()
    reg = {"headwind_log": _headwinds(book, 24, prefix="W")}  # these beat the market
    res = FT.read("headwind", reg, book, tmp_path, "2026-01-01T00:00:00+00:00")
    assert res["primary"]["median_excess"] > 0 and res["primary"]["p_value"] > 0.5
