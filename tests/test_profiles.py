"""Profiles: the semiconductor pilot and the technology pilot run side by side; the
technology pilot starts from a copy of the semiconductor registry and adding
companies must not manufacture emergence or cooling (docs/TECH_PILOT_V2.md)."""

import datetime as dt
import hashlib
import json

import pandas as pd
import pytest

from themegraph.config import load_universe
from themegraph.data.store import Store
from themegraph.ingest import load_documents, load_prices
from themegraph.monitor import refresh as M
from themegraph.monitor.compare import compare_profiles
from themegraph.monitor.config import load_monitor
from themegraph.monitor.profiles import effective, load_profile

SEMIS, TECH = load_profile("semis"), load_profile("tech-v2")
# The same pilot run as a live expansion of the semiconductor registry (fork mode):
# kept tested for future expansions that must keep another profile's theme IDs.
FORK = TECH.model_copy(update={"fork_from": "semis"})


def test_default_profile_is_the_original_configuration():
    from themegraph.config import CONFIG_DIR, ROOT
    assert SEMIS.universe == CONFIG_DIR / "universe.yaml"
    assert SEMIS.monitor == CONFIG_DIR / "monitor.yaml"
    assert SEMIS.state == ROOT / "monitor_state" and SEMIS.site == ROOT / "site"
    assert load_monitor(SEMIS.monitor).market.benchmarks == ["SPY", "SOXX"]
    assert not load_monitor(SEMIS.monitor).market.sector_benchmarks


def test_tech_universe_extends_the_pilot_without_changing_it():
    base, tech = load_universe(SEMIS.universe), load_universe(TECH.universe)
    old = {i.symbol: i for i in base.issuers}
    new = [i for i in tech.issuers if i.symbol not in old]
    assert all(i.model_dump() == old[i.symbol].model_dump()
               for i in tech.issuers if i.symbol in old)  # roles, dates untouched
    assert len(old) == 76 and 40 <= len(new) <= 60
    assert len({i.cik for i in new}) == len(new)  # one listing per company
    assert all(i.covered_from is None for i in new)  # built with full history
    assert tech.changes[-1].version == tech.version == "2"
    segs = {i.segment for i in new}
    assert segs == {"app_software", "security", "data_cloud"}
    assert all(sum(i.segment == s for i in new) >= 12 for s in segs)
    e = effective(TECH)
    assert e["universe_version"] == "2" and len(e["files"]["universe"]) == 2


def test_store_reads_are_limited_to_the_universe(tmp_path):
    from themegraph import synthetic as S
    big = S.synthetic_universe(tech=True)
    small = S.synthetic_universe()
    store = Store(tmp_path)
    docs = S.synthetic_documents(big)
    store.write("documents_transcripts", docs, "20220101T000000Z")
    prices = S.synthetic_prices(big)
    store.write("prices", prices, "20220101T000000Z")
    d = load_documents(store, universe=small)
    p = load_prices(store, universe=small)
    assert set(d["symbol"]) == {i.symbol for i in small.issuers}
    assert set(p["symbol"]) == {i.symbol for i in small.issuers} | {"SPY", "SOXX"}
    assert len(load_documents(store)) == len(docs)  # no universe: everything, as before


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("profiles")
    A, B = root / "semis", root / "tech"
    q = {"log": lambda *a: None, "fixtures": True}
    M.refresh(A / "state", A / "site", as_of=dt.date(2021, 6, 30), profile=SEMIS, **q)
    at_fork = json.loads((A / "state" / "registry.json").read_text())
    sha = hashlib.sha256((A / "state" / "registry.json").read_bytes()).hexdigest()
    tb = M.refresh(B / "state", B / "site", as_of=dt.date(2021, 12, 31), profile=FORK,
                   fork_state=A / "state", **q)
    sha_after = hashlib.sha256((A / "state" / "registry.json").read_bytes()).hexdigest()
    ta = M.refresh(A / "state", A / "site", as_of=dt.date(2021, 12, 31), profile=SEMIS, **q)
    return {"A": A, "B": B, "at_fork": at_fork, "fork_untouched": sha == sha_after,
            "ra": json.loads((A / "state" / "registry.json").read_text()),
            "rb": json.loads((B / "state" / "registry.json").read_text()),
            "ta": ta, "tb": tb}


def _index(site):
    rel = site / "data" / json.loads((site / "data" / "latest.json").read_text())["path"]
    return json.loads((rel / "index.json").read_text())


def test_fork_keeps_ids_baskets_and_history_and_leaves_the_source_alone(runs):
    assert runs["ta"]["ok"] and runs["tb"]["ok"], (runs["ta"]["errors"], runs["tb"]["errors"])
    assert runs["fork_untouched"]
    fork, rb = runs["at_fork"], runs["rb"]
    assert rb["fork"]["from_profile"] == "semis"
    assert rb["fork"]["at_snapshot"] == fork["last_snapshot"]
    for k, t in fork["themes"].items():
        if not t["published"]:
            continue
        u = rb["themes"][k]
        n = len(t["measures"])
        assert u["measures"][:n] == t["measures"]
        assert u["versions"][:len(t["versions"])] == t["versions"]
        assert u["published_at"] == t["published_at"]
    new = [k for k in rb["themes"] if k not in fork["themes"]]
    assert new and all(k.startswith("tech-") for k in new)
    idx = _index(runs["B"] / "site")
    assert idx["fork"]["from_profile"] == "semis" and idx["effective"]["profile"] == "tech-v2"
    assert idx["universe"]["version"] == "fixture-2"


