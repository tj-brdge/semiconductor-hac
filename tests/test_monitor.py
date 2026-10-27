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
    for asset in ("index.html", "app.js", "chart.js", "styles.css"):
        assert (r["site"] / asset).exists()


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


def test_public_themes_are_never_backdated_and_keep_a_live_period(two_refreshes):
    """The second refresh catches up six month-ends. Membership changes it finds for
    themes published by the first refresh take effect when decided, so the days
    after publication stay observed (live) and are not rewritten by hindsight."""
    themes = two_refreshes["reg2"]["themes"]
    first_go_live = two_refreshes["reg1"]["go_live"]
    old = [t for t in themes.values() if t["published"] and t["published_at"] == first_go_live]
    assert old
    for t in old:
        for v in t["versions"]:
            if v["created_at"] > t["published_at"]:
                assert v["entry_after"] == v["created_at"] and v["basis"] == "live"
    idx = json.loads((_release(two_refreshes["site"]) / "index.json").read_text())
    for r in idx["themes"]:
        if r["id"] in {t["id"] for t in old} and r["market"]["status"] == "ok":
            live = r["market"]["returns"]["live"]
            assert live and live["basis"] == "live" and live["start"] == first_go_live[:10]


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


def test_theme_failure_after_partial_processing_keeps_release_and_registry(
        two_refreshes, tmp_path, monkeypatch):
    import shutil

    from themegraph.monitor.registry import Registry
    state, site = tmp_path / "state", tmp_path / "site"
    shutil.copytree(two_refreshes["state"], state)
    shutil.copytree(two_refreshes["site"], site)
    pointer = (site / "data" / "latest.json").read_text()
    registry = (state / "registry.json").read_text()
    real, calls = Registry.process, []

    def flaky(self, t, *a, **k):  # the first new month-end succeeds, the second fails
        calls.append(t)
        if len(calls) == 2:
            raise RuntimeError("topic layer crashed")
        return real(self, t, *a, **k)
    monkeypatch.setattr(Registry, "process", flaky)
    st = M.refresh(state, site, fixtures=True, as_of=pd.Timestamp("2022-03-31").date(),
                   log=lambda *a: None)
    assert len(calls) == 2  # one month-end was applied in memory before the failure
    assert not st["ok"] and "topic layer crashed" in st["errors"]["themes"]
    assert set(st.get("skipped", {})) == {"valuation", "export"}
    assert (site / "data" / "latest.json").read_text() == pointer
    assert (state / "registry.json").read_text() == registry  # nothing half-saved
    status = json.loads((site / "data" / "status.json").read_text())
    assert status["published_release"] == json.loads(pointer)["release"]

    monkeypatch.setattr(Registry, "process", real)  # the next run catches up normally
    st = M.refresh(state, site, fixtures=True, as_of=pd.Timestamp("2022-03-31").date(),
                   log=lambda *a: None)
    assert st["ok"] and (site / "data" / "latest.json").read_text() != pointer
    reg = json.loads((state / "registry.json").read_text())
    assert reg["last_snapshot"].startswith("2022-03-31")
    assert len(reg["snapshots"]) == len(set(reg["snapshots"]))


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
    assert perf["returns"]["full_period"]["theme"] == pytest.approx(expect)
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
    assert perf["returns"]["full_period"]["theme"] == pytest.approx(
        50.0 / gone[1] - 1)


def test_cash_only_acquisition_is_valued_at_its_terms_not_reset_to_cost():
    gone = np.linspace(40, 44, len(CAL))
    gone[30:] = np.nan
    succ = {"G": Successor(symbol="ACQ", shares=0.0, cash=60.0, source="t")}
    perf = theme_performance(_theme((str(CAL[0]), ["G"])),
                             _book({"G": gone}, {"G": CAL[30].date()}, succ), ["SPY"], None)
    c = perf["constituents"][0]
    assert c["holding"] == "converted_cash" and c["price_status"] == "fresh"
    assert perf["returns"]["full_period"]["theme"] == pytest.approx(60.0 / gone[1] - 1)
    assert perf["coverage_latest"] == 1.0


