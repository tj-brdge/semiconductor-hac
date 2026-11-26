"""Two reading aids added 2026-11-26 (docs/DECISIONS.md): why a basket's 3-month lead
changed, and how current the evidence behind a theme's status is. Neither changes a
classification or a status."""
import pandas as pd

from themegraph.monitor.baskets import TRAILING, market_history
from themegraph.monitor.export import _market_trend, observation


def _perf(theme, bench):
    days = pd.bdate_range("2020-01-01", periods=len(theme))
    return {"status": "ok", "series": [
        {"date": str(d.date()), "theme": float(t), "SPY": float(b), "coverage": 1.0,
         "basis": "reconstructed"} for d, t, b in zip(days, theme, bench, strict=True)]}, days


def test_an_old_bad_month_leaving_the_window_is_not_new_outperformance():
    n3, m = TRAILING["3m"], 21
    # Flat benchmark. The basket loses 10% in the first month, then matches the
    # benchmark exactly. One month later the bad month leaves the 3-month window.
    theme = [100.0] * 5 + [100 - 10 * (i + 1) / m for i in range(m)] + [90.0] * (n3 + m)
    perf, days = _perf(theme, [100.0] * len(theme))
    t0, t1 = days[5 + n3 - 1], days[5 + n3 - 1 + m]   # bad month inside, then outside
    hist = market_history(perf, [str(t0), str(t1)])
    tr = _market_trend(hist, "SPY", 5.0)
    assert tr["trend"] == "gaining" and tr["change_pp_1m"] > 5
    assert abs(tr["latest_month_pp"]) < 0.01          # nothing happened this month
    assert tr["left_month_pp"] < -5                   # a bad month left the window
    assert tr["driver"] == "window"


def test_new_relative_performance_is_named_as_such():
    n3, m = TRAILING["3m"], 21
    theme = [100.0] * (n3 + 6) + [100 + 8 * (i + 1) / m for i in range(m)]
    perf, days = _perf(theme, [100.0] * len(theme))
    hist = market_history(perf, [str(days[n3 + 5]), str(days[-1])])
    tr = _market_trend(hist, "SPY", 5.0)
    assert tr["trend"] == "gaining" and tr["driver"] == "latest_month"
    assert tr["latest_month_pp"] > 7 and abs(tr["left_month_pp"]) < 0.01
    # The two parts account for the change (compounding aside).
    assert abs(tr["latest_month_pp"] - tr["left_month_pp"] - tr["change_pp_1m"]) < 0.5


def test_the_classification_itself_is_unchanged():
    key = "excess_3m_vs_SPY"
    hist = [{"t": "a", key: 0.01}, {"t": "b", key: 0.07}]
    assert _market_trend(hist, "SPY", 5.0)["trend"] == "gaining"
    assert _market_trend([{"t": "a", key: 0.07}, {"t": "b", key: 0.01}], "SPY", 5.0)[
        "trend"] == "losing"
    assert _market_trend([{"t": "a", key: 0.01}, {"t": "b", key: 0.03}], "SPY", 5.0)[
        "trend"] == "steady"
    assert _market_trend(hist[:1], "SPY", 5.0) == {"trend": None}
    assert _market_trend(hist, "SPY", 5.0)["driver"] is None   # parts not recorded


def _th(*rows):
    return {"measures": [{"t": f"2026-{i + 1:02d}-28", **r} for i, r in enumerate(rows)]}


def test_missing_evidence_is_counted_apart_from_absence():
    P, A, U = {"present": True}, {"present": False, "observed": True}, {
        "present": False, "observed": False}
    o = observation(_th(P, P, U, U))
    assert o["months_unobserved"] == 2 and o["months_absent_since_present"] == 0
    assert not o["current"] and o["last_observed"] == "2026-02-28"
    o = observation(_th(P, A, A))                       # looked for and not found
    assert o["months_unobserved"] == 0 and o["months_absent_since_present"] == 2
    assert o["current"] and o["last_present"] == "2026-01-28"
    o = observation(_th(P, U, A))                       # evidence came back: current
    assert o["current"] and o["months_unobserved_since_present"] == 1
    assert observation(_th(P, P))["current"]
    assert observation({"measures": []})["last_observed"] is None
