"""Standalone interactive HTML report from a run directory (no network needed to view)."""

from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import UniverseConfig

# Segments fold into seven colour groups (categorical cap); ticker labels carry identity.
GROUPS = {
    "memory": "Memory", "compute": "Logic & IP", "foundry": "Logic & IP", "eda": "Logic & IP",
    "analog": "Analog & power semis", "equipment": "Equipment",
    "cloud": "Systems", "devices": "Systems", "servers": "Systems",
    "networking": "Networking", "power": "Power & EV", "ev": "Power & EV",
}
GROUP_ORDER = ["Memory", "Logic & IP", "Analog & power semis", "Equipment", "Systems",
               "Networking", "Power & EV"]
TOP_EDGES_PER_NODE = 3


def _top_edges(edges: pd.DataFrame) -> pd.DataFrame:
    """Keep each node's strongest edges per (t, layer) so the page stays small."""
    if edges.empty:
        return edges
    a = edges.rename(columns={"a": "n", "b": "m"})
    b = edges.rename(columns={"b": "n", "a": "m"})
    both = pd.concat([a, b], ignore_index=True)
    both = both.sort_values("weight", ascending=False)
    top = both.groupby(["t", "layer", "n"]).head(TOP_EDGES_PER_NODE)
    top = top.assign(a=np.minimum(top["n"], top["m"]), b=np.maximum(top["n"], top["m"]))
    return top.drop_duplicates(["t", "layer", "a", "b"])[["t", "layer", "a", "b", "weight"]]


def _clean(o):
    if o is None or o is pd.NaT:
        return None
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, np.ndarray)):
        return [_clean(v) for v in o]
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), 4)
    return o