def test_missing_successor_quotes_keep_the_last_value_and_count_as_stale():
    gone = np.linspace(40, 44, len(CAL))
    gone[30:] = np.nan
    succ_px = np.full(len(CAL), np.nan)
    succ_px[40:] = 100.0   # the successor is only quoted from session 40
    succ = {"G": Successor(symbol="S", shares=0.5, cash=0.0, source="t")}
    perf = theme_performance(_theme((str(CAL[0]), ["G"])),
                             _book({"G": gone, "S": succ_px}, {"G": CAL[30].date()}, succ),
                             ["SPY"], None)
    s = pd.DataFrame(perf["series"]).set_index("date")
    last_before = 100.0 * gone[29] / gone[1]
    # Entry is CAL[1], so series row i is CAL[i + 1]: rows 29..38 are CAL[30..39],
    # the sessions after the delisting and before the successor's first quote.
    gap = s.iloc[29:39]
    assert np.allclose(gap["theme"], last_before, atol=1e-3)       # not reset to 100
    assert (gap["coverage"] == 0).all()                            # and not fresh
    assert s["theme"].iloc[-1] == pytest.approx(100.0 * 50.0 / gone[1])


def test_stale_successor_quote_is_not_fresh_coverage():
    gone = np.linspace(40, 44, len(CAL))
    gone[30:] = np.nan
    succ_px = np.linspace(80, 90, len(CAL))
    succ_px[-10:] = np.nan  # successor stopped quoting 10 sessions ago
    succ = {"G": Successor(symbol="S", shares=0.5, cash=0.0, source="t")}
    perf = theme_performance(_theme((str(CAL[0]), ["G"])),
                             _book({"G": gone, "S": succ_px}, {"G": CAL[30].date()}, succ),
                             ["SPY"], None)
    c = perf["constituents"][0]
    assert c["price_status"] == "stale" and c["sessions_since_close"] == 10
    assert perf["coverage_latest"] == 0.0


def test_reconstructed_and_live_returns_are_separated_by_recorded_times():
    t = _theme((str(CAL[0]), ["A", "B"]))
    t["published_at"] = str(CAL[39])            # published (wall clock) at session 39
    t["versions"][0]["created_at"] = str(CAL[39])
    perf = theme_performance(t, _book(), ["SPY"], None)
    s = pd.DataFrame(perf["series"]).set_index("date")
    assert set(s["basis"].iloc[:39]) == {"reconstructed"}  # rows 0..38 = CAL[1..39]
    assert set(s["basis"].iloc[39:]) == {"live"}
    R = perf["returns"]
    assert R["reconstructed"]["end"] == R["live"]["start"] == str(CAL[39].date())
    assert R["full_period"]["basis"] == "mixed" and R["live"]["basis"] == "live"
    chained = (1 + R["reconstructed"]["theme"]) * (1 + R["live"]["theme"]) - 1
    assert chained == pytest.approx(R["full_period"]["theme"])
    # 20 live sessions: a 21-session month spans both kinds; 59 sessions: no 3m window
    assert R["1m"]["basis"] == "mixed" and R["3m"] is None


def test_a_backdated_version_is_not_counted_as_observed():
    # v2 decided at session 50 for a month-end at session 29 (a catch-up refresh):
    # its days before session 50 were not observed live, even after publication.
    t = _theme((str(CAL[0]), ["B"]), (str(CAL[29]), ["A", "B"]))
    t["published_at"] = str(CAL[10])
    t["versions"][0]["created_at"] = str(CAL[10])
    t["versions"][1]["created_at"] = str(CAL[50])
    perf = theme_performance(t, _book(), ["SPY"], None)
    s = pd.DataFrame(perf["series"]).set_index("date")["basis"]
    assert s.loc[str(CAL[40].date())] == "reconstructed"
    assert perf["returns"]["live"]["start"] == str(CAL[50].date())
