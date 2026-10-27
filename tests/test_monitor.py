"""Theme monitor: inventory, persistence, idempotent refresh, basket method, feed."""

import json

import numpy as np
import pandas as pd
import pytest

from themegraph.config import Successor
from themegraph.monitor import refresh as M
from themegraph.monitor.baskets import PriceBook, theme_performance

# -- end to end on labelled fixture data ---------------------------------------------

@pytest.fixture(scope="module")
def two_refreshes(tmp_path_factory):
    root = tmp_path_factory.mktemp("monitor")
    state, site = root / "state", root / "site"
    s1 = M.refresh(state, site, fixtures=True, as_of=pd.Timestamp("2021-06-30").date(),
                   log=lambda *a: None)
    reg1 = json.loads((state / "registry.json").read_text())
    feed1 = json.loads((site / "data" / "latest.json").read_text())
    s2 = M.refresh(state, site, fixtures=True, as_of=pd.Timestamp("2021-12-31").date(),
                   log=lambda *a: None)
    reg2 = json.loads((state / "registry.json").read_text())
    return {"state": state, "site": site, "s1": s1, "s2": s2, "reg1": reg1, "reg2": reg2,
            "feed1": feed1}


def _release(site):
    p = json.loads((site / "data" / "latest.json").read_text())["path"]
    return site / "data" / p


def test_refresh_produces_inventory_with_both_routes(two_refreshes):
    r = two_refreshes
    assert r["s1"]["ok"] and r["s2"]["ok"], (r["s1"]["errors"], r["s2"]["errors"])
    idx = json.loads((_release(r["site"]) / "index.json").read_text())
    assert idx["fixture"] is True and idx["schema_version"] == 1
    routes = {t["route"] for t in idx["themes"]}
    assert {"emergence", "established"} <= routes
    for row in idx["themes"]:
        doc = json.loads((_release(r["site"]) / "themes" / f"{row['id']}.json").read_text())
        perf = doc["performance"]
        assert perf["status"] == "ok" and perf["series"][0]["theme"] == 100.0
        used = doc["basket_versions"][perf["current_version"] - 1]["constituents"]
        assert [c["symbol"] for c in perf["constituents"]] == [c["symbol"] for c in used]
        assert doc["evidence"]["latest"] and all("role" in q for q in doc["evidence"]["latest"])
        assert {"SPY", "SOXX"} <= set(perf["series"][-1])
    assert (r["site"] / "index.html").exists()


def test_second_refresh_extends_history_without_duplicates(two_refreshes):
    r1, r2 = two_refreshes["reg1"]["themes"], two_refreshes["reg2"]["themes"]
    pub1 = {k for k, t in r1.items() if t["published"]}
    assert pub1 <= {k for k, t in r2.items() if t["published"]}  # nobody disappears
    for k in pub1:
        a, b = r1[k], r2[k]
        n = len(a["measures"])
        assert b["measures"][:n] == a["measures"]  # history never rewritten
        assert b["versions"][:len(a["versions"])] == a["versions"]
        assert b["published_snapshot"] == a["published_snapshot"]
        assert len(b["measures"]) == n + 6  # six new month-ends, one row each
    snaps = two_refreshes["reg2"]["snapshots"]
    assert len(snaps) == len(set(snaps))


def test_rerun_with_same_data_changes_nothing(two_refreshes, tmp_path):
    state = two_refreshes["state"]
    before = (state / "registry.json").read_text()
    st = M.refresh(state, two_refreshes["site"], fixtures=True,
                   as_of=pd.Timestamp("2021-12-31").date(), log=lambda *a: None)
    assert st["ok"] and st["last_attempt"]["themes"]["detail"] == "no new month-end to process"
    assert (state / "registry.json").read_text() == before


def test_themes_stay_after_the_emergence_rule_stops_firing(two_refreshes):
    themes = two_refreshes["reg2"]["themes"]
    emerged = [t for t in themes.values() if t["published"] and t["route"] == "emergence"]
    assert emerged
    # Themes whose emergence flag is off in recent months are still listed.
    quiet = [t for t in themes.values() if t["published"]
             and not any(m.get("flagged") for m in t["measures"][-3:])]
    assert quiet
    idx = json.loads((_release(two_refreshes["site"]) / "index.json").read_text())
    listed = {r["id"] for r in idx["themes"]}
    assert {t["id"] for t in quiet} <= listed
    # The planted hype theme stops being discussed: kept, marked inactive or cooling.
    hype = [t for t in emerged if "metaverse" in " ".join(t["keywords"])
            or "virtual" in t["name"]]
    assert hype and hype[0]["status"] in ("inactive", "cooling")


