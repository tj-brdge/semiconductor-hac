"""Registry rules that must not be fooled by coverage: status comparisons use a fixed
company cohort, and basket membership changes need the same proposal confirmed."""

import datetime as dt
from types import SimpleNamespace

import pytest

from themegraph.config import Issuer, UniverseConfig
from themegraph.monitor.config import load_monitor
from themegraph.monitor.coverage import Coverage
from themegraph.monitor.registry import Registry

M = load_monitor()
SNAPS = [f"2021-{m:02d}-28 23:59:59+00:00" for m in range(1, 9)]


def _cov(removed=None):
    iss = [Issuer(symbol=s, segment="x", role="core",
                  covered_until=(removed or {}).get(s)) for s in "ABCDEFG"]
    return Coverage(UniverseConfig(issuers=iss, benchmarks=["SPY"], sectors={"x": "X"}),
                    {}, 12)


def _theme(reg, windows, theme_rows, status="stable"):
    """windows: per month {company: passages in window}; theme_rows: per month
    {company: theme passages} or None when the theme was not found."""
    th = {"id": "th-1", "status": status, "status_history": [], "measures": [],
          "merged_into": None}
    for t, w, tr in zip(SNAPS, windows, theme_rows, strict=False):
        reg.data.setdefault("snapshot_meta", {})[t] = {"window_by_issuer": w}
        row = {"t": t, "present": tr is not None,
               "share": sum((tr or {}).values()) / sum(w.values())}
        if tr is not None:
            row.update(passages_by_issuer=tr, issuers=sorted(tr))
        th["measures"].append(row)
    return th


def _status(windows, theme_rows, cov=None, status="stable", tmp=None):
    reg = Registry(tmp)
    th = _theme(reg, windows, theme_rows, status)
    reg._status(th, SNAPS[len(windows) - 1], SNAPS[:len(windows)], M, cov or _cov())
    return th


BASE = {"A": 100, "B": 100}
THEME = {"A": 10, "B": 10}


def test_adding_unrelated_companies_does_not_cool_an_unchanged_theme(tmp_path):
    windows = [BASE] * 3 + [BASE | {s: 100 for s in "CDEF"}] * 3
    th = _status(windows, [THEME] * 6, tmp=tmp_path)
    assert th["status"] == "stable"  # the raw share of all text fell by two thirds


def test_removed_company_and_missing_documents_do_not_read_as_decline(tmp_path):
    # C stops being covered in month 4; B has no documents in month 5.
    windows = [BASE | {"C": 100}] * 3 + [BASE, {"A": 100}, BASE]
    rows = [THEME | {"C": 30}] * 3 + [THEME, {"A": 10}, THEME]
    th = _status(windows, rows, cov=_cov({"C": dt.date(2021, 4, 1)}), tmp=tmp_path)
    assert th["status"] == "stable"


def test_genuine_changes_in_discussion_are_still_detected(tmp_path):
    windows = [BASE] * 6
    up = _status(windows, [THEME] * 3 + [{"A": 20, "B": 20}] * 3, tmp=tmp_path / "up")
    down = _status(windows, [THEME] * 3 + [{"A": 3, "B": 3}] * 3, tmp=tmp_path / "down")
    assert up["status"] == "strengthening" and down["status"] == "cooling"
    assert down["status_history"][-1]["basis"]["cohort"] == 2


def test_months_without_the_themes_documents_are_not_absences(tmp_path):
    # The theme's companies have no documents for three months (a data gap):
    # the theme is not found, but that is missing evidence, not inactivity.
    other = {"C": 100, "D": 100}
    windows = [BASE | other] * 3 + [other] * 3
    th = _status(windows, [THEME] * 3 + [None] * 3, tmp=tmp_path / "gap")
    assert th["status"] != "inactive"
    # With their documents present and the theme gone, it is inactive.
    th = _status([BASE | other] * 6, [THEME] * 3 + [None] * 3, tmp=tmp_path / "gone")
    assert th["status"] == "inactive"


# -- basket membership confirmation ---------------------------------------------------

def _versioned(observations, tmp):
    reg = Registry(tmp)
    cov = _cov()
    th = {"id": "th-1", "published": True, "published_at": "2021-01-01T00:00:00+00:00",
          "pending": None, "versions": [{"version": 1, "created_at": "2021-01-01T00:00:00+00:00",
                                         "entry_after": "2021-01-01T00:00:00+00:00",
                                         "constituents": [{"symbol": "A"}, {"symbol": "B"}]}]}
    for t, members in zip(SNAPS, observations, strict=False):
        if members is None:  # absent this month: what process() does
            th["pending"] = None
            continue
        c = SimpleNamespace(exposure={s: 0.5 for s in members})
        reg._version(th, c, t, "2021-12-31T23:59:59+00:00", M, {}, cov)
    return th, [[x["symbol"] for x in v["constituents"]] for v in th["versions"]]


@pytest.mark.parametrize(("obs", "expected", "pending"), [
    (["AC", "AD"], [["A", "B"]], ["A", "D"]),             # different proposals: none adopted
    (["AC", "AC"], [["A", "B"], ["A", "C"]], None),        # confirmed twice in a row
    (["AC", None, "AC"], [["A", "B"]], ["A", "C"]),        # absence breaks the run
    (["AC", "AB", "AC"], [["A", "B"]], ["A", "C"]),        # back to current resets
    (["AC", "AD", "AD"], [["A", "B"], ["A", "D"]], None),  # a new proposal needs its own two
])
def test_membership_change_needs_the_same_set_confirmed(obs, expected, pending, tmp_path):
    th, versions = _versioned(obs, tmp_path)
    assert versions == expected
    assert (th["pending"] or {}).get("members") == pending


# -- missing evidence is not zero discussion -----------------------------------------

def _absent_rows(windows, theme_rows, tmp):
    """Feed absent months through _measures as process() does; returns the rows."""
    reg = Registry(tmp)
    th = _theme(reg, windows[:1], theme_rows[:1])
    for t, w, tr in zip(SNAPS[1:], windows[1:], theme_rows[1:], strict=False):
        reg.data["snapshot_meta"][t] = {"window_by_issuer": w}
        if tr is None:
            row = reg._measures(th | {"seen": []}, None, t, SNAPS, _cov(), M)
        else:
            row = {"t": t, "present": True, "observed": True, "share": 0.1,
                   "passages_by_issuer": tr, "issuers": sorted(tr)}
        th["measures"].append(row)
    return reg, th


def test_missing_documents_are_a_gap_and_disappearance_is_zero(tmp_path):
    other = {"C": 100, "D": 100}
    # Month 2: the theme's companies have no documents; month 3: they do, theme gone;
    # month 4: documents resume and the theme is back.
    windows = [BASE | other, other, BASE | other, BASE | other]
    rows = [THEME, None, None, THEME]
    reg, th = _absent_rows(windows, rows, tmp_path)
    gap, gone, back = th["measures"][1:]
    assert gap["observed"] is False and gap["share"] is None   # before: share 0.0
    assert gone["observed"] is True and gone["share"] == 0.0
    assert back["present"] and back["observed"]
    # Rows from registries written before `observed` existed get the same reading.
    for r in th["measures"]:
        if not r["present"]:
            r.pop("observed")
            r["share"] = 0.0
    reg.themes["th-1"] = th
    reg.annotate_observed(_cov())
    assert th["measures"][1]["observed"] is False and th["measures"][1]["share"] is None
    assert th["measures"][2]["observed"] is True and th["measures"][2]["share"] == 0.0
