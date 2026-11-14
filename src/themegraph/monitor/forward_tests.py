"""Forward tests: declared before any outcome, logged live, read once
(DESIGN.md, "Headwind forward test" and "Forward tests, second set").

The registry logs an event at the moment a declared condition is met in a live
refresh, with the basket held then. This module does the rest:

- `progress`: how many events each test has, how many have a completed window and
  how many of those have a usable primary outcome (with the reasons for the rest).
  Shows no returns, so looking at progress is not looking at results.
- `read`: when a test has at least `MIN_EVENTS` counted events with a completed
  window and a usable primary outcome, computes the declared statistic and stores
  it in `<state>/forward_reads.json`. A test is read once; a second read returns
  the stored result unchanged.
- `family`: Holm's correction across the three primary p-values, derived (never
  stored) once all three tests have been read. Until then each p-value is nominal
  and unadjusted and no test is called supported.

Independence: the sign test treats counted events as independent. The registered
overlap rule removes events that share half their companies within the window; it
does not remove dependence between baskets that share fewer companies, a sector, or
the same market months. Holm's correction handles three tests, not that.

Outcome of an event: its basket, equal starting capital, bought at the first
session on a later UTC date than the decision, held `h` sessions; total USD return
minus the benchmark's over the same sessions. A constituent without a price stays
in cash, as in the monitor's baskets.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.stats import binomtest

from .baskets import PriceBook, _segment, entry_session

PRIMARY, SECONDARY = 126, 63
MIN_EVENTS = 20
ALPHA = 0.05
# test id -> direction of the hypothesis for the basket's excess return over SPY
TESTS = {
    "headwind": {"expect": "less", "registered": "2026-11-12",
                 "hypothesis": "After a theme turns headwind, its basket lags SPY over the "
                               "next 6 months."},
    "emergence": {"expect": "greater", "registered": "2026-11-12",
                  "hypothesis": "A theme published through the emergence rule has a basket "
                                "that beats SPY over the next 6 months."},
    "tailwind": {"expect": "greater", "registered": "2026-11-12",
                 "hypothesis": "After a theme turns tailwind, its basket beats SPY over the "
                               "next 6 months."},
}


def events(reg: dict) -> dict[str, list[dict]]:
    out = {k: [] for k in TESTS}
    out["headwind"] = list(reg.get("headwind_log", []))
    for e in reg.get("forward_log", []):
        if e.get("test") in out:
            out[e["test"]].append(e)
    return out


def _counted(evs: list[dict], book: PriceBook) -> list[dict]:
    """Events with an entry session, minus overlaps: an event is left out when an
    earlier counted event of the same test started within PRIMARY sessions before it
    and holds at least half of this event's companies."""
    pos = {d: i for i, d in enumerate(book.cal)}
    kept: list[dict] = []
    for e in sorted(evs, key=lambda e: (e["entry_after"], e["theme"])):
        entry = entry_session(book, e["entry_after"])
        if entry is None or not e["basket"]:
            continue
        i = pos[entry]
        dup = any(i - k["_i"] < PRIMARY and
                  len(set(e["basket"]) & set(k["basket"])) * 2 >= len(e["basket"])
                  for k in kept)
        if not dup:
            kept.append({**e, "_i": i})
    return kept


def _excess(e: dict, book: PriceBook, h: int, bench: str | None) -> tuple[float | None, str]:
    """(excess return, "") or (None, why it cannot be computed)."""
    i = e["_i"]
    if i + h >= len(book.cal):
        return None, "window not complete"
    if bench is None:
        return None, "no benchmark recorded"
    if bench not in book.carry:
        return None, f"no prices for {bench}"
    a, z = book.cal[i], book.cal[i + h]
    b0, b1 = book.carry[bench].get(a), book.carry[bench].get(z)
    if not (b0 == b0 and b1 == b1 and b0):
        return None, f"{bench} has no price at the start or end of the window"
    seg = _segment(book, e["basket"], a, z)
    basket = float(seg["total"].iloc[-1] / seg["total"].iloc[0] - 1)
    if not np.isfinite(basket):
        return None, "basket return not computable"
    return basket - float(b1 / b0 - 1), ""


