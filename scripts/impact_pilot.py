"""Economic-impact extraction pilot: the commands the owner runs, in order.

Protocol (frozen): docs/experiments/ECONOMIC_IMPACT_PILOT.md. How to run it:
docs/experiments/ECONOMIC_IMPACT_PILOT_RUNBOOK.md. Not part of the monitor.

    .venv/bin/python scripts/impact_pilot.py status
    .venv/bin/python scripts/impact_pilot.py export            # read-only; no model
    .venv/bin/python scripts/impact_pilot.py sample            # once; writes reader sheets
    .venv/bin/python scripts/impact_pilot.py run --confirm-cost    # the only paid step
    .venv/bin/python scripts/impact_pilot.py score

Protocol version 2 (docs/experiments/ECONOMIC_IMPACT_PILOT_V2.md) replaces the two
readers with invited labellers. `export`, `sample` and `run` are the same; instead
of the reader sheets and `score`:

    .venv/bin/python scripts/impact_pilot.py crowd-codes --n 24 --out codes.txt
    .venv/bin/python scripts/impact_pilot.py crowd-pack --codes codes.txt
    .venv/bin/python scripts/impact_pilot.py crowd-collect returned/labels_*.json
    .venv/bin/python scripts/impact_pilot.py crowd-score       # after `run`

`score` stays version 1's (two reader sheets, kappa); `crowd-score` is version 2's
(majority labels, alpha). They are separate commands so that neither can be run
by mistake against the other's labels.

Files go to runs/impact_pilot/ (not in git); model answers go to
<state>/experimental/impact_pilot/answers.jsonl. `export` may compute embeddings,
which costs money, only with --allow-embed and only if the cache is incomplete.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from themegraph.experimental import impact_crowd as C
from themegraph.experimental import impact_crowd_run as CR
from themegraph.experimental import impact_pilot as P
from themegraph.experimental import impact_run as R
from themegraph.monitor.profiles import ACTIVE, load_profile

NEXT = {
    "export": "Next: `sample` (draws the sample once and writes the reader sheets).",
    "sample": "Next: commit sample.csv (copy it to docs/experiments/impact_pilot/) before "
              "anything else; give each reader only their own labels_reader_*.csv and "
              "READER_GUIDE.md. Then `run --confirm-cost` (readers must not see its output).",
    "run": "Next: when both reader sheets are filled in, `score`. Keep answers.jsonl away "
           "from the readers until their labels are final.",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, default=R.WORK, help="folder for the pilot's files")
    ap.add_argument("--profile", default=ACTIVE, help="monitor profile (default: %(default)s)")
    ap.add_argument("--state", type=Path, help="registry folder (default: the profile's)")
    ap.add_argument("--answers", type=Path,
                    help="model answers (default: <state>/experimental/impact_pilot/"
                         "answers.jsonl)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("export", help="link stored passages to themes (read-only)")
    s.add_argument("--allow-embed", action="store_true",
                   help="compute embeddings missing from the cache (a paid API call); "
                        "without it the export stops if any is missing")
    s.add_argument("--allow-partial", action="store_true",
                   help="write the exactly linked theme-months even if some could not be "
                        "linked (they are listed in export_report.json)")
    s.add_argument("--fixtures", action="store_true", help="synthetic data (tests, demos)")
    sub.add_parser("sample", help="draw the sample once; write the blind reader sheets")
    s = sub.add_parser("run", help="ask the model once per sampled passage (paid)")
    s.add_argument("--confirm-cost", action="store_true",
                   help="required: accepts the cost printed from the protocol")
    s.add_argument("--limit", type=int, help="ask at most this many passages in this run")
    sub.add_parser("score", help="agreement, adjudication, criteria and result.md")
    sub.add_parser("status", help="which steps are done")
    s = sub.add_parser("crowd-codes", help="version 2: make random labeller codes")
    s.add_argument("--n", type=int, required=True, help="how many codes")
    s.add_argument("--out", type=Path, help="write them to this file (one per line); "
                                            "it must not exist yet")
    s = sub.add_parser("crowd-pack", help="version 2: write the labelling folder")
    s.add_argument("--codes", type=Path, required=True,
                   help="file with one labeller code per line (codes only: never a name "
                        "or an email address)")
    s.add_argument("--per-passage", type=int, choices=(3, 5), default=C.LABELS_PER_PASSAGE,
                   help="labellers per passage in the first round (default: %(default)s)")
    s = sub.add_parser("crowd-collect", help="version 2: check the returned label files "
                                             "and write the majority labels")
    s.add_argument("files", nargs="+", type=Path,
                   help="every labels_<code>.json returned so far")
    sub.add_parser("crowd-score", help="version 2: criteria against the majority labels")
    a = ap.parse_args(argv)

    prof = load_profile(a.profile)
    state = a.state or prof.state
    answers = a.answers or R.answers_path(state)
    try:
        if a.cmd == "export":
            R.export(state, a.work, profile=prof, fixtures=a.fixtures,
                     allow_embed=a.allow_embed, allow_partial=a.allow_partial)
            print(NEXT["export"])
        elif a.cmd == "sample":
            try:
                R.sample(a.work)
            except P.SampleShortfall as e:
                print("The sample cannot be drawn as the protocol declares. Nothing was "
                      "written and nothing was shrunk or borrowed. Shortfalls:")
                for x in e.shortfalls:
                    print(f"  {x['stratum']} / {x['cell']}: wanted {x['wanted']}, found "
                          f"{x['found']}")
                print("Quotas change only in a new protocol version, before any model run.")
                return 1
            print(NEXT["sample"])
        elif a.cmd == "run":
            out = R.run(a.work, answers, limit=a.limit, confirm_cost=a.confirm_cost)
            print(NEXT["run"] if not out["remaining"] else
                  f"{out['remaining']} passages still have no answer: run `run "
                  "--confirm-cost` again (answered passages are not asked twice).")
            return 0 if not out["errors"] else 1
        elif a.cmd == "score":
            out = R.score(a.work, answers)
            if out["status"] == "needs_adjudication":
                print("Next: the readers settle each row of disagreements.csv (fill the "
                      "empty adjudicated_* cells and a one-line reason; the owner decides "
                      "where they cannot), save it as adjudicated.csv in the same folder, "
                      "then run `score` again.")
                return 2
            print("Next: copy sample.csv, labels_final.csv, predictions.csv and result.md "
                  "to docs/experiments/impact_pilot/, commit them, and record the result "
                  "in the protocol, pass or fail, with no change to the criteria.")
        elif a.cmd == "crowd-codes":
            codes = C.new_codes(a.n)
            if a.out:
                if a.out.exists():
                    raise R.Refused(f"{a.out} exists: codes already handed out are kept")
                a.out.write_text("\n".join(codes) + "\n")
            print("\n".join(codes))
            print(f"{C.labellers_needed(300)} labellers at least are needed for 300 "
                  f"passages at {C.LABELS_PER_PASSAGE} each and {C.WORKLOAD_CAP} passages "
                  "per labeller; invite a few more. Keep the list of who has which code "
                  "to yourself: it is never committed.")
        elif a.cmd == "crowd-pack":
            CR.pack(a.work, a.codes, per_passage=a.per_passage)
            print("Next: host the folder (or send each labeller index.html and only their "
                  "own data/<code>.json) and give each labeller the link ending "
                  "?code=<their code>. Labellers must see nothing else from this folder "
                  "tree. When files come back: `crowd-collect <all returned files>`.")
        elif a.cmd == "crowd-collect":
            out = CR.collect(a.work, a.files)
            if out["status"] != "adjudicated":
                print("Next: re-send to the labellers listed as waiting, or add new codes "
                      "to the codes file and run `crowd-pack` again (new labellers get "
                      "only the passages still needing labels); then `crowd-collect` "
                      "with every returned file.")
                return 2
            print("Next: copy crowd_assignment.csv and, from crowd_labels/, labellers.csv, "
                  "labels_long.csv, adjudicated.csv and crowd_report.md to "
                  "docs/experiments/impact_pilot/ and commit them BEFORE `run "
                  "--confirm-cost`; then `crowd-score`. Do not commit the returned files "
                  "(comments) or the labelling folder (passage text).")
        elif a.cmd == "crowd-score":
            CR.score(a.work, answers)
            print("Next: copy predictions.csv and result_v2.md to "
                  "docs/experiments/impact_pilot/, commit them, and record the result in "
                  "the version 2 protocol, pass or fail, with no change to the criteria.")
        else:
            for step, done, detail in R.status(a.work, answers):
                print(f"[{'x' if done else ' '}] {step}: {detail}")
            nxt = next((s for s, done, _ in R.status(a.work, answers) if not done), None)
            print(f"Next: {nxt}." if nxt else "Every step is done.")
    except R.Refused as e:
        print(f"Refused: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
