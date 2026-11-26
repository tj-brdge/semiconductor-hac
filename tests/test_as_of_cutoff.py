"""`--as-of` is an information cutoff for every layer on real data (the store path),
not only for themes: data available after the date cannot change a snapshot of it.

The store is built offline from the synthetic fixture and read through the same
`load_inputs` branch a live refresh uses."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from themegraph import synthetic as S
from themegraph.config import Availability, load_pipeline
from themegraph.data.pit import macro_available_at, price_available_at, snapshot_ts
from themegraph.data.store import Store
from themegraph.monitor import refresh as M
from themegraph.text.embed import HashingEmbedder

D = dt.date(2021, 6, 30)       # the historical snapshot
LATER = dt.date(2021, 9, 30)   # a later one, which may use the later data
VINTAGE = "20220105T000000Z"
RULES = Availability()
TIMES = {"generated_at", "checked_at", "release", "published_release", "at", "seconds",
         "metrics", "last_attempt", "last_success"}


def _macro() -> pd.DataFrame:
    """Daily yields, oil and dollar levels, and monthly CPI, through 2021-12."""
    rng = np.random.default_rng(11)
    days = pd.bdate_range("2014-01-01", "2021-12-31")
    frames = []
    for name, kind, base, step in (("us2y", "treasury", 1.5, 0.03), ("us10y", "treasury", 2.5, 0.03),
                                   ("wti", "price", 60.0, 0.9), ("dxy", "price", 95.0, 0.3)):
        level = np.abs(base + np.cumsum(rng.normal(0, step, len(days)))) + 0.2
        df = pd.DataFrame({"date": days, "value": level})
        df["available_at"] = price_available_at(df["date"], RULES)
        frames.append(df.assign(series=name, kind=kind))
    months = pd.date_range("2014-01-01", "2021-12-01", freq="MS")
    df = pd.DataFrame({"date": months,
                       "value": 230 * np.exp(np.cumsum(rng.normal(0.002, 0.002, len(months))))})
    df["available_at"] = macro_available_at(df["date"], RULES)
    frames.append(df.assign(series="cpi", kind="economic"))
    out = pd.concat(frames, ignore_index=True)
    out["period"] = out["date"].dt.date.astype(str)
    out["published_at"] = out["available_at"]
    return out


def _known_by(day: dt.date, prices, docs, macro):
    """What existed at the end of `day`, cut here independently of the loader."""
    end = pd.Timestamp(day)
    daily = (macro["series"] != "cpi") & (macro["date"] <= end)
    # CPI for reference month M is usable from day 1 of M + 2 (config/pipeline.yaml).
    cpi = (macro["series"] == "cpi") & (
        macro["date"] + pd.DateOffset(months=RULES.macro_release_lag_months) <= end)
    return (prices.loc[prices["date"] <= end],
            docs.loc[docs["available_at"] <= snapshot_ts(day)], macro.loc[daily | cpi])


def _write(root, prices, docs, macro) -> Store:
    store = Store(root)
    store.write("prices", prices.reset_index(drop=True), VINTAGE)
    store.write("documents_transcripts", docs.reset_index(drop=True), VINTAGE)
    store.write("macro", macro.reset_index(drop=True), VINTAGE)
    return store


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("asof")
    u = S.synthetic_universe()
    prices, docs, macro = S.synthetic_prices(u), S.synthetic_documents(u), _macro()
    assert prices["date"].max() > pd.Timestamp(LATER) and docs["available_at"].max() > snapshot_ts(LATER)
    return {"root": root, "universe": u, "cfg": S.demo_config(load_pipeline()),
            "full": _write(root / "store_full", prices, docs, macro),
            "only_d": _write(root / "store_d", *_known_by(D, prices, docs, macro))}


def _wire(monkeypatch, w, store):
    """Point the real-data branch of `load_inputs` at an offline store."""
    from themegraph import cli
    from themegraph.monitor import naming
    from themegraph.text import embed

    def no_writer(*a, **k):
        raise RuntimeError("offline test: no label writer")
    monkeypatch.setattr(cli, "_store", lambda: store)
    monkeypatch.setattr(cli, "DATA", store.root.parent / f"data_{store.root.name}")
    monkeypatch.setattr(M, "load_pipeline", lambda *a, **k: w["cfg"])
    monkeypatch.setattr(M, "load_universe", lambda *a, **k: w["universe"])
    monkeypatch.setattr(embed, "make_embedder",
                        lambda e: HashingEmbedder(w["cfg"].embedding.dimensions))
    monkeypatch.setattr(naming, "ClaudeNamer", no_writer)


def _refresh(monkeypatch, w, store, name, as_of):
    _wire(monkeypatch, w, store)
    root = w["root"] / name
    st = M.refresh(root / "state", root / "site", fixtures=False, as_of=as_of,
                   ingest_prices=False, ingest_documents=False,
                   now=f"{as_of}T23:59:59+00:00", log=lambda *a: None)
    assert st["ok"], (st["errors"], st["skipped"])
    return root


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in TIMES}
    return [_strip(v) for v in x] if isinstance(x, list) else x


def _published(root) -> dict:
    data = root / "site" / "data"
    rel = data / json.loads((data / "latest.json").read_text())["path"]
    out = {"index": _strip(json.loads((rel / "index.json").read_text()))}
    for p in sorted((rel / "themes").glob("*.json")):
        out[p.name] = _strip(json.loads(p.read_text()))
    return out


def _measures(root) -> dict:
    reg = json.loads((root / "state" / "registry.json").read_text())
    return {k: (t["measures"], t["versions"], t["published"]) for k, t in reg["themes"].items()}


@pytest.fixture(scope="module")
def runs(world):
    mp = pytest.MonkeyPatch()
    try:
        out = {"d_only": _refresh(mp, world, world["only_d"], "only_d", D),
               "d_full": _refresh(mp, world, world["full"], "full", D)}
        out["pub_d_only"], out["pub_d_full"] = _published(out["d_only"]), _published(out["d_full"])
        out["reg_d_only"], out["reg_d_full"] = _measures(out["d_only"]), _measures(out["d_full"])
        # The same two registries, moved forward: now the later data may be used.
        _refresh(mp, world, world["only_d"], "only_d", LATER)
        _refresh(mp, world, world["full"], "full", LATER)
        out["pub_later_only"], out["pub_later_full"] = (_published(out["d_only"]),
                                                        _published(out["d_full"]))
        out["reg_later_only"], out["reg_later_full"] = (_measures(out["d_only"]),
                                                        _measures(out["d_full"]))
    finally:
        mp.undo()
    return out


def test_loader_cuts_prices_documents_and_factors_at_the_as_of_date(world, monkeypatch):
    _wire(monkeypatch, world, world["full"])
    d = M.load_inputs(False, D)
    assert d["fixture"] is False
    assert pd.to_datetime(d["prices"]["date"]).max() == pd.Timestamp(D)
    assert d["prices"]["available_at"].max() <= snapshot_ts(D)
    assert d["docs"]["available_at"].max() <= snapshot_ts(D)
    assert d["factors"].index.max() <= pd.Timestamp(D)
    _wire(monkeypatch, world, world["only_d"])
    alone = M.load_inputs(False, D)
    for k in ("prices", "docs"):
        cols = [c for c in alone[k].columns if c != "text"]
        pd.testing.assert_frame_equal(d[k][cols], alone[k][cols])
    pd.testing.assert_frame_equal(d["factors"], alone["factors"])
    # CPI growth for a reference month counts two months later and no sooner: the
    # May figure (usable from 1 July) is not in a 30 June snapshot.
    assert pd.notna(d["factors"]["cpi"].loc["2021-06-30"])
    _wire(monkeypatch, world, world["full"])
    whole = M.load_inputs(False, dt.date.today())           # the default: nothing cut
    assert pd.to_datetime(whole["prices"]["date"]).max() == pd.Timestamp("2021-12-31")
    assert len(whole["docs"]) > len(d["docs"]) and whole["factors"].index.max() > pd.Timestamp(D)


def test_future_data_cannot_change_a_historical_snapshot(runs):
    a, b = runs["pub_d_only"], runs["pub_d_full"]
    assert a["index"]["themes"], "nothing was published: the comparison would be empty"
    assert a["index"]["data_as_of"]["prices"] == str(D)
    assert a["index"]["macro"]["available"] and a["index"]["market_clusters"]["available"]
    assert set(a) == set(b)
    for name in a:
        assert a[name] == b[name], name
    assert runs["reg_d_only"] == runs["reg_d_full"]


def test_the_later_data_is_used_once_as_of_moves_forward(runs):
    """Not vacuous: the extra prices, documents and macro values do matter, from the
    date they became available."""
    a, b = runs["pub_later_only"], runs["pub_later_full"]
    assert a["index"]["data_as_of"]["prices"] == str(D)       # nothing newer in that store
    assert b["index"]["data_as_of"]["prices"] == str(LATER)
    assert b["index"]["data_as_of"]["documents"] > a["index"]["data_as_of"]["documents"]
    assert b["index"]["macro"]["end"] > a["index"]["macro"]["end"]
    assert runs["reg_later_only"] != runs["reg_later_full"]   # new month-ends were measured
    assert b != runs["pub_d_full"]