def _outcomes(kept: list[dict], book: PriceBook) -> tuple[list[dict], dict[str, int]]:
    """Counted events with a completed primary window, split into usable ones and
    the reasons the others have no primary outcome."""
    usable, unusable = [], {}
    for k in kept:
        if k["_i"] + PRIMARY >= len(book.cal):
            continue
        x, why = _excess(k, book, PRIMARY, k["benchmark"])
        if x is None:
            unusable[why] = unusable.get(why, 0) + 1
        else:
            usable.append({**k, "_x": x})
    return usable, unusable


def progress(reg: dict, book: PriceBook, state_dir: Path | None = None) -> list[dict]:
    """Counts only: outcomes are checked for being computable, never displayed."""
    done = _reads(state_dir)
    fam = family(done)
    rows = []
    for test, spec in TESTS.items():
        evs = events(reg)[test]
        kept = _counted(evs, book)
        complete = sum(k["_i"] + PRIMARY < len(book.cal) for k in kept)
        usable, unusable = _outcomes(kept, book)
        rows.append({"test": test, "hypothesis": spec["hypothesis"],
                     "registered": spec["registered"], "logged": len(evs),
                     "counted": len(kept), "complete": complete, "usable": len(usable),
                     "unusable": unusable, "needed": MIN_EVENTS,
                     "status": "read" if test in done else
                     "ready to read" if len(usable) >= MIN_EVENTS else "collecting",
                     "result": done.get(test),
                     "adjusted": (fam or {}).get("tests", {}).get(test)})
    return rows


def _reads(state_dir: Path | None) -> dict:
    p = Path(state_dir) / "forward_reads.json" if state_dir else None
    return json.loads(p.read_text()) if p and p.exists() else {}


def _stat(xs: list[float], expect: str) -> dict:
    """One-sided sign test. `n_valid` outcomes; exact zeros are valid outcomes but
    carry no sign, so the test uses the `n_sign` non-tied ones: `wins` of `n_sign`."""
    wins = sum(x < 0 for x in xs) if expect == "less" else sum(x > 0 for x in xs)
    ties = sum(x == 0 for x in xs)
    n = len(xs) - ties
    p = float(binomtest(wins, n, 0.5, alternative="greater").pvalue) if n else None
    return {"n_valid": len(xs), "n_ties": int(ties), "n_sign": int(n), "wins": int(wins),
            "median_excess": round(float(np.median(xs)), 5) if xs else None,
            "p_value": None if p is None else round(p, 6)}


def read(test: str, reg: dict, book: PriceBook, state_dir: Path, now: str) -> dict:
    """The one read of `test`. Refused until MIN_EVENTS counted events have a
    completed primary window and a usable primary outcome; stored, and returned
    unchanged on later calls. The stored p-value is nominal (unadjusted): whether
    the claim is supported is decided by `family`, not here."""
    done = _reads(state_dir)
    if test in done:
        return done[test]
    spec = TESTS[test]
    usable, unusable = _outcomes(_counted(events(reg)[test], book), book)
    if len(usable) < MIN_EVENTS:
        raise RuntimeError(
            f"{test}: {len(usable)} of {MIN_EVENTS} events have a completed {PRIMARY}-session "
            f"window and a usable outcome; not read"
            + (f" (unusable: {unusable})" if unusable else ""))

    def secondary(h: int, key: str) -> dict:
        got = [_excess(k, book, h, k.get(key)) for k in usable]
        why: dict[str, int] = {}
        for x, w in got:
            if x is None:
                why[w] = why.get(w, 0) + 1
        return {**_stat([x for x, _ in got if x is not None], spec["expect"]), "unusable": why}

    result = {"test": test, "read_at": now, "hypothesis": spec["hypothesis"],
              "expect": spec["expect"],
              "events": [{"theme": k["theme"], "snapshot": k["snapshot"]} for k in usable],
              "unusable": unusable,
              "primary": {"sessions": PRIMARY, "benchmark": "SPY",
                          **_stat([k["_x"] for k in usable], spec["expect"])},
              "secondary_63": secondary(SECONDARY, "benchmark"),
              "secondary_sector": secondary(PRIMARY, "sector_benchmark"),
              "alpha": ALPHA}
    done[test] = result
    path = Path(state_dir) / "forward_reads.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(done, indent=1, sort_keys=True))
    tmp.replace(path)
    return result