def test_added_companies_do_not_manufacture_emergence_or_cooling(runs):
    ra, rb = runs["ra"], runs["rb"]
    since = rb["fork"]["at_snapshot"]
    for k, a in ra["themes"].items():
        if not a["published"]:
            continue
        b = rb["themes"][k]
        assert b["status"] == a["status"], k  # same original theme, same reading
        fa = sum(1 for r in a["measures"] if r["t"] > since and r.get("flagged_comparable"))
        fb = sum(1 for r in b["measures"] if r["t"] > since and r.get("flagged_comparable"))
        assert fb <= fa, k


def test_newly_covered_themes_are_labelled_and_never_emerging(runs):
    rb, since = runs["rb"], runs["rb"]["fork"]["at_snapshot"]
    nc = [t for t in rb["themes"].values() if t.get("route") == "newly_covered"]
    assert nc
    for t in nc:
        assert t["newly_covered"]["backfill_present"] >= 6
        assert t["first_seen"] > since  # no history written before the fork
        assert t["published_at"] == runs["tb"]["checked_at"]  # decided now, not earlier
    lists = _index(runs["B"] / "site")["lists"]
    assert {t["id"] for t in nc} <= set(lists["newly_covered"])
    assert not {t["id"] for t in nc} & set(lists["emerging"])
    # A planted theme that new companies join gets a confirmed new basket version.
    joined = [t for t in rb["themes"].values() if t["published"] and len(t["versions"]) >
              len(runs["ra"]["themes"].get(t["id"], {"versions": t["versions"]})["versions"])]
    assert joined
    for t in joined:
        assert t["versions"][-1]["created_at"] == runs["tb"]["checked_at"]


def test_sector_benchmark_is_fixed_from_composition(runs):
    rb, ra = runs["rb"], runs["ra"]
    assert all("sector_benchmark" not in t for t in ra["themes"].values())  # semis unchanged
    chosen = {t["id"]: t["sector_benchmark"] for t in rb["themes"].values() if t["published"]}
    assert all(v["decided_at"] for v in chosen.values())
    assert any(v["symbol"] == "SOXX" for v in chosen.values())
    assert any(v["symbol"] in ("IGV", "CIBR") for v in chosen.values())
    for v in chosen.values():
        assert (v["symbol"] is None) == (v["share"] < 0.5 or v["sector"] is None)


def test_comparison_report_covers_the_requested_sections(runs):
    text = compare_profiles(SEMIS, runs["A"] / "state", runs["A"] / "site",
                            FORK, runs["B"] / "state", runs["B"] / "site")
    assert "Synthetic fixture data" in text
    for h in ("## Themes", "### Added themes", "Themes among semis", "## Naming and evidence",
              "## Generic or duplicate themes", "## Not published", "## Per-company coverage",
              "## Run metrics"):
        assert h in text, h
    assert "embeddings computed" in text


def test_semis_profile_output_is_unchanged_by_profiles(tmp_path):
    """The default profile still produces the same registry as an explicit run with
    the original files (nothing in the profile machinery changes its results)."""
    a = M.refresh(tmp_path / "a", tmp_path / "as", fixtures=True, as_of=dt.date(2021, 6, 30),
                  log=lambda *x: None)
    b = M.refresh(tmp_path / "b", tmp_path / "bs", fixtures=True, as_of=dt.date(2021, 6, 30),
                  log=lambda *x: None, profile=SEMIS)
    assert a["ok"] and b["ok"]
    ra = json.loads((tmp_path / "a" / "registry.json").read_text())
    rb = json.loads((tmp_path / "b" / "registry.json").read_text())
    assert ra["themes"] == rb["themes"]
    assert pd.Series([k for k in ra["themes"]]).str.startswith("th-").all()


def test_full_history_build_counts_every_company_from_the_start(tmp_path):
    """The pilot as configured: no fork, all companies covered from the start, a
    replay of every fixture month-end labelled as reconstructed, its own IDs,
    nothing 'newly covered'."""
    st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                   as_of=dt.date(2021, 12, 31), log=lambda *a: None, profile=TECH)
    assert st["ok"], st["errors"]
    reg = json.loads((tmp_path / "state" / "registry.json").read_text())
    assert "fork" not in reg and len(reg["snapshots"]) == 60  # every fixture month-end (< 120)
    idx = _index(tmp_path / "site")
    assert idx["universe"]["n_companies"] == 72 and not idx["universe"]["recently_added"]
    assert idx["themes"] and all(r["id"].startswith("tech-") for r in idx["themes"])
    assert all(r["route"] != "newly_covered" for r in idx["themes"])
    assert {r["basis"] for r in idx["themes"]} == {"reconstructed"}
    new = {f"{p}{i}" for p in ("APP", "SEC", "DAT") for i in range(1, 9)}
    members = {s for r in idx["themes"] for s in r["key_constituents"] for s in [s["symbol"]]}
    assert members & new  # the added companies are in themes and baskets from the start
    # Tone and families reach the feed; the review report reads the same state.
    assert all(r["tone"]["direction"] in ("headwind", "tailwind", "mixed", "unknown")
               for r in idx["themes"] if r["status"] not in ("inactive", "merged"))
    assert "headwinds" in idx["lists"] and idx["families"]["min_cosine"] == 0.85
    from themegraph.monitor.review import review_report
    text = review_report(tmp_path / "state", load_monitor(TECH.monitor))
    assert "## Most similar pairs" in text and "## Tone" in text
