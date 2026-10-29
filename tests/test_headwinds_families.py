"""Headwind themes (monitor/tone.py) and theme families (monitor/families.py)."""

import numpy as np
import pytest

from themegraph.monitor.config import load_monitor
from themegraph.monitor.export import overview_lists, theme_overview
from themegraph.monitor.families import families
from themegraph.monitor.tone import score, terms, theme_tone

M = load_monitor()


def test_passages_are_scored_by_business_language():
    s, hits = score([
        "We saw weaker demand and an inventory correction at our largest customers.",
        "Record revenue on strong demand; we are raising guidance.",
        "Revenue was $1.2 billion in the quarter.",
        "Despite some headwinds, we see robust demand and share gains.",
        "These forward-looking statements involve risks and uncertainties.",  # boilerplate
    ])
    assert list(s) == [-1, 1, 0, 1, 0]
    assert hits[0] == 2
    assert terms(["Pricing pressure and pricing pressure again; excess inventory."]) == \
        ["pricing pressure", "excess inventory"]


def _tone(neg, pos, n, genre="transcript:prepared", rates=None):
    sign = np.array([-1] * neg + [1] * pos + [0] * (n - neg - pos))
    return theme_tone(sign, np.array([genre] * n), rates or {genre: (0.1, 0.2)}, M.tone)


def test_direction_needs_share_ratio_and_majority():
    assert _tone(30, 5, 60)["direction"] == "headwind"       # 50%, 5x the usual 10%
    assert _tone(10, 2, 60)["direction"] == "mixed"          # 17%: below min_share
    assert _tone(30, 32, 60)["direction"] == "tailwind"      # more positive than negative
    assert _tone(5, 2, 10)["direction"] == "unknown"         # too few passages
    t = _tone(30, 5, 60)
    assert t["negative_ratio"] == pytest.approx(5.0) and t["expected_negative"] == 0.1


def test_filings_are_compared_with_filings():
    """Risk factors make filings negative everywhere: a filings theme with the
    usual filings rate is not a headwind, the same share in call text is."""
    rates = {"filing": (0.4, 0.1), "transcript:prepared": (0.1, 0.2)}
    assert _tone(25, 3, 60, "filing", rates)["direction"] == "mixed"
    assert _tone(25, 3, 60, "transcript:prepared", rates)["direction"] == "headwind"


def _row(i, direction=None, growth=None, status="stable", persistence=8, issuers=5):
    return {"id": i, "status": status, "route": "established", "published_snapshot": "x",
            "emergence": {"flagged_recently": False, "growth_3m": growth},
            "strength": {"persistence_12": persistence, "n_issuers": issuers},
            "market": {"trend": {}}, "tone": {"direction": direction, "negative_ratio": 3.0}}


def test_headwinds_listed_once_and_spreading_first():
    rows = [_row("a", "headwind", 1.1), _row("b", "headwind", 1.6), _row("c", "tailwind"),
            _row("d", "headwind", 2.0, status="inactive")]
    lists = overview_lists(rows, M)
    assert lists["headwinds"] == ["b", "a"]            # inactive left out
    assert lists["established"] == ["c"]               # each theme in one section


def test_overview_states_the_tone_and_its_limits():
    doc = {"keywords": ["inventory"], "label": None, "route": "established",
           "first_seen": "2021-01-31", "published_snapshot": "2021-01-31", "status": "stable",
           "status_history": [], "performance": {},
           "measures": [{"t": "2021-06-30", "present": True, "n_issuers": 4, "share": 0.01,
                         "tone": {"direction": "headwind", "negative_share": 0.46,
                                  "negative_ratio": 2.6,
                                  "headwind_terms": ["inventory correction", "weaker demand"]}}]}
    ov = {p["heading"]: p["text"] for p in theme_overview(doc)}
    t = ov["What kind of news"]
    assert t.startswith("A headwind") and "46%" in t and "2.6×" in t
    assert "inventory correction and weaker demand" in t and "miss negation" in t


def _theme(i, vec_status="stable", persistence=6, issuers=("A", "B"), first="2020-01-31"):
    return {"id": i, "published": True, "status": vec_status, "merged_into": None,
            "first_seen": first, "keywords": [],
            "measures": [{"t": "2021-06-30", "present": True, "persistence_12": persistence,
                          "issuers": list(issuers), "n_issuers": len(issuers)}]}


def _unit(*v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


def test_families_group_near_duplicates_without_chaining():
    # a~b very close; c is close to b but not to a (a chain); d unrelated.
    vec = {"a": _unit(1, 0, 0), "b": _unit(1, 0.25, 0), "c": _unit(1, 0.9, 0),
           "d": _unit(0, 0, 1)}
    th = [_theme("a", persistence=9, issuers=("A", "B", "C")), _theme("b", issuers=("B", "C")),
          _theme("c"), _theme("d")]
    fam = families(th, vec, 0.85)
    assert fam["a"]["family"] == fam["b"]["family"] == "a" and fam["a"]["lead"]
    assert fam["c"]["family"] == "c" and fam["d"]["family"] == "d"  # no chain through b
    rel = fam["a"]["related"][0]
    assert rel["id"] == "b" and rel["shared_companies"] == ["B", "C"]
    assert rel["cosine"] == pytest.approx(float(vec["a"] @ vec["b"]), abs=1e-3)


def test_families_leave_inactive_themes_and_records_alone():
    vec = {"a": _unit(1, 0, 0), "b": _unit(1, 0.1, 0)}
    th = [_theme("a"), _theme("b", vec_status="inactive")]
    before = [dict(t) for t in th]
    fam = families(th, vec, 0.85)
    assert set(fam) == {"a"} and fam["a"]["members"] == ["a"]
    assert th == before  # display only: nothing in the registry changes
