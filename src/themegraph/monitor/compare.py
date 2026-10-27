"""`themegraph monitor compare`: two profiles over the same month-ends
(docs/TECH_PILOT_V2.md, "Validation").

Reads each profile's registry, latest release, status.json and coverage report and
writes a Markdown report:
- themes retained, added, lost, split or merged;
- what changed for themes among the base profile's companies;
- naming and evidence for a sample of themes, generic and duplicate themes;
- material the larger profile did not publish, and why (route checks);
- per-company document and price coverage;
- runtime, peak memory, provider requests and cache reuse.

It reads results only; it changes nothing and decides nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .profiles import Profile, effective

GENERIC = set("""quarter quarters revenue revenues margin margins guidance customers customer
demand growth year years operating expenses cash flow team execution priorities outlook
business results performance strong continue continued momentum results fiscal billion
million percent opportunity opportunities strategy investments investment""".split())


def _load(state: Path, site: Path) -> dict:
    reg = json.loads((state / "registry.json").read_text()) if (state / "registry.json").exists() \
        else None
    vec = dict(np.load(state / "vectors.npz")) if (state / "vectors.npz").exists() else {}
    status = json.loads((site / "data" / "status.json").read_text()) \
        if (site / "data" / "status.json").exists() else {}
    index, docs = None, {}
    latest = site / "data" / "latest.json"
    if latest.exists():
        rel = site / "data" / json.loads(latest.read_text())["path"]
        index = json.loads((rel / "index.json").read_text())
        for p in (rel / "themes").glob("*.json"):
            docs[p.stem] = json.loads(p.read_text())
    cov = json.loads((state / "coverage_report.json").read_text()) \
        if (state / "coverage_report.json").exists() else None
    return {"reg": reg, "vec": vec, "status": status, "index": index, "docs": docs, "cov": cov}


def _last(th: dict) -> dict:
    return next((r for r in reversed(th["measures"]) if r.get("present")), {})


def _jac(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def _title(docs: dict, th: dict) -> str:
    d = docs.get(th["id"])
    return d["title"] if d else th["name"]


def _universe(x: dict, e: dict) -> str:
    """What the run used: the released universe (a fixture run's synthetic one),
    falling back to the configured universe before any release exists."""
    u = (x["index"] or {}).get("universe")
    if u:
        return f"{u['name']} (version {u['version']}, {u['n_companies']} companies)"
    return f"{e['universe_name']} (version {e['universe_version']}, {e['n_issuers']} companies)"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def compare_profiles(pa: Profile, state_a: Path, site_a: Path,
                     pb: Profile, state_b: Path, site_b: Path) -> str:
    A, B = _load(Path(state_a), Path(site_a)), _load(Path(state_b), Path(site_b))
    if A["reg"] is None or B["reg"] is None:
        missing = [p.name for p, x in ((pa, A), (pb, B)) if x["reg"] is None]
        return f"# Profile comparison\n\nNo registry yet for: {', '.join(missing)}. " \
               "Run `themegraph monitor refresh --profile <name>` first.\n"
    ra, rb = A["reg"], B["reg"]
    ta = {k: t for k, t in ra["themes"].items() if t["published"]}
    tb = {k: t for k, t in rb["themes"].items() if t["published"]}
    ea, eb = effective(pa), effective(pb)
    fixture = bool((A["index"] or {}).get("fixture") or (B["index"] or {}).get("fixture"))
    fork = rb.get("fork") or {}
    since = fork.get("at_snapshot")
    L = [f"# Profile comparison: {pa.title} vs {pb.title}", ""]
    if fixture:
        L += ["> **Synthetic fixture data.** These results show that the code paths work; they",
              "> are not a validation of the expanded pilot on real companies.", ""]
    same = ra.get("last_snapshot") == rb.get("last_snapshot")
    L += ["## Runs compared", "",
          "| | " + pa.name + " | " + pb.name + " |", "|---|---|---|",
          "| Universe run | " + " | ".join(_universe(x, e) for x, e in ((A, ea), (B, eb))) + " |",
          f"| Themes processed through | {ra.get('last_snapshot')} | {rb.get('last_snapshot')} |",
          f"| Published themes | {len(ta)} | {len(tb)} |",
          f"| Forked from | — | {fork.get('from_profile', '—')} at {since or '—'} |", ""]
    if not same:
        L += ["**Warning:** the two registries end at different month-ends, so the "
              "comparison mixes periods. Refresh both to the same date.", ""]

    # -- inventory ----------------------------------------------------------------
    match = {}
    for k, t in ta.items():
        if k in rb["themes"]:
            match[k] = k
            continue
        best = max(tb.values(), key=lambda u: _jac(t.get("last_members", []),
                                                    u.get("last_members", [])), default=None)
        if best and _jac(t.get("last_members", []), best.get("last_members", [])) >= 0.15:
            match[k] = best["id"]
    lost = [k for k, t in ta.items() if k not in match or
            (rb["themes"][match[k]]["status"] in ("inactive", "merged")
             and t["status"] not in ("inactive", "merged"))]
    added = [k for k in tb if k not in set(match.values())]
    events = [(k, e) for k, t in rb["themes"].items() for e in t.get("lineage", [])
              if since is None or e["t"] > since]
    L += ["## Themes", "",
          f"- Retained: {len(match) - len([k for k in lost if k in match])} of {len(ta)}",
          f"- Added in {pb.name}: {len(added)}",
          f"- Lost or no longer active in {pb.name} only: {len(lost)}"
          + (f" ({', '.join(lost)})" if lost else ""),
          f"- Splits and merges in {pb.name}" + (f" since the fork ({since})" if since else "")
          + f": {len(events)}"]
    L += [f"  - {k}: {e['event'].replace('_', ' ')} {e['other']} ({e['t'][:10]})"
          for k, e in events]
    L.append("")
    if added:
        L += ["### Added themes", "",
              "| ID | Route | Status | Title | Companies (new / previously covered) | Sectors |",
              "|---|---|---|---|---|---|"]
        base_syms = {c["symbol"] for c in (A["cov"] or {}).get("companies", [])}
        for k in added:
            t, m = tb[k], _last(tb[k])
            iss = m.get("issuers", [])
            new = [s for s in iss if base_syms and s not in base_syms]
            L.append(f"| {k} | {t['route']} | {t['status']} | {_title(B['docs'], t)} | "
                     f"{len(new)} / {len(iss) - len(new)} | {', '.join(m.get('sectors', []))} |")
        L.append("")

    # -- base themes ---------------------------------------------------------------
    L += [f"## Themes among {pa.name}'s companies", "",
          "Membership, status and basket of each retained theme in the two profiles. "
          "Original companies are those in the base profile's universe.", "",
          "| ID | Status | Companies | Original companies (added / dropped) | New companies joined "
          "| Basket versions | Emergence flags since fork |", "|---|---|---|---|---|---|---|"]
    base_syms = {c["symbol"] for c in (A["cov"] or {}).get("companies", [])}
    for k, kb in sorted(match.items()):
        a, b = ta[k], rb["themes"][kb]
        ia, ib = set(_last(a).get("issuers", [])), set(_last(b).get("issuers", []))
        orig_b = {s for s in ib if not base_syms or s in base_syms}
        newj = sorted(ib - orig_b)
        fl = [r["t"][:10] for r in b["measures"] if r.get("flagged_comparable")
              and (since is None or r["t"] > since)]
        fa = [r["t"][:10] for r in a["measures"] if r.get("flagged_comparable")
              and (since is None or r["t"] > since)]
        st = a["status"] if a["status"] == b["status"] else f"{a['status']} → {b['status']}"
        plus, minus = ",".join(sorted(orig_b - ia)) or "—", ",".join(sorted(ia - orig_b)) or "—"
        L.append(f"| {k}{'' if kb == k else ' ~ ' + kb} | {st} | {len(ia)} → {len(ib)} | "
                 f"+{plus} / -{minus} | "
                 f"{', '.join(newj) or '—'} | {len(a['versions'])} → {len(b['versions'])} | "
                 f"{len(fa)} → {len(fl)} |")
    L.append("")

    # -- naming, evidence, generic, duplicates --------------------------------------
    L += ["## Naming and evidence (sample)", ""]
    sample = added + [k for k in sorted(tb) if k not in added][:4]
    for k in sample:
        t, d = tb[k], B["docs"].get(k, {})
        lab = d.get("label") or {}
        L.append(f"- **{k} · {d.get('title', t['name'])}** ({lab.get('source', '—')}"
                 f"{', ' + lab['status'] if lab.get('status') else ''}). {d.get('summary', '')} "
                 f"Keywords: {', '.join(t['keywords'][:8])}.")
        for q in t["evidence"].get("latest", [])[:2]:
            L.append(f"  - {q['symbol']} · {q['date']} · {q['role']}: “{q['text'][:160]}…”")
    L.append("")
    generic = []
    for k, t in tb.items():
        g = sum(w in GENERIC for w in t["keywords"][:8])
        if g >= 3:
            generic.append(f"{k} ({g}/8 generic terms: {', '.join(t['keywords'][:8])})")
    active = [k for k, t in tb.items() if t["status"] not in ("inactive", "merged")]
    dups = []
    for i, k in enumerate(active):
        for j in active[i + 1:]:
            cos = float(B["vec"][k] @ B["vec"][j]) if k in B["vec"] and j in B["vec"] else None
            ji = _jac(_last(tb[k]).get("issuers", []), _last(tb[j]).get("issuers", []))
            if (cos is not None and cos >= 0.8) or ji >= 0.6:
                dups.append(f"{k} / {j} (meaning cosine {cos:.2f}, company overlap {ji:.2f})"
                            if cos is not None else f"{k} / {j} (company overlap {ji:.2f})")
    L += ["## Generic or duplicate themes", "",
          "Generic: three or more of the top eight keywords are generic business words. "
          "Duplicate: meaning cosine ≥ 0.8 or company overlap ≥ 0.6 between active themes.", "",
          "- Generic: " + ("; ".join(generic) if generic else "none"),
          "- Possible duplicates: " + ("; ".join(dups) if dups else "none"), ""]

    # -- missed ---------------------------------------------------------------------
    pool = [t for t in rb["themes"].values() if not t["published"] and t.get("route_check")]
    L += [f"## Not published in {pb.name} (sample of what was missed)", "",
          "Topics the monitor tracks but did not publish, with the latest route check. "
          "Established route: present in at least 6 of 9 month-ends, 4+ comparable "
          "companies, coherence at least the month's median. Newly covered: the same bar "
          "on backfilled history for topics carried mostly by newly covered companies.", "",
          "| ID | Keywords | Companies | Months present | Comparable | Coherence (median) | "
          "Newly covered check |", "|---|---|---|---|---|---|---|"]
    for t in sorted(pool, key=lambda t: -t["route_check"]["issuers"])[:12]:
        c = t["route_check"]
        L.append(f"| {t['id']} | {', '.join(t['keywords'][:5])} | {c['issuers']} | "
                 f"{c['months_present']}/{c['of_last']} | {c['comparable_issuers']} | "
                 f"{c['coherence']} ({c['median_coherence']}) | {c.get('newly_covered') or '—'} |")
    in_theme = {s for t in tb.values() if t["status"] not in ("inactive", "merged")
                for s in _last(t).get("issuers", [])}
    covb = (B["cov"] or {}).get("companies", [])
    new_cos = [c for c in covb if c["symbol"] not in base_syms]
    orphans = [c["symbol"] for c in new_cos if c["symbol"] not in in_theme]
    L += ["", f"Newly covered companies in no active published theme: {len(orphans)} of "
          f"{len(new_cos)}" + (f" ({', '.join(orphans)})" if orphans else "") + ".", ""]

    # -- coverage --------------------------------------------------------------------
    L += [f"## Per-company coverage ({pb.name}, newly covered companies)", "",
          "| Company | Segment | Documents | First – last document | Price sessions | "
          "First – last price | Flag |", "|---|---|---|---|---|---|---|"]
    for c in new_cos:
        flag = "no documents" if not c["documents"] else "no prices" if not c["price_sessions"] \
            else ""
        L.append(f"| {c['symbol']} | {c['segment']} | {c['documents']} | "
                 f"{c['first_document'] or '—'} – {c['last_document'] or '—'} | "
                 f"{c['price_sessions']} | {c['first_price'] or '—'} – {c['last_price'] or '—'} | "
                 f"{flag} |")
    old = [c for c in covb if c["symbol"] in base_syms]
    no_docs = [c["symbol"] for c in old if not c["documents"]]
    L += ["", f"Previously covered companies: {len(old)}; without documents: "
          f"{', '.join(no_docs) or 'none'}.", ""]

    # -- metrics -----------------------------------------------------------------------
    L += ["## Run metrics (latest refresh of each profile)", "",
          "| | " + pa.name + " | " + pb.name + " |", "|---|---|---|"]
    for name, key in (("Total seconds", "seconds"), ("Peak memory (MB, whole process)",
                                                      "peak_rss_mb")):
        L.append(f"| {name} | {A['status'].get('metrics', {}).get(key, '—')} | "
                 f"{B['status'].get('metrics', {}).get(key, '—')} |")
    steps = sorted(set(A["status"].get("last_attempt", {})) | set(B["status"].get(
        "last_attempt", {})))
    for s in steps:
        L.append(f"| {s} (s) | {A['status'].get('last_attempt', {}).get(s, {}).get('seconds', '—')}"
                 f" | {B['status'].get('last_attempt', {}).get(s, {}).get('seconds', '—')} |")
    ua, ub = A["status"].get("metrics", {}).get("usage", {}), B["status"].get(
        "metrics", {}).get("usage", {})
    for k in sorted(set(ua) | set(ub)):
        L.append(f"| {k.replace('_', ' ')} | {ua.get(k, 0)} | {ub.get(k, 0)} |")
    L += ["", "Counts are for the latest refresh only. Provider requests are paid or "
          "rate-limited calls; cache hits are responses served from data/raw. Embedding and "
          "cluster reuse come from the shared content-addressed caches.", ""]
    return "\n".join(L)
