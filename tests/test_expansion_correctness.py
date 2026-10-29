"""Coverage expansions: a theme is only published with companies it can buy, its
basket starts when it is published, and a release never contains invalid JSON
(docs/MONITOR.md, "Universe changes" and "Repairing state")."""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from themegraph.config import Issuer, UniverseConfig
from themegraph.monitor import refresh as M
from themegraph.monitor.config import load_monitor
from themegraph.monitor.coverage import Coverage
from themegraph.monitor.export import _dump
from themegraph.monitor.profiles import load_profile
from themegraph.monitor.registry import Registry

TECH = load_profile("tech-v2")
NEW_SEGMENTS = ("app_software", "security", "data_cloud")


def _covered_from(day):
    def hook(ctx, as_of):
        u = ctx["universe"]
        iss = [i.model_copy(update={"covered_from": day}) if i.segment in NEW_SEGMENTS
               else i for i in u.issuers]
        return {"universe": u.model_copy(update={"issuers": iss})}
    return hook


def _strict(path):
    def bad(name):
        raise ValueError(name)
    return json.loads(path.read_text(), parse_constant=bad)


def _release(site):
    rel = json.loads((site / "data" / "latest.json").read_text())["path"]
    return site / "data" / rel


@pytest.fixture(scope="module")
def expansion(tmp_path_factory):
    """Software companies join coverage on 1 Aug 2021. The first refresh runs that
    day (month-ends through 31 Jul); the second runs three months late, on 1 Nov,
    and catches up on Aug, Sep and Oct."""
    root = tmp_path_factory.mktemp("expansion")
    day = dt.date(2021, 8, 1)
    out = []
    for as_of in (day, dt.date(2021, 11, 1)):
        st = M.refresh(root / "state", root / "site", fixtures=True, as_of=as_of,
                       log=lambda *a: None, profile=TECH, inputs_hook=_covered_from(day))
        reg = json.loads((root / "state" / "registry.json").read_text())
        out.append({"status": st, "reg": reg, "release": _release(root / "site")})
    return out


def test_companies_not_yet_covered_cannot_publish_a_theme(expansion):
    """Before: the newly-covered route counted companies whose coverage starts
    later, published their theme with an empty basket, valued it as status "ok"
    with NaN, and wrote JSON a browser cannot parse."""
    first = expansion[0]
    assert first["status"]["ok"], first["status"]["errors"]
    for t in first["reg"]["themes"].values():
        assert t["route"] != "newly_covered"
        for v in t["versions"]:
            assert v["constituents"], (t["id"], v["version"])
    rel = first["release"]
    _strict(rel / "index.json")
    for f in (rel / "themes").glob("*.json"):
        doc = _strict(f)
        if doc["performance"]["status"] == "ok":
            assert doc["performance"]["constituents"]


def test_late_refresh_buys_a_newly_covered_theme_after_publication(expansion):
    """Before: a month-end processed more than live_window_days late gave the
    theme a backdated entry at that month-end, although the route promises the
    basket starts when the theme is published."""
    second = expansion[1]
    assert second["status"]["ok"], second["status"]["errors"]
    now = second["status"]["checked_at"]
    nc = [t for t in second["reg"]["themes"].values() if t["route"] == "newly_covered"]
    assert nc
    for t in nc:
        assert t["published_at"] == now and t["basis"] == "live"
        assert t["newly_covered"]["live_expansion"] is True
        assert t["published_snapshot"] < now  # the evidence month stays recorded
        entries = [v["entry_after"] for v in t["versions"]]
        assert entries[0] == now and entries == sorted(entries)
        doc = _strict(second["release"] / "themes" / f"{t['id']}.json")
        perf = doc["performance"]
        if perf["status"] == "ok":
            assert perf["first_entry"] > now[:10]
            assert {r["basis"] for r in perf["series"]} == {"live"}


def test_reconstructed_history_is_not_moved_to_publication():
    """In a build with history (coverage dates before the monitor went live),
    newly-covered themes keep the reconstructed month-end entry."""
    reg, th, cov = _registry(covered_from=dt.date(2020, 1, 1), go_live="2021-06-01T00:00:00+00:00")
    reg._publish(th, _cluster("ABCD"), "2020-06-30 23:59:59+00:00", "newly_covered",
                 "2021-06-01T00:00:00+00:00", load_monitor(), {}, cov)
    assert th["basis"] == "reconstructed"
    assert th["versions"][0]["entry_after"] == "2020-06-30 23:59:59+00:00"


# -- registry rules (unit) -----------------------------------------------------------

def _cluster(members):
    return SimpleNamespace(exposure={s: 1 / len(members) for s in members})