def collect(run_dir: Path, universe: UniverseConfig) -> dict:
    run_dir = Path(run_dir)
    read = lambda n: pd.read_parquet(run_dir / n) if (run_dir / n).exists() else pd.DataFrame()  # noqa: E731
    manifest = json.loads((run_dir / "manifest.json").read_text())
    results = json.loads((run_dir / "results.json").read_text()) \
        if (run_dir / "results.json").exists() else None
    edges = _top_edges(read("edges.parquet"))
    snaps = sorted(edges["t"].unique()) if len(edges) else []
    by_t: dict[str, dict[str, list]] = {}
    for (t, layer), g in edges.groupby(["t", "layer"]):
        # Layers live on different scales; the graph thresholds a per-snapshot
        # rescaled weight (strongest edge = 1) and shows the raw weight on hover.
        top = g["weight"].max() or 1.0
        g = g.assign(scaled=g["weight"] / top)
        by_t.setdefault(pd.Timestamp(t).strftime("%Y-%m-%d"), {})[layer] = \
            g[["a", "b", "scaled", "weight"]].round(4).values.tolist()
    nodes = [{"id": i.symbol, "segment": i.segment, "group": GROUPS.get(i.segment, "Systems"),
              "role": i.role} for i in universe.issuers]

    exposure, topics, ledger = read("exposure.parquet"), read("topics.parquet"), \
        read("candidate_ledger.parquet")
    tracks = []
    windows = read("windows.parquet")
    if len(exposure):
        # Same denominator as the detector: all passages in the window, not only the
        # clustered ones (older runs without windows.parquet fall back to clustered).
        totals = (windows.set_index("t")["n_passages"] if len(windows)
                  else exposure.groupby("t")["n_passages"].sum())
        s = exposure.groupby(["track_id", "t"])["n_passages"].sum().reset_index()
        s["share"] = s["n_passages"] / s["t"].map(totals)
        flagged = set(ledger["track_id"]) if len(ledger) else set()
        peak = s.groupby("track_id")["share"].max().sort_values(ascending=False)
        chosen = list(dict.fromkeys([t for t in peak.index if t in flagged] +
                                    list(peak.index[:12])))[:24]
        for tid in chosen:
            ss = s.loc[s["track_id"] == tid]
            kw = topics.loc[topics["track_id"] == tid].sort_values("n_passages")
            words = list(kw["keywords"].iloc[-1]) if len(kw) else []
            ex = exposure.loc[exposure["track_id"] == tid]
            top_issuers = ex.groupby("symbol")["n_passages"].sum().nlargest(6).index.tolist()
            flags = ledger.loc[ledger["track_id"] == tid, "t"] if len(ledger) else []
            tracks.append({
                "id": int(tid), "keywords": words[:6], "issuers": top_issuers,
                "points": [[pd.Timestamp(t).strftime("%Y-%m-%d"), float(v)]
                           for t, v in zip(ss["t"], ss["share"], strict=True)],
                "first_flag": pd.Timestamp(min(flags)).strftime("%Y-%m-%d") if len(flags)
                else None,
            })
    cands = []
    if len(ledger):
        firsts = ledger.loc[ledger["t"] == ledger["t_first_flag"]]
        matches = read("eval_matches.parquet")
        matched = {}
        if len(matches) and "candidate_id" in matches:
            matched = dict(zip(matches["candidate_id"], matches["theme"], strict=False))
        expl = {}
        path = run_dir / "explanations.json"
        if path.exists():
            expl = {e["candidate_id"]: e for e in json.loads(path.read_text())}
        for r in firsts.itertuples():
            e = expl.get(r.candidate_id, {})
            cands.append({
                "id": r.candidate_id, "t": r.t, "issuers": list(r.issuers),
                "segments": list(r.segments), "keywords": list(e.get("keywords", []))[:6],
                "quotes": e.get("quotes", []),
                "alignment_pct": r.market_affinity_null_pct, "label": matched.get(r.candidate_id),
            })
    return _clean({
        "manifest": manifest, "results": results, "nodes": nodes,
        "groups": GROUP_ORDER, "snapshots": [pd.Timestamp(t).strftime("%Y-%m-%d")
                                             for t in snaps],
        "edges": by_t, "tracks": tracks, "candidates": cands,
        "matches": read("eval_matches.parquet").to_dict(orient="records"),
        "comovement": read("eval_comovement.parquet").to_dict(orient="records"),
        "returns": read("eval_returns.parquet").to_dict(orient="records"),
    })


def build_report(run_dir: Path, universe: UniverseConfig, title: str = "Theme graph") -> Path:
    data = collect(run_dir, universe)
    page = TEMPLATE.replace("__TITLE__", html.escape(title)).replace(
        "__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    out = Path(run_dir) / "report.html"
    out.write_text(page)
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);
--l1:#2a78d6;--l2:#eb6834;--l3:#1baf7a;--warn:#c98500;
--g1:#2a78d6;--g2:#eb6834;--g3:#1baf7a;--g4:#eda100;--g5:#e87ba4;--g6:#008300;--g7:#6250d6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;
--axis:#383835;--ring:rgba(255,255,255,.10);--l1:#3987e5;--l2:#d95926;--l3:#199e70;
--g1:#3987e5;--g2:#d95926;--g3:#199e70;--g4:#c98500;--g5:#d55181;--g6:#008300;--g7:#9085e9}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);
--l1:#3987e5;--l2:#d95926;--l3:#199e70;--g1:#3987e5;--g2:#d95926;--g3:#199e70;--g4:#c98500;
--g5:#d55181;--g6:#008300;--g7:#9085e9}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}
.sub{color:var(--ink2);margin:0 0 16px}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:16px}
.warn{border-left:3px solid var(--warn);padding:8px 12px;background:var(--surface);
margin:12px 0;color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile .v{font-size:24px;font-weight:600}.tile .k{color:var(--ink2);font-size:12px}
.controls{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;margin-bottom:8px}
.controls label{display:flex;gap:6px;align-items:center;color:var(--ink2)}
input[type=range]{width:220px;max-width:100%}
.swatch{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:middle;
margin-right:4px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;color:var(--ink2);font-size:12px;margin-top:8px}
svg text{fill:var(--ink2);font-size:10px}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.spark h3{font-size:13px;margin:0 0 2px}.spark p{margin:0;color:var(--ink2);font-size:12px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink2);font-weight:600}
#tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--ring);
border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);
display:none;max-width:280px;z-index:5}
.pill{display:inline-block;padding:0 6px;border-radius:9px;border:1px solid var(--ring);
font-size:11px;color:var(--ink2);margin:1px 2px 1px 0}
</style></head>
<body><main>
<h1>__TITLE__</h1>
<p class="sub" id="meta"></p>
<div id="warnings"></div>

