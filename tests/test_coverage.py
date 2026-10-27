"""Universe configuration and coverage changes (docs/MONITOR.md, "Universe changes")."""

import datetime as dt
import json

import pandas as pd
import pytest

from themegraph.config import Issuer, UniverseChange, UniverseConfig, check_universe
from themegraph.monitor import refresh as M
from themegraph.monitor.coverage import Coverage

ADDED = {"NET1", "NET2"}
ADD_DATE = dt.date(2021, 7, 15)


def _u(**kw):
    base = {"issuers": [Issuer(symbol="A", segment="memory", role="core"),
                        Issuer(symbol="B", segment="power", role="adjacent",
                               covered_from=dt.date(2021, 3, 1))],
            "benchmarks": ["SPY"], "sectors": {"memory": "Semis", "power": "Power"}}
    base.update(kw)
    return UniverseConfig(**base)


def test_comparable_needs_coverage_at_both_dates_and_enough_history():
    first = {"A": pd.Timestamp("2019-01-10"), "B": pd.Timestamp("2018-01-10")}
    cov = Coverage(_u(), first, 12)
    t, base = pd.Timestamp("2021-06-30"), pd.Timestamp("2021-03-31")
    assert cov.comparable("A", t, base)
    assert not cov.comparable("B", t, pd.Timestamp("2021-02-28"))  # not covered at base
    assert cov.comparable("B", t, base)                             # history is back-filled
    short = Coverage(_u(), {"A": pd.Timestamp("2021-01-10")}, 12)
    assert not short.comparable("A", t, base)                       # history too short
    assert cov.sector == {"A": "Semis", "B": "Power"}
    s = cov.summary(pd.Timestamp("2021-04-15"))
    assert s["n_companies"] == 2 and s["recently_added"] == ["B"]


def test_universe_validation():
    with pytest.raises(ValueError, match="without a sector"):
        check_universe(_u(sectors={"memory": "Semis"}))
    with pytest.raises(ValueError, match="covered_from"):
        check_universe(_u(version="2", changes=[UniverseChange(
            version="2", date=dt.date(2021, 4, 1), added=["B"])]))
    with pytest.raises(ValueError, match="latest change"):
        check_universe(_u(version="3", changes=[UniverseChange(
            version="2", date=dt.date(2021, 3, 1), added=["B"])]))
    check_universe(_u(version="2", changes=[UniverseChange(
        version="2", date=dt.date(2021, 3, 1), added=["B"])]))


def _without(d, as_of):
    u = d["universe"]
    keep = [i for i in u.issuers if i.symbol not in ADDED]
    return {"universe": u.model_copy(update={"issuers": keep}),
            "prices": d["prices"].loc[~d["prices"]["symbol"].isin(ADDED)],
            "docs": d["docs"].loc[~d["docs"]["symbol"].isin(ADDED)]}


def _added(d, as_of):
    u = d["universe"]
    iss = [i.model_copy(update={"covered_from": ADD_DATE}) if i.symbol in ADDED else i
           for i in u.issuers]
    ch = UniverseChange(version="fixture-2", date=ADD_DATE, added=sorted(ADDED))
    return {"universe": u.model_copy(update={"issuers": iss, "version": "fixture-2",
                                             "changes": [*u.changes, ch]})}


def test_adding_companies_is_not_adoption_and_rewrites_nothing(tmp_path):
    state, site = tmp_path / "state", tmp_path / "site"
    quiet = {"log": lambda *a: None, "fixtures": True}
    s1 = M.refresh(state, site, as_of=dt.date(2021, 6, 30), inputs_hook=_without, **quiet)
    reg1 = json.loads((state / "registry.json").read_text())
    s2 = M.refresh(state, site, as_of=dt.date(2021, 12, 31), inputs_hook=_added, **quiet)
    reg2 = json.loads((state / "registry.json").read_text())
    assert s1["ok"] and s2["ok"], (s1["errors"], s2["errors"])

    for k, a in reg1["themes"].items():  # history and earlier versions untouched
        b = reg2["themes"][k]
        assert b["measures"][:len(a["measures"])] == a["measures"]
        assert b["versions"][:len(a["versions"])] == a["versions"]
        assert all(not ADDED & {c["symbol"] for c in v["constituents"]} for v in a["versions"])

    seen = False
    for t in reg2["themes"].values():
        for m in t["measures"]:
            # Until the 3-month baseline is on or after the addition, the added
            # companies are excluded; afterwards the comparison is like for like.
            if not "2021-07" <= m["t"] < "2021-10":
                continue
            assert not ADDED & set(m.get("comparable_issuers", []))
            assert not ADDED & set(m.get("new_issuers_3m") or [])
            if ADDED & set(m.get("recently_added", [])):
                seen = True
                # growth uses comparable companies only: the added text is not counted
                assert m["share_comparable"] is None or m["share_comparable"] <= m["share"] + 1e-9
    assert seen, "the added companies should show up as recently added in some theme"

    idx = json.loads((site / "data" / json.loads(
        (site / "data" / "latest.json").read_text())["path"] / "index.json").read_text())
    assert idx["universe"]["version"] == "fixture-2"
    assert not ADDED & set(idx["universe"]["recently_added"])  # added > 3 months ago
    assert idx["universe"]["changes"][-1]["added"] == sorted(ADDED)
