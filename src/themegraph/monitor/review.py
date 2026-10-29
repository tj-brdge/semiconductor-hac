"""`themegraph monitor review`: what to look at before trusting families and
headwinds on real data (docs/MONITOR.md, "Theme families" and "Headwinds").

Reads a profile's registry and writes Markdown: the most similar pairs of active
themes with their shared companies and keywords (where the family cut falls), the
families formed, and each theme's tone with the phrases it matched. Text only:
no return is read, so nothing here can be tuned on performance.
"""

from __future__ import annotations

from pathlib import Path

from .config import MonitorConfig
from .families import ACTIVE, families, overlap_pairs
from .registry import Registry


def _latest(th: dict) -> dict:
    return next((r for r in reversed(th["measures"]) if r.get("present")), {})


def review_report(state_dir: Path, mcfg: MonitorConfig, pairs: int = 40) -> str:
    reg = Registry(state_dir)
    themes = reg.published()
    name = {t["id"]: (t.get("label") or {}).get("title") or t["name"] for t in themes}
    cut = mcfg.families.min_cosine
    out = [f"# Theme review: {state_dir}", "",
           f"Active published themes: {sum(t['status'] in ACTIVE for t in themes)}. "
           f"Family cut: cosine >= {cut} (average linkage).", "",
           "## Most similar pairs", "",
           "| cosine | theme A | theme B | company overlap | shared keywords | family |",
           "|---|---|---|---|---|---|"]
    fam = families(themes, reg.vectors, cut)
    every = overlap_pairs(themes, reg.vectors, floor=-1.0)
    edges = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9]
    hist = [sum(r["cosine"] >= e for r in every) for e in edges]
    out.insert(-4, "Pairs at or above each cosine: " + ", ".join(
        f">={e}: {n}" for e, n in zip(edges, hist, strict=True)) + f" (of {len(every)}).")
    out.insert(-4, "")
    for r in [r for r in every if r["cosine"] >= 0.5][:pairs]:
        same = fam.get(r["a"], {}).get("family") == fam.get(r["b"], {}).get("family")
        out.append(f"| {r['cosine']:.3f} | {r['a']} {name[r['a']]} | {r['b']} "
                   f"{name[r['b']]} | {r['company_jaccard']} | "
                   f"{', '.join(r['shared_keywords']) or '—'} | {'same' if same else '—'} |")
    out += ["", "## Families with more than one theme", ""]
    leads = sorted({v["family"] for v in fam.values()})
    multi = [lead for lead in leads if len(fam[lead]["members"]) > 1]
    for lead in multi:
        out.append(f"- **{lead} {name[lead]}**: " + "; ".join(
            f"{r['id']} {name[r['id']]} (cosine {r['cosine']:.2f}, "
            f"{len(r['shared_companies'])} shared companies)"
            for r in fam[lead]["related"]))
    if not multi:
        out.append("None at this cut.")
    out += ["", "## Tone", "",
            f"Headwind: at least {mcfg.tone.min_share:.0%} of company passages negative and "
            f"{mcfg.tone.min_ratio}x the genre-matched rate.", "",
            "| theme | direction | negative share | ratio | positive share | phrases |",
            "|---|---|---|---|---|---|"]
    rows = [(t, _latest(t).get("tone") or {}) for t in themes if t["status"] in ACTIVE]
    for t, tn in sorted(rows, key=lambda x: -(x[1].get("negative_ratio") or 0)):
        out.append(f"| {t['id']} {name[t['id']]} | {tn.get('direction', '—')} | "
                   f"{tn.get('negative_share', '—')} | {tn.get('negative_ratio', '—')} | "
                   f"{tn.get('positive_share', '—')} | "
                   f"{', '.join(tn.get('headwind_terms') or []) or '—'} |")
    return "\n".join(out) + "\n"