<h2>Results</h2>
<div class="tiles" id="tiles"></div>

<h2>Multi-layer graph</h2>
<div class="card">
  <div class="controls">
    <label>Snapshot <input type="range" id="tslider" min="0" value="0"><b id="tlabel"></b></label>
    <label>Min relative weight <input type="range" id="wslider" min="0" max="100" value="50">
      <b id="wlabel"></b></label>
    <span id="layers"></span>
  </div>
  <div class="scroll"><svg id="graph" viewBox="0 0 760 640" width="100%"
    style="max-width:760px;display:block;margin:auto" role="img"
    aria-label="Issuer network for the selected snapshot"></svg></div>
  <div class="legend" id="glegend"></div>
  <p class="sub" style="margin-top:8px">Each layer keeps every issuer's three strongest
  edges per snapshot; weights are relative to that layer's strongest edge. Click an
  issuer to highlight its links.</p>
</div>

<h2>Topic tracks</h2>
<p class="sub">Share of all passages in each window (the detector's denominator) per
tracked topic. Topics that produced a candidate are listed first; the dashed line marks
the first flag. Keywords here are a <b>retrospective</b> label from each topic's largest
window over the whole run, which may come after the flag; the candidate table below
shows what was known at the flag.</p>
<div class="grid2" id="tracks"></div>

<h2>Theme candidates</h2>
<div class="card scroll"><table id="cands"></table></div>

<h2>Q1 · Detection against labelled themes</h2>
<div class="card scroll"><table id="matches"></table></div>

<h2>Q2 · Co-movement after first flag</h2>
<p class="sub">Change in mean market affinity among a candidate's issuers over the next
h sessions, ranked against segment-matched random sets of non-members (0.5 = no
difference from chance).</p>
<div class="card scroll"><table id="como"></table></div>

<h2>Q2 · Forward returns (secondary)</h2>
<div class="card scroll"><table id="rets"></table></div>

<h2>Limitations</h2>
<div class="card" style="color:var(--ink2)"><ul style="margin:0;padding-left:18px">
<li>Candidates, thresholds and labels are a research specification, not a trading system.
Few independent episodes exist; percentiles and p-values are descriptive.</li>
<li>Any holdout run is pseudo-out-of-sample: its history was known when the method was
designed. Only the forward log is clean evidence.</li>
<li>Coverage differs by issuer (non-US issuers have no SEC text). Missing text is
unknown, not negative.</li>
<li>Provider histories are present-day vintages; revisions and survivorship can bias
results despite point-in-time availability rules.</li>
</ul></div>
</main>
<div id="tip"></div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const fmt = (v, d=2) => v == null ? "–" : (typeof v === "number" ? v.toFixed(d) : v);
const pct = v => v == null ? "–" : (v*100).toFixed(0) + "%";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const tip = $("#tip");
function showTip(e, h){ tip.innerHTML = h; tip.style.display = "block";
  const x = Math.min(e.clientX + 12, innerWidth - 290); tip.style.left = x + "px";
  tip.style.top = (e.clientY + 12) + "px"; }
function hideTip(){ tip.style.display = "none"; }

// ---- meta + warnings
const M = D.manifest;
$("#meta").textContent = `Period: ${M.period} · through ${M.end.slice(0,10)} · ${M.snapshots}
 monthly snapshots · embeddings: ${M.embedder} · ledger sha256 ${M.ledger_sha256.slice(0,12)}…`;
const warns = [];
if (!M.research_embedder) warns.push(`Embeddings came from <b>${esc(M.embedder)}</b>, not the
  configured research model. Treat text-layer results as a pipeline check only.`);
if (M.period === "holdout") warns.push("Holdout run: pseudo-out-of-sample (history was known when the method was designed).");
$("#warnings").innerHTML = warns.map(w => `<div class="warn">${w}</div>`).join("");

// ---- tiles
const R = D.results || {q1:{}, q2:{comovement:{}}};
const q1 = R.q1 || {}, como = (R.q2 || {}).comovement || {};
const tiles = [
  ["Detection recall", q1.detection_recall == null ? "–" : pct(q1.detection_recall),
   `${q1.themes_evaluable ?? 0} labelled themes evaluable` +
   (q1.themes_not_evaluable ? `, ${q1.themes_not_evaluable} missing dates` : "")],
  ["Median lead", q1.median_lead_days == null ? "–" : `${Math.round(q1.median_lead_days)} days`,
   "first flag → consensus marker"],
  ["Unmatched candidates", q1.unmatched_candidates ?? "–",
   `${fmt(q1.unmatched_candidates_per_year,1)} per year; need blind review`],
  ["Negative controls flagged", q1.negative_controls_flagged ?? "–", "hype that faded"],
];
for (const [h, v] of Object.entries(como))
  tiles.push([`Co-movement percentile, ${h} sessions`, fmt(v.group_mean_pct ?? v.mean_pct, 2),
              `${v.n_episodes ?? v.n} episodes in ${v.n_groups ?? "?"} dependence groups; ` +
              `grouped sign-flip p=${fmt(v.p_value_grouped ?? v.p_value, 3)} (descriptive)`]);
$("#tiles").innerHTML = tiles.map(([k, v, s]) =>
  `<div class="card tile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>
   <div class="k">${esc(s)}</div></div>`).join("");

// ---- graph
const LAYER_COLORS = ["var(--l1)", "var(--l2)", "var(--l3)"];
const layerNames = [...new Set(Object.values(D.edges).flatMap(o => Object.keys(o)))].sort();
const active = new Set(layerNames.filter(l => !l.startsWith("macro")));
$("#layers").innerHTML = layerNames.map((l, i) => `<label><input type="checkbox" data-l="${l}"
  ${active.has(l) ? "checked" : ""}><span class="swatch" style="background:${LAYER_COLORS[i % 3]}"></span>${l}</label>`).join("");
document.querySelectorAll("#layers input").forEach(cb => cb.onchange = () => {
  cb.checked ? active.add(cb.dataset.l) : active.delete(cb.dataset.l); drawGraph(); });
const gIndex = Object.fromEntries(D.groups.map((g, i) => [g, i]));
const nodes = D.nodes.slice().sort((a, b) => gIndex[a.group] - gIndex[b.group] || a.id.localeCompare(b.id));
const W = 760, H = 640, cx = W/2, cy = H/2 - 6, rad = 245;
const pos = {}; nodes.forEach((n, i) => { const a = 2*Math.PI*i/nodes.length - Math.PI/2;
  pos[n.id] = [cx + rad*Math.cos(a), cy + rad*Math.sin(a), a]; });
$("#glegend").innerHTML = D.groups.map((g, i) =>
  `<span><span class="dot" style="background:var(--g${i+1})"></span>${esc(g)}</span>`).join("") +
  `<span>· hollow = adjacency ring (text only in evaluation)</span>`;
let focus = null;
const ts = $("#tslider"); ts.max = Math.max(0, D.snapshots.length - 1); ts.value = ts.max;
ts.oninput = drawGraph; $("#wslider").oninput = drawGraph;
function drawGraph(){
  const t = D.snapshots[+ts.value]; $("#tlabel").textContent = t || "–";
  const wmin = +$("#wslider").value / 100; $("#wlabel").textContent = wmin.toFixed(2);
  let s = "";
  const E = (D.edges[t] || {});
  layerNames.forEach((l, li) => { if (!active.has(l)) return;
    for (const [a, b, w, raw] of (E[l] || [])) { if (w < wmin || !pos[a] || !pos[b]) continue;
      const on = !focus || a === focus || b === focus;
      const [x1, y1] = pos[a], [x2, y2] = pos[b];
      s += `<path d="M${x1},${y1} Q${cx},${cy} ${x2},${y2}" fill="none" stroke="${LAYER_COLORS[li % 3]}"
        stroke-width="${on ? 1 + 2*w : 1}" stroke-opacity="${on ? 0.25 + 0.6*w : 0.06}"
        data-tip="${esc(l)}: ${a}–${b} · weight ${raw.toFixed(3)} (${(w*100).toFixed(0)}% of strongest)"/>`; } });
  for (const n of nodes) { const [x, y, a] = pos[n.id]; const c = `var(--g${gIndex[n.group]+1})`;
    const lx = cx + (rad + 14)*Math.cos(a), ly = cy + (rad + 14)*Math.sin(a);
    const anchor = Math.cos(a) > 0.2 ? "start" : Math.cos(a) < -0.2 ? "end" : "middle";
    const dim = focus && focus !== n.id ? 0.35 : 1;
    s += `<g opacity="${dim}" data-node="${n.id}" style="cursor:pointer" data-tip="${n.id} · ${esc(n.segment)} · ${n.role}">
      <circle cx="${x}" cy="${y}" r="9" fill="transparent"/>
      <circle cx="${x}" cy="${y}" r="5" fill="${n.role === "core" ? c : "var(--surface)"}"
        stroke="${c}" stroke-width="2"/>
      <text x="${lx}" y="${ly + 3}" text-anchor="${anchor}">${n.id}</text></g>`; }
  const svg = $("#graph"); svg.innerHTML = s;
  svg.querySelectorAll("[data-node]").forEach(g => g.onclick = () => {
    focus = focus === g.dataset.node ? null : g.dataset.node; drawGraph(); });
}
$("#graph").addEventListener("mousemove", e => { const el = e.target.closest("[data-tip]");
  el ? showTip(e, esc(el.dataset.tip)) : hideTip(); });
$("#graph").addEventListener("mouseleave", hideTip);
drawGraph();

// ---- topic small multiples (one hue; flagged first)
const allT = D.snapshots.length ? D.snapshots : [...new Set(D.tracks.flatMap(t => t.points.map(p => p[0])))].sort();
const tIdx = Object.fromEntries(allT.map((t, i) => [t, i]));
const ymax = Math.max(0.01, ...D.tracks.flatMap(t => t.points.map(p => p[1])));
const sw = 250, sh = 70;
$("#tracks").innerHTML = D.tracks.sort((a, b) => (b.first_flag ? 1 : 0) - (a.first_flag ? 1 : 0))
  .map(tr => {
  const xs = i => 4 + (sw - 8) * i / Math.max(1, allT.length - 1), ys = v => sh - 4 - (sh - 10) * v / ymax;
  const pts = tr.points.filter(p => p[0] in tIdx);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${xs(tIdx[p[0]]).toFixed(1)},${ys(p[1]).toFixed(1)}`).join("");
  const flag = tr.first_flag && tr.first_flag in tIdx ?
    `<line x1="${xs(tIdx[tr.first_flag])}" x2="${xs(tIdx[tr.first_flag])}" y1="2" y2="${sh}"
      stroke="var(--ink2)" stroke-dasharray="3 3"/>` : "";
  return `<div class="card spark"><h3>Topic ${tr.id}${tr.first_flag ? " · flagged " + tr.first_flag : ""}</h3>
    <p>${tr.keywords.map(esc).join(", ")}</p>
    <svg viewBox="0 0 ${sw} ${sh}" width="100%" data-track="${tr.id}">
      <line x1="0" x2="${sw}" y1="${sh-4}" y2="${sh-4}" stroke="var(--axis)"/>${flag}
      <path d="${d}" fill="none" stroke="var(--l1)" stroke-width="2" stroke-linejoin="round"/></svg>
    <p>${tr.issuers.map(s => `<span class="pill">${esc(s)}</span>`).join("")}</p></div>`;
}).join("") || "<p class='sub'>No text layer in this run.</p>";
document.querySelectorAll("[data-track]").forEach(svg => {
  const tr = D.tracks.find(t => String(t.id) === svg.dataset.track);
  svg.addEventListener("mousemove", e => { const r = svg.getBoundingClientRect();
    const i = Math.round((e.clientX - r.left) / r.width * (allT.length - 1));
    const t = allT[Math.max(0, Math.min(allT.length - 1, i))];
    const p = tr.points.find(p => p[0] === t);
    showTip(e, `${t}: ${p ? pct(p[1]) + " of passages" : "not present"}`); });
  svg.addEventListener("mouseleave", hideTip); });

// ---- tables
function table(el, cols, rows){
  el.innerHTML = rows.length ? `<thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="sub">None.</td></tr></tbody>`; }
const pills = a => (a || []).map(s => `<span class="pill">${esc(s)}</span>`).join("");
table($("#cands"), [["Candidate", r => esc(r.id)], ["First flag", r => esc(r.t)],
  ["Issuers", r => pills(r.issuers)],
  ["As-of keywords and quotes", r => esc((r.keywords || []).join(", ")) +
    ((r.quotes || []).length ? `<details><summary>${r.quotes.length} supporting passages</summary>` +
      r.quotes.map(q => `<p><b>${esc(q.symbol)}</b> · ${esc(q.genre || "")} · available ${esc((q.available_at || "").slice(0,10))}` +
        (q.url ? ` · <a href="${esc(q.url)}">source</a>` : "") + `<br>${esc(q.text)}…</p>`).join("") + `</details>` : "")],
  ["Market alignment pct", r => fmt(r.alignment_pct)], ["Matched label", r => esc(r.label || "–")]],
  D.candidates);
table($("#matches"), [["Theme", r => esc(r.theme)], ["Kind", r => esc(r.kind)],
  ["Evaluable", r => r.evaluable ? "yes" : "no (marker dates missing)"],
  ["Detected", r => r.evaluable ? (r.detected ? "yes" : "no") : "–"],
  ["First flag", r => esc(r.first_flag || "–")], ["Consensus", r => esc(r.consensus || "–")],
  ["Lead (days)", r => fmt(r.lead_days, 0)], ["Jaccard", r => fmt(r.jaccard)]], D.matches);
table($("#como"), [["Candidate", r => esc(r.candidate_id)], ["Flag", r => esc(r.t)],
  ["h", r => r.h], ["Issuers", r => r.n], ["Δ affinity", r => fmt(r.delta, 3)],
  ["Null median", r => fmt(r.null_median, 3)], ["Percentile", r => fmt(r.null_pct)]], D.comovement);
table($("#rets"), [["Candidate", r => esc(r.candidate_id)], ["Flag", r => esc(r.t)],
  ["h", r => r.h], ["Status", r => esc(r.status || "")],
  ["Basket return (EW buy-and-hold, next-close entry)", r => fmt(r.basket, 3)],
  ["vs sector ETF", r => fmt(r.excess_vs_benchmark, 3)], ["vs momentum", r => fmt(r.excess_vs_momentum, 3)],
  ["Null percentile", r => fmt(r.null_pct)]], D.returns);
</script></body></html>
"""
