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
    assert p["n"] == 24 and p["median_excess"] < 0 and p["in_expected_direction"] >= 20
    assert res["supported"] and p["p_value"] < 0.05
    # Hand check of one event: equal-weight, buy and hold, against SPY.
    k = FT._counted(reg["headwind_log"], book)[0]
    a, z = book.cal[k["_i"]], book.cal[k["_i"] + 126]
    want = np.mean([book.carry[s][z] / book.carry[s][a] - 1 for s in k["basket"]]) - (
        book.carry["SPY"][z] / book.carry["SPY"][a] - 1)
    assert FT._excess(k, book, 126, "SPY") == pytest.approx(want)
    # More events arrive later: the stored read does not change.
    more = {"headwind_log": reg["headwind_log"] + _headwinds(book, 5, start=200, prefix="W")}
    again = FT.read("headwind", more, book, tmp_path, "2027-01-01T00:00:00+00:00")
    assert again == res and FT.progress(more, book, tmp_path)[0]["status"] == "read"
    assert "Supported" in FT.report(FT.progress(more, book, tmp_path))


def test_the_claim_can_fail(tmp_path):
    book = _book()
    reg = {"headwind_log": _headwinds(book, 24, prefix="W")}  # these beat the market
    res = FT.read("headwind", reg, book, tmp_path, "2026-01-01T00:00:00+00:00")
    assert not res["supported"] and res["primary"]["median_excess"] > 0
