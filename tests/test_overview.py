"""Status points (where discussion strengthens or weakens) and the longer theme
overview, both built only from what the registry recorded."""

from themegraph.monitor.export import status_points, theme_overview


def _hist(*moves):
    return {"status_history": [{"t": f"2021-{i + 1:02d}-28T00:00:00", "from": a, "to": b,
                                "basis": {"ratio": r} if r else {}}
                               for i, (a, b, r) in enumerate(moves)]}


def test_status_points_mark_each_strengthening_and_weakening():
    pts = status_points(_hist(("new", "stable", None), ("stable", "strengthening", 1.4),
                              ("strengthening", "stable", 1.1), ("stable", "cooling", 0.7),
                              ("cooling", "inactive", None), ("inactive", "stable", None)))
    assert [(p["kind"], p["label"]) for p in pts] == [
        ("up", "Strengthening"), ("down", "Growth levelled off"), ("down", "Cooling"),
        ("down", "Went quiet"), ("up", "Recovered")]  # new -> stable is not a change of pace
    assert pts[0]["ratio"] == 1.4 and pts[3]["ratio"] is None


def _doc(**kw):
    m = {"t": "2021-06-30", "present": True, "n_issuers": 3, "issuers": ["A", "B", "C"],
         "sectors": ["Software"], "passages_by_issuer": {"A": 6, "B": 3, "C": 1},
         "share": 0.02, "growth_3m": 1.3, "persistence_12": 9, "new_issuers_3m": ["C"],
         "recently_added": [], "evidence_roles": {"management_remarks": 0.5,
                                                  "management_answer": 0.1, "filing": 0.1,
                                                  "analyst_question": 0.3}}
    doc = {"keywords": ["agents", "copilots", "models"], "label": {"source": "keywords"},
           "measures": [m], "first_seen": "2020-01-31", "published_snapshot": "2020-06-30",
           "route": "established", "status": "strengthening",
           **_hist(("stable", "strengthening", 1.3)),
           "performance": {"status": "ok", "constituents": [{}, {}, {}],
                           "returns": {"full_period": {"theme": 0.12, "SPY": 0.05,
                                                       "start": "2020-07-01",
                                                       "end": "2021-06-30",
                                                       "basis": "mixed"}}},
           "benchmarks": ["SPY"],
           "trading_together": {"now": {"percentile": 0.97, "random_baskets": 1000}}}
    doc.update(kw)
    return doc


def test_overview_states_what_was_recorded_and_keeps_prices_separate():
    ov = {p["heading"]: p["text"] for p in theme_overview(_doc())}
    assert list(ov) == ["What it is", "Who discusses it", "How it has developed",
                        "Where the evidence comes from", "How its stocks have done"]
    assert "No written description yet" in ov["What it is"]
    assert "A (60%), B (30%) and C (10%)" in ov["Who discusses it"]
    assert "New in the last three months: C" in ov["Who discusses it"]
    assert "strengthening (Jan 2021)" in ov["How it has developed"]
    assert "1.30×" in ov["How it has developed"]
    assert "investors are asking about it" in ov["Where the evidence comes from"]
    s = ov["How its stocks have done"]
    assert "+12.0%" in s and "+5.0%" in s and "simulated, then observed" in s
    assert "more than 97% of 1000 random baskets" in s and "trading together" in s
    assert "Prices only" in s
    for k in ("What it is", "Who discusses it", "How it has developed",
              "Where the evidence comes from"):
        assert "returned" not in ov[k] and "SPY" not in ov[k]  # prices stay in their section


def test_overview_without_prices_or_history_says_less_not_more():
    ov = theme_overview(_doc(performance={"status": "awaiting_entry"}, status_history=[],
                             trading_together=None))
    heads = [p["heading"] for p in ov]
    assert "How its stocks have done" not in heads
    assert "Changes in discussion" not in ov[2]["text"]
