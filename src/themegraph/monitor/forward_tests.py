"""Forward tests: declared before any outcome, logged live, read once
(DESIGN.md, "Headwind forward test" and "Forward tests, second set").

The registry logs an event at the moment a declared condition is met in a live
refresh, with the basket held then. This module does the rest:

- `progress`: how many events each test has and how many have a completed window.
  Shows no returns, so looking at progress is not looking at results.
- `read`: when a test has at least `MIN_EVENTS` counted events with a completed
  primary window, computes the declared statistic and stores it in
  `<state>/forward_reads.json`. A test is read once; a second read returns the
  stored result.

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


def _excess(e: dict, book: PriceBook, h: int, bench: str | None) -> float | None:
    i = e["_i"]
    if bench is None or bench not in book.carry or i + h >= len(book.cal):
        return None
    a, z = book.cal[i], book.cal[i + h]
    seg = _segment(book, e["basket"], a, z)
    basket = float(seg["total"].iloc[-1] / seg["total"].iloc[0] - 1)
    b0, b1 = book.carry[bench].get(a), book.carry[bench].get(z)
    if not (np.isfinite(basket) and b0 == b0 and b1 == b1):
        return None
    return basket - float(b1 / b0 - 1)


def progress(reg: dict, book: PriceBook, state_dir: Path | None = None) -> list[dict]:
    """Counts only: no return is computed for display here."""
    done = _reads(state_dir)
    rows = []
    for test, spec in TESTS.items():
        evs = events(reg)[test]
        kept = _counted(evs, book)
        complete = sum(k["_i"] + PRIMARY < len(book.cal) for k in kept)
        rows.append({"test": test, "hypothesis": spec["hypothesis"],
                     "registered": spec["registered"], "logged": len(evs),
                     "counted": len(kept), "complete": complete, "needed": MIN_EVENTS,
                     "status": "read" if test in done else
                     "ready to read" if complete >= MIN_EVENTS else "collecting",
                     "result": done.get(test)})
    return rows


def _reads(state_dir: Path | None) -> dict:
    p = Path(state_dir) / "forward_reads.json" if state_dir else None
    return json.loads(p.read_text()) if p and p.exists() else {}


def _stat(xs: list[float], expect: str) -> dict:
    wins = sum(x < 0 for x in xs) if expect == "less" else sum(x > 0 for x in xs)
    n = sum(x != 0 for x in xs)
    p = float(binomtest(wins, n, 0.5, alternative="greater").pvalue) if n else None
    return {"n": len(xs), "median_excess": round(float(np.median(xs)), 5) if xs else None,
            "in_expected_direction": int(wins), "p_value": None if p is None else round(p, 5)}


def read(test: str, reg: dict, book: PriceBook, state_dir: Path, now: str) -> dict:
    """The one read of `test`. Refused until MIN_EVENTS counted events have a
    completed primary window; stored, and returned unchanged on later calls."""
    done = _reads(state_dir)
    if test in done:
        return done[test]
    spec = TESTS[test]
    kept = [k for k in _counted(events(reg)[test], book) if k["_i"] + PRIMARY < len(book.cal)]
    if len(kept) < MIN_EVENTS:
        raise RuntimeError(f"{test}: {len(kept)} of {MIN_EVENTS} events have a completed "
                           f"{PRIMARY}-session window; not read")
    prim = [x for k in kept if (x := _excess(k, book, PRIMARY, k["benchmark"])) is not None]
    sec = [x for k in kept if (x := _excess(k, book, SECONDARY, k["benchmark"])) is not None]
    sect = [x for k in kept
            if (x := _excess(k, book, PRIMARY, k.get("sector_benchmark"))) is not None]
    result = {"test": test, "read_at": now, "hypothesis": spec["hypothesis"],
              "events": [{"theme": k["theme"], "snapshot": k["snapshot"]} for k in kept],
              "primary": {"sessions": PRIMARY, "benchmark": "SPY", **_stat(prim, spec["expect"])},
              "secondary_63": _stat(sec, spec["expect"]),
              "secondary_sector": _stat(sect, spec["expect"]),
              "alpha": ALPHA}
    result["supported"] = bool(result["primary"]["p_value"] is not None
                               and result["primary"]["p_value"] < ALPHA)
    done[test] = result
    path = Path(state_dir) / "forward_reads.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(done, indent=1, sort_keys=True))
    tmp.replace(path)
    return result


def report(rows: list[dict]) -> str:
    out = ["# Forward tests", "",
           "Declared before any outcome; events are logged live by the monitor. Progress "
           "shows counts only. A test is read once, when "
           f"{MIN_EVENTS} counted events have a completed {PRIMARY}-session window.", "",
           "| test | hypothesis | logged | counted | windows complete | status |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['test']} | {r['hypothesis']} | {r['logged']} | {r['counted']} | "
                   f"{r['complete']} of {r['needed']} | {r['status']} |")
    for r in rows:
        if r["result"]:
            p = r["result"]["primary"]
            out += ["", f"## {r['test']}: read {r['result']['read_at'][:10]}", "",
                    f"- Primary ({p['sessions']} sessions vs SPY): median excess "
                    f"{p['median_excess']:+.1%}, {p['in_expected_direction']} of {p['n']} in "
                    f"the expected direction, one-sided p = {p['p_value']}. "
                    f"{'Supported' if r['result']['supported'] else 'Not supported'} at "
                    f"{ALPHA:.0%}.",
                    f"- Secondary: 63 sessions {r['result']['secondary_63']}; vs sector "
                    f"benchmark {r['result']['secondary_sector']}."]
    return "\n".join(out) + "\n"