def family(done: dict) -> dict | None:
    """Holm's step-down correction across the three registered primary p-values.
    None until every test has been read with a p-value: a single nominal result
    decides nothing. Derived from the stored raw reads each time, never stored."""
    ps = {t: (done.get(t) or {}).get("primary", {}).get("p_value") for t in TESTS}
    if any(p is None for p in ps.values()):
        return None
    order = sorted(ps, key=lambda t: (ps[t], t))
    m, running, out, open_ = len(order), 0.0, {}, True
    for i, t in enumerate(order):
        adj = min(1.0, max(running, (m - i) * ps[t]))
        running = adj
        open_ = open_ and ps[t] <= ALPHA / (m - i)  # step-down: stop at the first failure
        out[t] = {"p_value": ps[t], "p_adjusted": round(adj, 6), "supported": bool(open_)}
    return {"method": "Holm", "alpha": ALPHA, "tests": out}


def _fmt(st: dict) -> str:
    if not st.get("n_valid"):
        return "no usable outcomes"
    return (f"median excess {st['median_excess']:+.1%}; {st['wins']} of {st['n_sign']} "
            f"non-tied outcomes as claimed ({st['n_valid']} valid, {st['n_ties']} exact "
            f"ties); nominal one-sided p = {st['p_value']}")


def report(rows: list[dict]) -> str:
    out = ["# Forward tests", "",
           "Declared before any outcome; events are logged live by the monitor. Progress "
           "shows counts only. A test is read once, when "
           f"{MIN_EVENTS} counted events have a completed {PRIMARY}-session window and a "
           "usable outcome.", "",
           "| test | hypothesis | logged | counted | windows complete | usable outcomes "
           "| status |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['test']} | {r['hypothesis']} | {r['logged']} | {r['counted']} | "
                   f"{r['complete']} | {r['usable']} of {r['needed']} | {r['status']} |")
    for r in rows:
        if r["unusable"]:
            out += ["", f"{r['test']}: completed windows without a usable outcome: " +
                    "; ".join(f"{k} ({v})" for k, v in r["unusable"].items()) + "."]
    read_ = [r for r in rows if r["result"]]
    for r in read_:
        res = r["result"]
        out += ["", f"## {r['test']}: read {res['read_at'][:10]}", "",
                f"- Primary ({res['primary']['sessions']} sessions vs SPY): "
                f"{_fmt(res['primary'])}.",
                f"- Secondary, 63 sessions vs SPY: {_fmt(res['secondary_63'])}.",
                f"- Secondary, vs sector benchmark: {_fmt(res['secondary_sector'])}."]
    if read_:
        out += ["", "## Family result (Holm, three tests)", ""]
        if all(r["adjusted"] for r in rows):
            for r in rows:
                a = r["adjusted"]
                out.append(f"- {r['test']}: nominal p = {a['p_value']}, adjusted p = "
                           f"{a['p_adjusted']}: "
                           f"{'supported' if a['supported'] else 'not supported'} at "
                           f"{ALPHA:.0%}.")
        else:
            waiting = [r["test"] for r in rows if not r["result"]]
            out.append("Not available: " + ", ".join(waiting) + " not read yet. The p-values "
                       "above are nominal and unadjusted; no claim is supported or rejected "
                       "until all three tests are read.")
    out += ["", "The sign test treats counted events as independent. The overlap rule "
            "removes events sharing half their companies within the window; baskets that "
            "share fewer companies, a sector or the same market months remain dependent, "
            "which the correction for three tests does not address."]
    return "\n".join(out) + "\n"