def test_failed_refresh_keeps_last_release_and_reports_it(two_refreshes, monkeypatch):
    site = two_refreshes["site"]
    pointer = (site / "data" / "latest.json").read_text()

    def boom(*a, **k):
        raise RuntimeError("price feed down")
    monkeypatch.setattr(M, "_value", boom)
    st = M.refresh(two_refreshes["state"], site, fixtures=True,
                   as_of=pd.Timestamp("2021-12-31").date(), log=lambda *a: None)
    assert not st["ok"] and "price feed down" in st["errors"]["valuation"]
    assert (site / "data" / "latest.json").read_text() == pointer
    status = json.loads((site / "data" / "status.json").read_text())
    assert status["errors"]["valuation"] and status["published_release"]


# -- basket method -------------------------------------------------------------------

CAL = pd.bdate_range("2021-01-04", periods=60, tz="UTC") + pd.Timedelta(hours=22)


def _book(extra=None, delisted=None, successors=None):
    px = pd.DataFrame(index=CAL)
    px["SPY"] = np.linspace(100, 110, len(CAL))
    px["SOXX"] = np.linspace(100, 120, len(CAL))
    px["A"] = np.linspace(10, 20, len(CAL))   # doubles
    px["B"] = 50.0                            # flat
    for k, v in (extra or {}).items():
        px[k] = v
    venues = {c: "USD" for c in px}
    return PriceBook(px, "SPY", venues, delisted, successors, 5)


def _theme(*versions):
    return {"versions": [{"version": i + 1, "entry_after": after, "constituents":
                          [{"symbol": s, "exposure": 1.0, "role": "core"} for s in members]}
                         for i, (after, members) in enumerate(versions)]}


def test_equal_capital_buy_and_hold_not_averaged_prices():
    perf = theme_performance(_theme((str(CAL[0]), ["A", "B"])), _book(), ["SPY", "SOXX"], None)
    # A: 10 -> 20 (+100%), B flat: equal capital gives +50%, not (20+50)/(10+50)-1.
    a0 = 10 + (20 - 10) / (len(CAL) - 1)  # entry is the first session after CAL[0]
    expect = 0.5 * (20 / a0 - 1)
    assert perf["returns"]["since_publication"]["theme"] == pytest.approx(expect)
    assert perf["series"][0]["theme"] == 100.0
    c = {x["symbol"]: x for x in perf["constituents"]}
    assert c["B"]["contribution"] == pytest.approx(0.0)
    assert c["A"]["start_weight"] == 0.5


def test_later_added_member_counts_only_from_its_version():
    t = _theme((str(CAL[0]), ["B"]), (str(CAL[29]), ["A", "B"]))
    perf = theme_performance(t, _book(), ["SPY"], None)
    s = pd.DataFrame(perf["series"]).set_index("date")["theme"]
    assert s.iloc[:29].round(6).eq(100.0).all()  # B flat: no backfilled gain from A
    assert perf["current_version"] == 2 and s.iloc[-1] > 100.0


def test_missing_prices_are_visible_not_dropped():
    gap = np.linspace(30, 33, len(CAL))
    gap[-8:] = np.nan
    perf = theme_performance(_theme((str(CAL[0]), ["A", "C", "N"])),
                             _book({"C": gap, "N": np.nan}), ["SPY"], None)
    c = {x["symbol"]: x for x in perf["constituents"]}
    assert c["C"]["price_status"] == "stale" and c["C"]["sessions_since_close"] == 8
    assert c["N"]["price_status"] == "not_bought" and c["N"]["return_since_entry"] == 0
    assert perf["coverage_latest"] < 0.5
    assert len(perf["constituents"]) == 3  # nobody silently omitted


def test_acquired_member_converts_on_recorded_terms():
    gone = np.linspace(40, 44, len(CAL))
    gone[30:] = np.nan
    succ = {"G": Successor(symbol="B", shares=1.0, cash=0.0, source="t")}
    perf = theme_performance(_theme((str(CAL[0]), ["G"])),
                             _book({"G": gone}, {"G": CAL[30].date()}, succ), ["SPY"], None)
    c = perf["constituents"][0]
    assert c["holding"] == "converted" and c["price_status"] == "fresh"
    assert perf["returns"]["since_publication"]["theme"] == pytest.approx(
        50.0 / gone[1] - 1)
