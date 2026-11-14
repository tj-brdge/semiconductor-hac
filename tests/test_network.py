"""The dashboard's company network (monitor/network.py) and --profile on the
research commands."""

import json

import numpy as np
import pytest

from themegraph.monitor.network import _layout, network


def _companies(n=12):
    return [{"symbol": f"S{i}", "sector": "A" if i < 6 else "B",
             "themes": [{"id": "th-1"}] if i < 4 else []} for i in range(n)]


def _docs():
    return [{"id": "th-1", "status": "stable",
             "measures": [{"present": True, "issuers": ["S0", "S1", "S2", "S3", "ZZ"]}]},
            {"id": "th-2", "status": "stable",
             "measures": [{"present": True, "issuers": ["S0", "S1", "S8"]}]},
            {"id": "th-3", "status": "inactive",
             "measures": [{"present": True, "issuers": ["S9", "S10"]}]}]


CLUSTERS = {"links": [["S4", "S5", 0.61], ["S0", "S1", 0.44], ["S0", "QQ", 0.9]],
            "leaves": [{"symbol": "S4", "cluster": 1}, {"symbol": "S5", "cluster": 1}],
            "cut": {"corr": 0.35}, "start": "2021-07-01", "end": "2021-12-31", "index": "SPY"}


def test_network_has_two_kinds_of_link_and_stable_positions():
    net = network(_companies(), _docs(), CLUSTERS)
    assert [n["symbol"] for n in net["nodes"]] == [f"S{i}" for i in range(12)]
    # Price links only between covered companies; discussion links from active themes.
    assert [(e["a"], e["b"]) for e in net["price_links"]] == [("S4", "S5"), ("S0", "S1")]
    disc = {(e["a"], e["b"]): e["themes"] for e in net["discussion_links"]}
    assert disc[("S0", "S1")] == ["th-1", "th-2"]         # two shared themes
    assert ("S9", "S10") not in disc and len(disc) == 8    # th-3 is inactive; 6 + 2 new pairs
    assert net["discussion_links"][0]["themes"] == ["th-1", "th-2"]  # strongest first
    by = {n["symbol"]: n for n in net["nodes"]}
    assert by["S4"]["cluster"] == 1 and by["S11"]["degree"] == 0 and by["S0"]["degree"] == 5
    xy = np.array([[n["x"], n["y"]] for n in net["nodes"]])
    assert xy.min() >= 0 and xy.max() <= 1
    again = network(_companies(), _docs(), CLUSTERS)
    assert again == net                                    # same release, same picture
    json.dumps(net, allow_nan=False)
    # Linked companies end up closer than the typical pair.
    d = lambda a, b: float(np.hypot(*(xy[a] - xy[b])))  # noqa: E731
    typical = np.median([d(i, j) for i in range(12) for j in range(i + 1, 12)])
    assert d(4, 5) < typical and d(0, 1) < typical


def test_layout_keeps_dots_apart_and_survives_no_links():
    xy = _layout(40, [(i, j, 1.0) for i in range(8) for j in range(i + 1, 8)])
    dist = np.sqrt(((xy[:, None] - xy[None]) ** 2).sum(-1))[np.triu_indices(40, 1)]
    assert dist.min() > 0.02  # a clique no longer collapses to a point
    assert np.isfinite(_layout(5, [])).all() and _layout(1, []).shape == (1, 2)
    empty = network([], [], None)
    assert empty["nodes"] == [] and empty["price_links"] == [] and empty["price_cut"] is None


def test_research_commands_take_a_profile(capsys):
    from argparse import Namespace

    from themegraph import cli
    cfg, u, prof = cli._research(Namespace())
    assert prof.name == "tech-v2" and len(u.issuers) == 127  # one universe by default
    cfg2, u2, prof2 = cli._research(Namespace(profile="semis"))
    assert prof2.name == "semis" and len(u2.issuers) == 76   # the pilot, on request
    assert cli._is_default(prof2) and not cli._is_default(prof)
    with pytest.raises(SystemExit, match="--profile semis"):
        cli.main(["run", "--period", "holdout"])  # the holdout belongs to the pilot
    for cmd in ("run", "ingest", "coverage", "topics", "forward", "report"):
        with pytest.raises(SystemExit):
            cli.main([cmd, "--help"])
        assert "--profile" in capsys.readouterr().out
