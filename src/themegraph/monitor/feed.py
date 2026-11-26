"""`themegraph monitor feed`: every published theme in one Markdown file, for a
person (or Claude) to read and comment on (docs/OPERATIONS.md).

For each theme: its title and where the title came from, the summary, keywords,
companies, tone and a few quoted passages. The top of the file says how titles
were written and why any are still keywords. Text only: no price or return is
read, so nothing here can be tuned on performance. No credential is written.
"""

from __future__ import annotations

import importlib.util
import json
import os
from collections import Counter
from pathlib import Path

from .config import MonitorConfig
from .families import groups, overlap_pairs
from .naming import ACTIVE, name_groups
from .registry import Registry

SOURCE = {"claude": "written by Claude", "curated": "curated",
          "keywords": "automatic (keywords)"}


def _latest(th: dict) -> dict:
    return next((r for r in reversed(th["measures"]) if r.get("present")), {})


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def label_setup(site_dir: Path | None = None, env=None) -> list[str]:
    """Why written titles are or are not being produced on this machine."""
    env = os.environ if env is None else env
    out = ["- anthropic package: " + ("installed" if importlib.util.find_spec("anthropic")
                                       else "NOT installed (run `uv sync` or "
                                            "`pip install -e .`)"),
           "- ANTHROPIC_API_KEY in this shell: " + (
               "set" if env.get("ANTHROPIC_API_KEY") else
               "NOT set (run `source ~/.themegraph.env` first; the file needs `export`)")]
    status = (Path(site_dir) / "data" / "status.json") if site_dir else None
    if status is not None and status.exists():
        warn = (json.loads(status.read_text()).get("warnings") or {}).get("labels")
        out.append("- last refresh: " + (f"labels warning: {warn}" if warn
                                         else "no labels warning"))
    return out


def group_lines(reg: Registry, mcfg: MonitorConfig) -> list[str]:
    """Topics (groups of active themes) at the configured cut, and how many pairs of
    active themes sit at each similarity: what the cut is judged against."""
    themes = reg.published()
    cut = mcfg.groups.min_cosine
    grp = groups(themes, reg.vectors, cut, mcfg.families.min_cosine)
    name_groups(grp, themes, dict(reg.data.get("group_names", {})))
    title = {t["id"]: (t.get("label") or {}).get("title") or t["name"] for t in themes}
    pairs = overlap_pairs(themes, reg.vectors, floor=-1.0)
    edges = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85]
    out = ["## Topics (groups of active themes)", "",
           f"Cut: cosine >= {cut} (average linkage). {len(grp)} topics hold "
           f"{sum(len(g['members']) for g in grp)} themes. Pairs of active themes at or "
           "above each cosine: " + ", ".join(
               f">={e}: {sum(r['cosine'] >= e for r in pairs)}" for e in edges)
           + f" (of {len(pairs)}).", ""]
    for g in grp:
        how = ("written by Claude" if g["name_source"] == "claude"
               else "named after its lead theme")
        out.append(f"- **{g['name']}** ({how}; average cosine {g['avg_cosine']}): "
                   + "; ".join(f"{i} {title[i]}" for i in g["members"]))
    if not grp:
        out.append("None at this cut.")
    return [*out, ""]


def feed(state_dir: Path, site_dir: Path | None = None, quotes: int = 3,
         quote_chars: int = 500, env=None, mcfg: MonitorConfig | None = None) -> str:
    reg = Registry(state_dir)
    themes = sorted(reg.published(), key=lambda t: (t["status"] not in ACTIVE, t["id"]))
    labs = [t.get("label") or {} for t in themes]
    by = Counter("ambiguous" if lab.get("status") == "ambiguous"
                 else lab.get("source") or "none" for lab in labs)
    out = [f"# Theme feed: {state_dir}", "",
           f"{len(themes)} published themes "
           f"({sum(t['status'] in ACTIVE for t in themes)} active). Titles: "
           + ", ".join(f"{n} {k}" for k, n in sorted(by.items())) + ".", "",
           "## How titles are being written", "", *label_setup(site_dir, env), ""]
    if mcfg is not None:
        out += group_lines(reg, mcfg)
    for t, lab in zip(themes, labs, strict=True):
        m = _latest(t)
        tone = (m.get("tone") or {}).get("direction", "—")
        src = SOURCE.get(lab.get("source"), "none")
        if lab.get("status") == "ambiguous":
            src = "automatic (Claude's answer was not used)"
        out += [f"## {t['id']} — {lab.get('title') or t['name']}", "",
                f"- Title: {src}",
                f"- Summary: {lab.get('summary') or '—'}",
                f"- Keywords: {', '.join(t.get('keywords', [])[:8]) or '—'}",
                f"- Status: {t['status']} · entered by {t.get('route', '—')} · "
                f"tone {tone} · first seen {str(t.get('first_seen', '—'))[:10]}",
                f"- Companies ({m.get('n_issuers', 0)}): "
                f"{', '.join(m.get('issuers', [])) or '—'}"]
        if lab.get("note"):
            rej = lab.get("rejected") or {}
            out.append(f"- Not used because: {lab['note']}"
                       + (f" (Claude's title: \"{rej['title']}\")" if rej.get("title") else ""))
        ev = (t.get("evidence") or {}).get("latest", [])[:quotes]
        if ev:
            out += ["", "Passages:"]
            out += [f"- {q['symbol']} · {str(q.get('date', ''))[:10]} · {q.get('role', '')}: "
                    f"\"{_clip(q['text'], quote_chars)}\"" for q in ev]
        out.append("")
    return "\n".join(out)