def _registry(covered_from=None, go_live="2021-01-01T00:00:00+00:00", tmp=None):
    import tempfile
    reg = Registry(tmp or tempfile.mkdtemp())
    reg.data["go_live"] = go_live
    iss = [Issuer(symbol=s, segment="x", role="core", covered_from=covered_from)
           for s in "ABCDEF"]
    cov = Coverage(UniverseConfig(issuers=iss, benchmarks=["SPY"], sectors={"x": "X"}), {}, 12)
    th = {"id": "th-1", "published": False, "versions": [], "pending": None,
          "newly_covered": {"new_companies": list("ABCD")}}
    return reg, th, cov


def test_publication_basket_revised_during_catch_up_is_never_backdated():
    m = load_monitor()
    now = "2021-11-01T23:59:59+00:00"
    reg, th, cov = _registry(covered_from=dt.date(2021, 8, 1),
                             go_live="2021-08-01T23:59:59+00:00")
    reg._publish(th, _cluster("ABCD"), "2021-08-31 23:59:59+00:00", "newly_covered", now,
                 m, {}, cov)
    assert th["versions"][0]["entry_after"] == now
    for ts in ("2021-09-30 23:59:59+00:00", "2021-10-31 23:59:59+00:00"):
        reg._version(th, _cluster("ABCE"), ts, now, m, {}, cov)
    # Two confirmations of a new set within the same refresh: the publication basket,
    # never held, is revised; nothing is bought before publication.
    assert len(th["versions"]) == 1
    v = th["versions"][0]
    assert [c["symbol"] for c in v["constituents"]] == list("ABCE")
    assert v["entry_after"] == now and v["reason"] == "published"
    assert v["revised_before_entry"] == "2021-10-31 23:59:59+00:00"


def test_basket_invariants():
    m = load_monitor()
    reg, th, cov = _registry(covered_from=dt.date(2022, 1, 1))
    with pytest.raises(ValueError, match="no company in coverage"):
        reg._add_version(th, _cluster("AB"), "2021-06-30 23:59:59+00:00",
                         "2021-07-01T00:00:00+00:00", False, {}, cov, reason="published")
    reg, th, cov = _registry()
    th["versions"].append({"entry_after": "2021-07-01T00:00:00+00:00", "constituents": [
        {"symbol": "A"}, {"symbol": "B"}]})
    with pytest.raises(ValueError, match="before the basket it replaces"):
        reg._add_version(th, _cluster("AC"), "2021-06-30 23:59:59+00:00",
                         "2021-07-02T00:00:00+00:00", False, {}, cov, reason="x")
    # A theme whose companies all left coverage keeps its basket: no empty proposal.
    reg, th, cov = _registry()
    th.update(published=True, published_at="2021-01-01T00:00:00+00:00",
              versions=[{"version": 1, "created_at": "x", "entry_after": "2021-01-01",
                         "constituents": [{"symbol": "A"}, {"symbol": "B"}]}])
    cov.issuers["A"] = cov.issuers["A"].model_copy(update={"covered_until": dt.date(2021, 1, 2)})
    cov.issuers["B"] = cov.issuers["B"].model_copy(update={"covered_until": dt.date(2021, 1, 2)})
    for ts in ("2021-02-28 23:59:59+00:00", "2021-03-31 23:59:59+00:00"):
        reg._version(th, _cluster("AB"), ts, "2021-04-01T00:00:00+00:00", m, {}, cov)
    assert len(th["versions"]) == 1 and th["pending"] is None


def test_existing_empty_basket_is_reported_not_valued():
    from themegraph.monitor.baskets import theme_performance
    th = {"versions": [{"version": 1, "entry_after": "2021-01-01", "constituents": []}]}
    perf = theme_performance(th, None, ["SPY"], None)
    assert perf["status"] == "unavailable" and perf["reason"] == "empty_basket"


def test_non_finite_numbers_never_reach_a_release(tmp_path):
    with pytest.raises(ValueError, match="non-finite number at /performance/returns/1m"):
        _dump(tmp_path / "x.json", {"performance": {"returns": {"1m": float("nan")}}})
    assert not (tmp_path / "x.json").exists()


def test_invalid_release_keeps_the_last_good_one(tmp_path):
    st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                   as_of=dt.date(2021, 6, 30), log=lambda *a: None)
    assert st["ok"]
    good = (tmp_path / "site" / "data" / "latest.json").read_text()

    def poison(reg, d, mcfg, box, _value=M._value):
        out = _value(reg, d, mcfg, box)
        box["docs"][0]["performance"]["returns"] = {"1m": float("inf")}
        return out
    orig = M._value
    M._value = poison
    try:
        st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                       as_of=dt.date(2021, 7, 1), log=lambda *a: None)
    finally:
        M._value = orig
    assert not st["ok"] and "export" in st["errors"]
    assert (tmp_path / "site" / "data" / "latest.json").read_text() == good
    rel = tmp_path / "site" / "data" / "releases"
    assert not [p for p in rel.iterdir() if p.name.startswith(".staging")]
