// Charts for the theme monitor: a responsive line chart with a crosshair readout
// (pointer and keyboard) and a small static sparkline. No dependencies.
// Series identity is never colour alone: legend, line style (benchmarks dashed),
// end labels where there is room, the readout, and a table view on the page.

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const fmtDate = (s, short = false) => {
  if (!s) return "—";
  const [y, m, d] = String(s).slice(0, 10).split("-").map(Number);
  return short ? `${MONTHS[m - 1]} ${y}` : `${d} ${MONTHS[m - 1]} ${y}`;
};

function niceTicks(lo, hi, n = 5) {
  const span = hi - lo || 1, raw = span / n, p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => span / s <= n) || 10 * p;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

/** Small static chart: basket (solid) vs one benchmark (dashed), live part shaded. */
export function sparkline({ dates, theme, benchmark, liveFrom, label }) {
  const pts = dates.map((d, i) => [i, theme[i], benchmark[i]]).filter((p) => p[1] != null);
  if (pts.length < 2) return "";
  const vals = pts.flatMap((p) => [p[1], p[2]]).filter((v) => v != null);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.08 || 1;
  const W = 100, H = 30, n = dates.length - 1 || 1;
  const X = (i) => (i / n) * W, Y = (v) => H - ((v - lo + pad) / (hi - lo + 2 * pad)) * H;
  const line = (k) => pts.filter((p) => p[k] != null).map((p) => `${X(p[0]).toFixed(2)},${Y(p[k]).toFixed(2)}`).join(" ");
  const li = liveFrom ? dates.findIndex((d) => d >= liveFrom) : -1;
  const band = li >= 0 ? `<rect x="${X(li)}" y="0" width="${W - X(li)}" height="${H}" fill="var(--live-band)"></rect>` : "";
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${label}">
    ${band}<polyline points="${line(2)}" fill="none" stroke="var(--series-2)" stroke-width="1.5" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"></polyline>
    <polyline points="${line(1)}" fill="none" stroke="var(--series-1)" stroke-width="2" vector-effect="non-scaling-stroke"></polyline></svg>`;
}

/**
 * Interactive line chart.
 * rows:   [{date, basis, v: {key: value}}] already rebased by the caller
 * series: [{key, label, color, dash, width, missing}] — a null value is a gap: the
 *          line breaks there (it is never drawn across it) and the readout shows
 *          `missing` (default "no price")
 * format: value -> text for the readout
 * markers: [{index, label, kind}] events drawn at rows[index] (the caller maps each
 *          event to its row and leaves out events outside the rows). kind "up"
 *          draws ▲, "down" ▼ (with their own colours), anything else a navy ▼.
 * base:    a reference value drawn as a dashed line and kept in range (100 for an
 *          index; null for none)
 * tick:    value -> axis and end-label text
 * describe: row -> what kind of day it is, for the readout
 * Returns {destroy()}.
 */
const GLYPH = { up: "▲", down: "▼" };
const KIND_TEXT = { live: "live", hindsight: "hindsight, before the basket existed" };

export function lineChart(host, { rows, series, format, title, announce, markers = [],
  base = 100, tick = (v) => v.toFixed(0),
  describe = (r) => KIND_TEXT[r.basis] || "simulated" }) {
  host.textContent = "";
  host.classList.add("chart");
  const svg = el("svg", { tabindex: "0", role: "group", "aria-label": `${title}. Use the left and right arrow keys to read values by date.` }, host);
  const tip = document.createElement("div");
  tip.className = "tip";
  tip.hidden = true;
  host.appendChild(tip);
  let idx = null, geom = null;
  const at = new Map(); // row index -> events (same-row events merge)
  for (const m of markers) at.set(m.index, [...(at.get(m.index) || []), m]);

  function draw() {
    const width = Math.max(host.clientWidth, 280);
    const narrow = width < 560;
    const H = narrow ? 230 : 300;
    const M = { l: 40, r: narrow ? 10 : 92, t: at.size ? 34 : 12, b: 28 };
    svg.setAttribute("viewBox", `0 0 ${width} ${H}`);
    svg.setAttribute("height", H);
    svg.textContent = "";
    const vals = rows.flatMap((r) => series.map((s) => r.v[s.key])).filter((v) => v != null);
    const ref = base == null ? [] : [base];
    let lo = Math.min(...vals, ...ref), hi = Math.max(...vals, ...ref);
    const pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;
    const n = rows.length - 1 || 1;
    const X = (i) => M.l + ((width - M.l - M.r) * i) / n;
    const Y = (v) => M.t + (H - M.t - M.b) * (1 - (v - lo) / (hi - lo));
    geom = { X, Y, M, width, H };

    // Shade each run of live days (a day's return is live when the day is) and the
    // hindsight stretch before a basket existed.
    for (const [kind, cls, label] of [["live", "band", "Live"], ["hindsight", "band band-hind", "Hindsight"]]) {
      let labelled = false;
      for (let i = 0; i < rows.length; i++) {
        if (rows[i].basis !== kind || (i && rows[i - 1].basis === kind)) continue;
        let j = i;
        while (j + 1 < rows.length && rows[j + 1].basis === kind) j++;
        const x = X(Math.max(kind === "live" ? i - 1 : i, 0)), x2 = X(kind === "live" ? j : j + 1);
        el("rect", { class: cls, x, y: M.t, width: Math.max(x2 - x, 1), height: H - M.t - M.b }, svg);
        if (!labelled) {
          const t = el("text", { class: "band-label", x: x + 6, y: M.t + 14 }, svg);
          t.textContent = label;
          labelled = true;
        }
      }
    }
    const grid = el("g", { class: "grid axis" }, svg);
    for (const v of niceTicks(lo, hi, narrow ? 4 : 5)) {
      el("line", { x1: M.l, x2: width - M.r, y1: Y(v), y2: Y(v) }, grid);
      const t = el("text", { x: M.l - 6, y: Y(v) + 4, "text-anchor": "end" }, grid);
      t.textContent = tick(v);
    }
    if (base != null) el("line", { class: "base", x1: M.l, x2: width - M.r, y1: Y(base), y2: Y(base) }, svg);
    const ax = el("g", { class: "axis" }, svg);
    const k = narrow ? 3 : 5;
    for (let j = 0; j < k; j++) {
      const i = Math.round((n * j) / (k - 1));
      const t = el("text", { x: X(i), y: H - 8, "text-anchor": j === 0 ? "start" : j === k - 1 ? "end" : "middle" }, ax);
      t.textContent = fmtDate(rows[i].date, true);
    }
    // benchmarks first, the basket on top
    for (const s of [...series].reverse()) {
      // One run of consecutive values per sub-path: a gap breaks the line.
      let d = "", run = 0;
      const lone = [];
      rows.forEach((r, i) => {
        const v = r.v[s.key];
        if (v == null) { if (run === 1) lone.push(i - 1); run = 0; return; }
        d += `${run ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
        run += 1;
      });
      if (run === 1) lone.push(rows.length - 1);
      el("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width || 2,
        "stroke-dasharray": s.dash || "none", "stroke-linejoin": "round" }, svg);
      for (const i of lone) // a value between two gaps would otherwise be invisible
        el("circle", { cx: X(i), cy: Y(rows[i].v[s.key]), r: (s.width || 2) * 1.2, fill: s.color }, svg);
    }
    if (!narrow) { // end labels, nudged apart
      const last = rows[rows.length - 1];
      const ends = series.filter((s) => last.v[s.key] != null).map((s) => ({ s, y: Y(last.v[s.key]) })).sort((a, b) => a.y - b.y);
      for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;
      const g = el("g", { class: "end" }, svg);
      for (const e of ends) {
        const t = el("text", { x: width - M.r + 8, y: e.y + 4 }, g);
        t.textContent = `${e.s.label} ${tick(last.v[e.s.key])}`;
      }
    }
    // Event arrows: a downward arrow above the plot, a dashed guide to the axis,
    // and a label (stacked when events are close together).
    // Labels go on one of two rows only where they fit; an event without room keeps
    // its arrow and is named in the readout and in the list under the chart.
    const ev = el("g", { class: "events" }, svg);
    const rowEnd = [-Infinity, -Infinity];
    for (const [i, evs] of [...at.entries()].sort((a, b) => a[0] - b[0])) {
      const x = X(i);
      const kind = evs.length === 1 && GLYPH[evs[0].kind] ? evs[0].kind : "event";
      el("line", { class: `event-guide ${kind}`, x1: x, x2: x, y1: M.t, y2: H - M.b }, ev);
      const tri = kind === "up" ? `M${x - 5},${M.t - 2} L${x + 5},${M.t - 2} L${x},${M.t - 10} Z`
        : `M${x - 5},${M.t - 10} L${x + 5},${M.t - 10} L${x},${M.t - 2} Z`;
      el("path", { class: `event-arrow ${kind}`, d: tri }, ev);
      const text = evs.map((e) => e.label).join(" · ");
      const w = text.length * 6.4 + 4;
      const right = x + 8 + w < width - 4;
      const x0 = right ? x - 2 : x - 8 - w, x1 = right ? x + 8 + w : x + 2;
      const level = rowEnd.findIndex((end) => x0 > end + 6);
      if (level < 0 || x0 < 0) continue;
      rowEnd[level] = x1;
      const t = el("text", { class: `event-label ${kind}`, x: right ? x + 8 : x - 8, y: M.t - 14 - level * 13,
        "text-anchor": right ? "start" : "end" }, ev);
      t.textContent = text;
    }
    const cross = el("line", { class: "cross", y1: M.t, y2: H - M.b, visibility: "hidden" }, svg);
    const dots = series.map((s) => el("circle", { r: 4, fill: s.color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }, svg));
    geom.cross = cross; geom.dots = dots;
    if (idx != null) show(idx);
  }

  function show(i) {
    idx = Math.max(0, Math.min(rows.length - 1, i));
    const r = rows[idx], { X, Y, cross, dots, width } = geom;
    cross.setAttribute("x1", X(idx)); cross.setAttribute("x2", X(idx)); cross.setAttribute("visibility", "visible");
    series.forEach((s, j) => {
      const v = r.v[s.key];
      dots[j].setAttribute("visibility", v == null ? "hidden" : "visible");
      if (v != null) { dots[j].setAttribute("cx", X(idx)); dots[j].setAttribute("cy", Y(v)); }
    });
    tip.textContent = "";
    const d = document.createElement("div");
    d.className = "d";
    const kind = describe(r);
    d.textContent = kind ? `${fmtDate(r.date)} · ${kind}` : fmtDate(r.date);
    tip.appendChild(d);
    const parts = [];
    for (const m of at.get(idx) || []) {
      const e = document.createElement("div");
      e.className = `e ${m.kind || ""}`;
      e.textContent = `${GLYPH[m.kind] || "▼"} ${m.label}`;
      tip.appendChild(e);
      parts.push(m.label);
    }
    for (const s of series) {
      if (r.v[s.key] == null && s.optional) continue;
      const row = document.createElement("div");
      row.className = "r";
      const name = document.createElement("span");
      const key = document.createElement("i");
      key.className = "key" + (s.dash ? " dash" : "");
      key.style.borderTopColor = s.color;
      name.append(key, s.label);
      const b = document.createElement("b");
      b.textContent = r.v[s.key] == null ? (s.missing || "no price") : format(r.v[s.key]);
      row.append(name, b);
      tip.appendChild(row);
      parts.push(`${s.label} ${b.textContent}`);
    }
    tip.hidden = false;
    const scale = host.clientWidth / width;
    const x = X(idx) * scale;
    tip.style.left = `${Math.min(Math.max(x + 12, 0), host.clientWidth - tip.offsetWidth)}px`;
    if (x + 12 + tip.offsetWidth > host.clientWidth) tip.style.left = `${Math.max(x - tip.offsetWidth - 12, 0)}px`;
    tip.style.top = "8px";
    announce?.(`${fmtDate(r.date)}${kind ? `, ${kind}` : ""}: ${parts.join(", ")}`);
  }

  function hide() {
    if (!geom) return;
    idx = null;
    tip.hidden = true;
    geom.cross.setAttribute("visibility", "hidden");
    geom.dots.forEach((d) => d.setAttribute("visibility", "hidden"));
  }

  function fromPointer(e) {
    const rect = svg.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * geom.width;
    const n = rows.length - 1 || 1;
    const i = Math.round(((x - geom.M.l) / (geom.width - geom.M.l - geom.M.r)) * n);
    show(i);
  }
  svg.addEventListener("pointermove", fromPointer);
  svg.addEventListener("pointerdown", fromPointer);
  svg.addEventListener("pointerleave", () => { if (document.activeElement !== svg) hide(); });
  svg.addEventListener("blur", hide);
  svg.addEventListener("focus", () => show(idx ?? rows.length - 1));
  svg.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 21 : 1;
    const moves = { ArrowLeft: -step, ArrowRight: step, PageUp: -21, PageDown: 21 };
    if (e.key in moves) { show((idx ?? rows.length - 1) + moves[e.key]); e.preventDefault(); }
    else if (e.key === "Home") { show(0); e.preventDefault(); }
    else if (e.key === "End") { show(rows.length - 1); e.preventDefault(); }
    else if (e.key === "Escape") { hide(); }
  });
  const ro = new ResizeObserver(() => draw());
  ro.observe(host);
  draw();
  return { destroy: () => ro.disconnect() };
}
