// Theme Monitor dashboard. Reads the static feed written by `themegraph monitor
// refresh` (docs/MONITOR.md, "Data feed") and renders four views:
//   #/            overview: emerging and established themes first
//   #/themes      all themes: search, filters, sorting
//   #/theme/<id>  one theme: chart, returns, companies, evidence, history
//   #/method      method, coverage, freshness, release details
import { fmtDate, lineChart, sparkline } from "./chart.js";

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MINUS = "−";
const num = (x, d = 1) => `${x < 0 ? MINUS : x > 0 ? "+" : ""}${Math.abs(x).toFixed(d)}`;
const pct = (x, d = 1) => x == null ? "—" : `<span class="${x > 0 ? "pos" : x < 0 ? "neg" : ""}">${num(x * 100, d)}%</span>`;
const pts = (x, d = 1) => x == null ? "—" : `<span class="${x > 0 ? "pos" : x < 0 ? "neg" : ""}">${num(x * 100, d)} pts</span>`;
const plainPct = (x) => x == null ? "n/a" : `${num(x * 100, 1)}%`;
const share = (x, d = 0) => x == null ? "—" : `${(x * 100).toFixed(d)}%`;
const PERIODS = { "3m": ["3M", "3-month", 63], "6m": ["6M", "6-month", 126], "12m": ["12M", "12-month", 252] };
const STATUS = { new: "New", strengthening: "Strengthening", stable: "Stable", cooling: "Cooling", inactive: "Inactive", merged: "Merged" };
const ROLE = { management_remarks: "Management, prepared remarks", management_answer: "Management, Q&A answer",
  analyst_question: "Analyst question", filing: "SEC filing" };
const BASIS = { live: ["Live", "live"], reconstructed: ["Simulated", "sim"], mixed: ["Simulated + live", "sim"] };

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const S = { index: null, status: null, base: "", U: {}, B: ["SPY"], byId: {}, period: store.get("tm.period", "12m"),
  cache: new Map(), nav: 0, chart: null };
if (!PERIODS[S.period]) S.period = "12m";

// -- shared pieces -----------------------------------------------------------------
const chip = (s) => `<span class="chip ${esc(s)}">${esc(STATUS[s] || s)}</span>`;
const basisTag = (b) => b ? `<span class="tag ${BASIS[b][1]}" title="${b === "live" ? "Prices observed after the theme was published" : "Includes a historical simulation of what the rule would have held"}">${BASIS[b][0]}</span>` : "";
const months = (sessions) => Math.max(0, Math.floor((sessions || 0) / 21));
const bench = () => S.B[0];

function getJSON(url, signal) {
  return fetch(url, { cache: "no-store", signal }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} for ${url}`);
    return r.json();
  });
}

function notices() {
  const out = [];
  const st = S.status;
  if (S.index?.fixture) {
    out.push(`<div class="notice warn" role="note"><span class="icon" aria-hidden="true">!</span><div><b>Demonstration data.</b> The companies, documents and prices on this site are synthetic. They show how the monitor works; nothing here is a real theme or a real return.</div></div>`);
  }
  if (st && (Object.values(st.errors || {}).some(Boolean) || Object.keys(st.skipped || {}).length)) {
    const failed = Object.entries(st.errors || {}).filter(([, v]) => v).map(([k]) => k);
    out.push(`<div class="notice bad" role="alert"><span class="icon" aria-hidden="true">×</span><div><b>The latest update failed</b> (${esc(failed.join(", ") || "incomplete")}, ${esc(fmtDate(st.checked_at))}). You are seeing the last complete release, from ${esc(fmtDate(S.index?.generated_at))}. <a href="#/method#freshness">Details</a></div></div>`);
  }
  const stale = Object.entries(st?.stale || {}).filter(([, v]) => v).map(([k]) => (k === "documents" ? "earnings calls and filings" : k));
  if (stale.length) {
    const a = S.index?.data_as_of || {};
    out.push(`<div class="notice warn" role="note"><span class="icon" aria-hidden="true">!</span><div><b>Some data is out of date:</b> ${esc(stale.join(" and "))}. Prices through ${esc(fmtDate(a.prices))}; calls and filings through ${esc(fmtDate(a.documents))}.</div></div>`);
  }
  return out.length ? `<div class="notices">${out.join("")}</div>` : "";
}

function periodControl() {
  return `<div class="controls" role="group" aria-label="Return period"><span class="small muted">Return period</span><div class="seg">${
    Object.entries(PERIODS).map(([k, [lab, long]]) => `<button type="button" data-period="${k}" aria-pressed="${S.period === k}" aria-label="${long} returns">${lab}</button>`).join("")}</div></div>`;
}

function bindPeriod(root, rerender) {
  root.querySelectorAll("[data-period]").forEach((b) => b.addEventListener("click", () => {
    S.period = b.dataset.period;
    store.set("tm.period", S.period);
    rerender();
    $(`[data-period="${S.period}"]`)?.focus();
  }));
}

function periodReturn(row) {
  const m = row.market;
  if (m.status !== "ok") return { na: m.status === "awaiting_entry" ? "Not bought yet" : "No prices" };
  const r = m.returns?.[S.period];
  if (!r) return { na: `${months(m.sessions)} months of prices; not enough for ${PERIODS[S.period][1]}` };
  return r;
}

function card(row) {
  const r = periodReturn(row), [, long] = PERIODS[S.period], b = bench();
  const e = row.emergence, st = row.strength;
  const keys = row.key_constituents || [];
  const more = (row.market.n_constituents || 0) - keys.length;
  const sp = row.market.spark;
  let spark = "";
  if (sp && !r.na) {
    const k = Math.max(1, Math.ceil(PERIODS[S.period][2] / 5));
    const sl = (a) => a.slice(-k - 1);
    const label = `Basket ${plainPct(r.theme)} versus ${b} ${plainPct(r[b])} over ${PERIODS[S.period][1]}s`;
    spark = sparkline({ dates: sl(sp.dates), theme: sl(sp.theme), benchmark: sl(sp.benchmark), liveFrom: sp.live_from, label: esc(label) });
  }
  return `<article class="card">
    <div class="card-top"><h3><a href="#/theme/${esc(row.id)}">${esc(row.title)}</a></h3>${chip(row.status)}</div>
    <p class="summary">${esc(row.summary)}</p>
    ${row.label_source === "keywords" ? `<p class="small muted">Automatic title from key terms</p>` : ""}
    <div class="meta">${row.route === "newly_covered" ? `<span class="tag" title="Visible because coverage expanded; not counted as emerging">Newly covered</span>` : ""}<span>${st.n_issuers ?? "—"} companies</span><span>${esc((row.sectors || []).join(", "))}</span></div>
    <div class="tickers" aria-label="Largest constituents">${keys.map((c) => esc(c.symbol)).join(" · ")}${more > 0 ? ` <span class="muted">+${more}</span>` : ""}</div>
    <div class="split">
      <div class="metric"><div class="k">${long.replace(/^./, (c) => c.toUpperCase())} basket return</div>
        ${r.na ? `<div class="na">${esc(r.na)}</div>` : `<div class="v">${pct(r.theme)}</div><div class="s">vs ${esc(b)} ${pts(r[`excess_vs_${b}`])} ${basisTag(r.basis)}</div>`}</div>
      <div class="metric"><div class="k">Discussion</div>
        <div class="v">${e.growth_3m == null ? "—" : `${e.growth_3m.toFixed(1)}×`}</div>
        <div class="s">${e.growth_3m == null ? "no 3-month comparison yet" : "its share 3 months ago"} · in ${st.persistence_12 ?? "—"} of 12 months</div></div>
    </div>
    ${spark ? `<div>${spark}<div class="small muted"><span class="key" style="border-top-color:var(--series-1)"></span>Basket <span class="key dash" style="border-top-color:var(--series-2);margin-left:10px"></span>${esc(b)}${sp.live_from ? " · shaded: live" : ""}</div></div>` : ""}
  </article>`;
}

function rowsList(rows, empty) {
  if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
  const b = bench(), [, long] = PERIODS[S.period];
  return `<div class="rows" role="list">
    <div class="row row-head" aria-hidden="true"><span>Theme</span><span class="num">Companies</span><span class="num">Discussion</span><span class="num">${esc(PERIODS[S.period][0])} return</span><span class="num">vs ${esc(b)}</span></div>
    ${rows.map((row) => {
      const r = periodReturn(row);
      return `<div class="row" role="listitem">
        <div class="t"><a href="#/theme/${esc(row.id)}">${esc(row.title)}</a> ${chip(row.status)}<div class="small muted">${esc((row.sectors || []).join(", "))}</div></div>
        <div class="num c-companies">${row.strength.n_issuers ?? "—"}</div>
        <div class="num c-disc small"><span class="lab">Discussion</span>${row.emergence.growth_3m == null ? "—" : `${row.emergence.growth_3m.toFixed(1)}× 3m ago`}</div>
        <div class="num c-ret"><span class="lab">${esc(long)}</span>${r.na ? `<span class="na">n/a</span>` : `${pct(r.theme)}<div>${basisTag(r.basis)}</div>`}</div>
        <div class="num c-ex small"><span class="lab">vs ${esc(b)}</span>${r.na ? "—" : pts(r[`excess_vs_${b}`])}</div>
      </div>`; }).join("")}</div>`;
}

// -- overview ------------------------------------------------------------------------
function overview() {
  const L = S.index.lists, by = S.byId, b = bench();
  const emerging = L.emerging.map((id) => by[id]);
  const seen = new Set(L.emerging);
  const established = L.established.filter((id) => !seen.has(id)).map((id) => by[id]);
  const lowList = (ids) => ids.map((id) => by[id]).filter(Boolean);
  const a = S.index.data_as_of || {}, U = S.U;
  const lastChange = [...(U.changes || [])].reverse().find((c) => (c.added || []).length);
  const newly = (L.newly_covered || []).map((id) => by[id]).filter(Boolean);
  const nSectors = Object.keys(U.sectors || {}).length;
  const render = () => {
    $("#main").innerHTML = `
      <section class="intro"><h1>Theme Monitor</h1>
        <p class="lede">Investment themes that companies are discussing in their earnings calls and SEC filings, and how an equal-weight basket of those companies' stocks has performed.</p>
        <p class="facts"><span>Coverage <b>${esc(U.name || "—")}</b> (version ${esc(U.version)}): ${U.n_companies ?? "—"} companies, ${nSectors} sectors</span>
          ${lastChange ? `<span>Expanded ${esc(fmtDate(lastChange.date))}: ${lastChange.added.length} companies added</span>` : ""}
          <span>Compared with <b>${esc(S.B.join(" and "))}</b>${S.index.benchmark_rule && Object.keys(S.index.benchmark_rule.sector_benchmarks || {}).length ? " and a sector reference where one fits" : ""}</span>
          <span>Prices through <b>${esc(fmtDate(a.prices))}</b></span><span>Calls and filings through <b>${esc(fmtDate(a.documents))}</b></span>
          <a href="#/method#coverage">What is covered</a></p></section>
      ${notices()}
      ${periodControl()}
      <section class="section" aria-labelledby="h-em"><div class="section-head"><div><h2 id="h-em">Emerging</h2>
        <p class="muted small">Newly prominent in what companies discuss: flagged by the emergence rule in the last three months, or entered that way and still growing. Ordered by growth in discussion.</p></div></div>
        ${emerging.length ? `<div class="cards">${emerging.map(card).join("")}</div>` : `<p class="empty">Nothing is emerging right now. Themes appear here when discussion of a topic grows quickly across several comparable companies.</p>`}</section>
      ${newly.length ? `<section class="section" aria-labelledby="h-new"><div class="section-head"><div><h2 id="h-new">Newly covered</h2>
        <p class="muted small">Visible because coverage expanded${lastChange ? ` on ${esc(fmtDate(lastChange.date))}` : ""}: long-running discussion among newly added companies, found in their earlier documents. Not counted as emerging, and tracked only from when it was published.</p></div></div>
        <div class="cards">${newly.map(card).join("")}</div></section>` : ""}
      <section class="section" aria-labelledby="h-es"><div class="section-head"><div><h2 id="h-es">Strong established themes</h2>
        <p class="muted small">Discussed in at least 6 of the last 12 months by four or more companies. “Strong” is within the monitored companies only, and describes discussion, not stock performance.</p></div></div>
        ${established.length ? `<div class="cards">${established.map(card).join("")}</div>` : `<p class="empty">No established themes yet.</p>`}</section>
      <section class="section" aria-label="Relative performance">
        <div class="section"><div><h2>Relative performance improving</h2><p class="muted small">The basket's 3-month return minus ${esc(b)}'s rose by at least 5 points since the previous month-end. This is about stock prices only.</p></div>${rowsList(lowList(L.market_gaining), "No theme's relative performance improved that much this month.")}</div>
        <div class="section"><div><h2>Relative performance weakening</h2><p class="muted small">The same measure fell by at least 5 points. A strong theme in discussion can still have a falling basket.</p></div>${rowsList(lowList(L.market_losing), "No theme's relative performance weakened that much this month.")}</div>
      </section>
      <section class="section" aria-labelledby="h-cool"><div><h2 id="h-cool">Cooling and inactive</h2><p class="muted small">Kept with their full history and their basket returns, including losses. Inactive: not discussed for three months.</p></div>
        ${rowsList(lowList([...(L.cooling || []), ...L.inactive]), "No cooling or inactive themes.")}</section>
      <p><a href="#/themes">See all ${S.index.themes.length} themes →</a></p>`;
    bindPeriod($("#main"), render);
  };
  render();
}

// -- all themes ----------------------------------------------------------------------
function allThemes(params) {
  const sectors = [...new Set(S.index.themes.flatMap((r) => r.sectors || []))].sort();
  const q = { q: params.get("q") || "", status: params.get("status") || "", sector: params.get("sector") || "",
    route: params.get("route") || "", sort: params.get("sort") || "return" };
  const b = bench();
  const opts = (list, cur) => list.map(([v, l]) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(l)}</option>`).join("");
  $("#main").innerHTML = `
    <section class="intro"><h1>All themes</h1><p class="muted">Every theme the monitor has published, including cooling, inactive and merged ones. Nothing is deleted.</p></section>
    ${notices()}
    <form class="controls" role="search" onsubmit="return false">
      <label class="field">Search<input id="f-q" type="search" placeholder="Theme, company or term" value="${esc(q.q)}"></label>
      <label class="field">Status<select id="f-status">${opts([["", "Any status"], ["active", "Active (not inactive)"], ...Object.entries(STATUS)], q.status)}</select></label>
      <label class="field">Sector<select id="f-sector">${opts([["", "Any sector"], ...sectors.map((s) => [s, s])], q.sector)}</select></label>
      <label class="field">How it entered<select id="f-route">${opts([["", "Any"], ["emergence", "Emergence rule"], ["established", "Established (persistent)"], ["newly_covered", "Newly covered"]], q.route)}</select></label>
      <label class="field">Sort by<select id="f-sort">${opts([["return", "Basket return"], ["excess", `Return vs ${b}`], ["growth", "Discussion growth"], ["companies", "Companies"], ["persistence", "Months discussed"], ["recent", "Recently published"], ["name", "Name"]], q.sort)}</select></label>
    </form>
    ${periodControl()}
    <p class="count" id="count" aria-live="polite"></p>
    <div id="list"></div>`;
  const read = () => ({ q: $("#f-q").value.trim(), status: $("#f-status").value, sector: $("#f-sector").value,
    route: $("#f-route").value, sort: $("#f-sort").value });
  const val = {
    return: (r) => periodReturn(r).theme, excess: (r) => periodReturn(r)[`excess_vs_${b}`],
    growth: (r) => r.emergence.growth_3m, companies: (r) => r.strength.n_issuers,
    persistence: (r) => r.strength.persistence_12, recent: (r) => r.published_snapshot,
  };
  const draw = () => {
    const f = read();
    const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
    let rows = S.index.themes.filter((r) => {
      if (f.status === "active" ? ["inactive", "merged"].includes(r.status) : f.status && r.status !== f.status) return false;
      if (f.sector && !(r.sectors || []).includes(f.sector)) return false;
      if (f.route && r.route !== f.route) return false;
      const hay = [r.title, r.summary, r.name, r.id, ...(r.sectors || []), ...(r.key_constituents || []).map((c) => c.symbol)].join(" ").toLowerCase();
      return words.every((w) => hay.includes(w));
    });
    if (f.sort === "name") rows.sort((x, y) => x.title.localeCompare(y.title));
    else rows.sort((x, y) => {
      const a = val[f.sort](x), c = val[f.sort](y);
      if (a == null && c == null) return 0;
      if (a == null) return 1;
      if (c == null) return -1;
      return a < c ? 1 : a > c ? -1 : 0;
    });
    $("#count").textContent = `Showing ${rows.length} of ${S.index.themes.length} themes`;
    $("#list").innerHTML = rowsList(rows, "No themes match these filters.");
    const p = new URLSearchParams(Object.entries(f).filter(([k, v]) => v && !(k === "sort" && v === "return")));
    history.replaceState(null, "", `#/themes${p.toString() ? "?" + p : ""}`);
  };
  ["f-q", "f-status", "f-sector", "f-route", "f-sort"].forEach((id) => $("#" + id).addEventListener(id === "f-q" ? "input" : "change", draw));
  bindPeriod($("#main"), draw);
  draw();
}

// -- one theme -----------------------------------------------------------------------
const RANGES = [["3m", "3M", 63], ["6m", "6M", 126], ["1y", "1Y", 252], ["all", "All", null], ["live", "Live", null]];

function togetherHTML(tt) {
  if (!tt) return "";
  const names = S.index.benchmark_labels || {};
  const idx = `${tt.index}${names[tt.index] ? ` (${names[tt.index]})` : ""}`;
  const pctText = (x) => (x.percentile >= 0.995 ? "all" : x.percentile <= 0.005 ? "none" : `${Math.round(x.percentile * 100)}%`);
  const line = (x, when) => {
    if (!x) return `<li>${when}: not enough price history.</li>`;
    if (x.percentile == null) return `<li>${when}: ${esc(x.note || "not enough price history")}.</li>`;
    return `<li>${when} (${esc(fmtDate(x.start))} – ${esc(fmtDate(x.end))}): the ${x.companies} companies moved together more than <b>${pctText(x)}</b> of ${x.random_baskets} random baskets with the same mix of segments.</li>`;
  };
  const n = tt.now, b = tt.before;
  const verdict = !n || n.percentile == null ? "" : n.percentile >= 0.9 ? "Trading together"
    : n.percentile <= 0.1 ? "Less connected than usual" : "Not unusual";
  return `<div class="together"><h3>Trading together, beyond the market</h3>
    <p class="small muted">Whether these stocks move as a group once the part explained by ${esc(idx)} is removed. Prices only: separate from how much the theme is discussed and from the basket's return.</p>
    ${verdict ? `<p><b>${verdict}</b></p>` : ""}
    ${n && n.same_segment_share != null && n.same_segment_share < 0.5 ? `<p class="small muted">Few other monitored companies share these segments, so most random baskets come from other segments (${Math.round(n.same_segment_share * 100)}% matched). Part of this may be ordinary sector co-movement rather than the theme.</p>` : ""}
    <ul class="plain">${line(n, "Last 6 months")}${line(b, "6 months before")}</ul>
    ${n && n.percentile != null ? `<details><summary>How this is measured</summary><div class="body prose small">
      <p>Daily returns of every monitored company with its own close on at least 90% of the sessions, linked across days without a close. Their covariance is shrunk toward a single-index model on ${esc(tt.index)} (Ledoit and Wolf, 2003; shrinkage ${n.shrinkage.toFixed(2)} for the last 6 months), and each pair's correlation beyond what ${esc(tt.index)} explains is averaged over the basket: ${n.excess_corr.toFixed(3)} now${b && b.excess_corr != null ? `, ${b.excess_corr.toFixed(3)} before` : ""}.</p>
      <p>With this model every pair's level is scaled down by the same factor (one minus the shrinkage), so the comparison with random baskets of the same size and segment mix is the measure, not the level. ${n.left_out?.length ? `Left out for too few closes: ${esc(n.left_out.join(", "))}.` : ""}</p></div></details>` : ""}
  </div>`;
}

function benchText(t, B) {
  const names = S.index.benchmark_labels || {};
  const sb = t.sector_benchmark || {};
  const parts = B.map((b) => `${b}${names[b] ? ` (${names[b]})` : ""}`);
  let why = "";
  if (sb.symbol) why = ` ${sb.symbol} is included because ${Math.round(sb.share * 100)}% of the first basket's companies are in ${sb.sector}; this was fixed when that basket was decided, from its composition only.`;
  else if (sb.decided_at) why = " No sector reference: no single sector held at least half of the first basket.";
  return `Compared with ${parts.join(", ")}.${why} Benchmarks are capitalisation-weighted; theme baskets are equal-weighted.`;
}

function labelNote(t) {
  const L = t.label || {};
  if (L.source === "claude") return `Title and summary written by Claude (${esc(L.model || "")}) from ${L.evidence?.length || 0} cited passages, marked below. They describe discussion only.`;
  if (L.source === "curated") return "Title and summary written by the site's editor.";
  if (L.status === "ambiguous") return "The evidence did not point to one clear subject, so this is an automatic title from the theme's most distinctive terms.";
  return "Automatic title from the theme's most distinctive terms. No written summary yet.";
}

function quoteHTML(q, cited) {
  return `<div class="quote ${esc(q.role)}"><div class="src"><span class="tag ${q.role === "analyst_question" ? "q" : ""}">${esc(ROLE[q.role] || q.role)}</span>
    <b>${esc(q.symbol)}</b><span>${esc(fmtDate(q.date))}</span><span>${esc(q.doc_type || "")}</span>
    ${q.url ? `<a href="${esc(q.url)}" rel="noopener noreferrer" target="_blank">Source</a>` : ""}${cited.has(q.passage_id) ? `<span class="tag live">Cited in summary</span>` : ""}</div>
    <blockquote>${esc(q.text)}…</blockquote></div>`;
}

async function themePage(id, token) {
  $("#main").setAttribute("aria-busy", "true");
  let t = S.cache.get(id);
  if (!t) {
    try { t = await getJSON(`${S.base}themes/${encodeURIComponent(id)}.json`); }
    catch (e) {
      if (token !== S.nav) return;
      $("#main").removeAttribute("aria-busy");
      $("#main").innerHTML = `${notices()}<p class="notice bad">Theme ${esc(id)} is not in the current release. <a href="#/themes">All themes</a></p>`;
      return;
    }
    S.cache.set(id, t);
  }
  if (token !== S.nav) return; // the reader has moved on: never draw a stale theme
  $("#main").removeAttribute("aria-busy");
  const p = t.performance, R = p.returns || {}, B = t.benchmarks || S.B;
  const m = [...t.measures].reverse().find((r) => r.present) || {};
  const cited = new Set(t.label?.evidence || []);
  const rec = R.reconstructed, live = R.live;
  const seriesNote = p.status !== "ok" ? "" :
    `${rec ? `Simulated from ${fmtDate(rec.start)} to ${fmtDate(rec.end)}: what the published rule would have held, reconstructed from history.` : ""}
     ${live ? `Live since ${fmtDate(live.start)}: prices observed after the theme was published on ${fmtDate(t.published_at)}.` : `No live prices yet: the theme was published on ${fmtDate(t.published_at)}.`}`;
  document.title = `${t.title} · Theme Monitor`;
  const versions = t.basket_versions.map((v, i) => {
    const prev = new Set(i ? t.basket_versions[i - 1].constituents.map((c) => c.symbol) : []);
    const cur = v.constituents.map((c) => c.symbol);
    const add = cur.filter((s) => !prev.has(s)), rem = [...prev].filter((s) => !cur.includes(s));
    return `<tr><td>v${v.version}</td><td>${esc(fmtDate(v.snapshot))}</td><td>${esc(v.basis === "live" ? "Live" : "Simulated")}</td><td>${esc(v.reason)}</td><td class="mono">${i ? `added ${esc(add.join(", ") || "none")}; removed ${esc(rem.join(", ") || "none")}` : esc(cur.join(", "))}</td></tr>`;
  }).join("");
  const retRow = (k, label) => {
    const r = R[k];
    if (!r) return `<tr><td class="first">${label}</td><td colspan="${1 + 2 * B.length}" class="muted">Not enough history</td></tr>`;
    return `<tr><td class="first">${label} ${basisTag(r.basis)}<div class="small muted">${esc(fmtDate(r.start))} – ${esc(fmtDate(r.end))}</div></td><td class="num" data-k="Basket">${pct(r.theme)}</td>${B.map((b) => `<td class="num" data-k="${esc(b)}">${pct(r[b])}</td>`).join("")}${B.map((b) => `<td class="num" data-k="vs ${esc(b)}">${pts(r["excess_vs_" + b])}</td>`).join("")}</tr>`;
  };
  const trend = t.market_trend || {};
  const roles = m.evidence_roles || {};
  const mgmt = (roles.management_remarks || 0) + (roles.management_answer || 0);
  const nameHist = [...(t.label_history || []).map((h) => `<li>${esc(fmtDate(h.at))}: titled “${esc(h.title)}” (${esc(h.source === "keywords" ? "automatic" : h.source)})</li>`),
    ...t.name_history.map((n) => `<li>${esc(fmtDate(n.t))}: keyword name “${esc(n.name)}”</li>`),
    ...t.status_history.map((s) => `<li>${esc(fmtDate(s.t))}: ${esc(STATUS[s.from] || s.from)} → ${esc(STATUS[s.to] || s.to)}</li>`),
    ...t.lineage.map((l) => `<li>${esc(fmtDate(l.t))}: ${esc(l.event.replace(/_/g, " "))} <a href="#/theme/${esc(l.other)}">${esc(S.byId[l.other]?.title || l.other)}</a></li>`)].join("");

  $("#main").innerHTML = `
    <nav class="crumb" aria-label="Breadcrumb"><a href="#/">Overview</a> / <a href="#/themes">All themes</a></nav>
    ${notices()}
    <header class="hero"><div class="card-top"><h1>${esc(t.title)}</h1>${chip(t.status)}</div>
      <p class="summary">${esc(t.summary)}</p>
      <p class="small muted">${labelNote(t)}</p>
      ${(t.overview || []).length ? `<details class="about" open><summary>About this theme</summary><div class="body prose">${t.overview.map((p) => `<div><h3>${esc(p.heading)}</h3><p>${esc(p.text)}</p></div>`).join("")}</div></details>` : ""}
      ${t.newly_covered ? `<p class="notice"><span><b>Newly covered.</b> This theme became visible when coverage expanded: ${t.newly_covered.new_companies.length} of its companies were added recently. Their earlier documents show the topic in ${t.newly_covered.backfill_present} of the ${t.newly_covered.of_last} months before ${esc(fmtDate(t.newly_covered.at))}; that is historical analysis, not an earlier discovery. Tracking began when it was published on ${esc(fmtDate(t.published_at))}.</span></p>` : ""}
      <p class="facts"><span>${m.n_issuers ?? "—"} companies</span><span>${esc((m.sectors || []).join(", "))}</span>
        <span>Entered ${esc(fmtDate(t.published_snapshot))} via the ${{ emergence: "emergence rule", established: "established-theme rule", newly_covered: "newly-covered rule" }[t.route] || esc(t.route)}</span></p>
      ${t.merged_into ? `<p class="notice warn">Merged into <a href="#/theme/${esc(t.merged_into)}">${esc(S.byId[t.merged_into]?.title || t.merged_into)}</a>. Its history and basket are kept here.</p>` : ""}
    </header>
    <section class="panel" aria-labelledby="h-perf">
      <div class="section-head"><h2 id="h-perf">Equal-weight basket vs benchmarks</h2>
        ${p.status === "ok" ? `<div class="seg" role="group" aria-label="Chart range">${RANGES.map(([k, l]) => `<button type="button" data-range="${k}">${l}</button>`).join("")}</div>` : ""}</div>
      ${p.status === "ok" ? `<div class="kpis" id="kpis"></div>
        <div class="legend"><span><i class="key" style="border-top-color:var(--series-1);border-top-width:3px"></i>Theme basket</span>${B.map((b, i) => `<span><i class="key dash" style="border-top-color:var(--series-${i + 2})"></i>${esc(b)}</span>`).join("")}<span><i class="band-key"></i>Live (observed)</span><span><span class="event-key" aria-hidden="true">▼</span>Event</span>${p.hindsight ? `<span><i class="hind-key"></i>Before entry (hindsight, when shown)</span>` : ""}<span class="muted">Unshaded: simulated</span></div>
        ${p.hindsight ? `<label class="check"><input type="checkbox" id="hind-toggle"> Show the ${Math.round(p.hindsight.sessions / 21)} months before the basket started (hindsight)</label>` : ""}
        <div id="chart"></div><p class="sr" aria-live="polite" id="readout"></p>
        <p class="small muted">${benchText(t, B)}</p>
        <p class="small muted" id="hind-note"></p>
        <p class="small muted" id="events-note"></p>
        <p class="small muted">${seriesNote} Index = 100 at the start of the selected range. ${esc(p.price_basis)}.</p>
        <details><summary>Chart data as a table</summary><div class="body table-wrap" id="chart-table"></div></details>`
        : `<p class="empty">${esc(p.note || "No prices yet.")}</p>`}
    </section>
    <section class="panel" aria-labelledby="h-disc"><h2 id="h-disc">Discussion</h2>
        <p class="small muted">How much and how widely companies talk about this theme. Independent of stock prices.</p>
        <div class="legend"><span><i class="key" style="border-top-color:var(--series-1);border-top-width:3px"></i>Share of all monitored text (%)</span><span><span class="event-key up" aria-hidden="true">▲</span>Strengthening or recovered</span><span><span class="event-key down" aria-hidden="true">▼</span>Cooling, levelling off or going quiet</span></div>
        <div id="disc-chart"></div><p class="sr" aria-live="polite" id="disc-readout"></p>
        <p class="small muted">Points mark changes in status. Status compares the last three months with the three before within a fixed group of companies, so adding or removing companies can move this line without a point.</p>
        <dl class="dl cols">
          <dt>Growth</dt><dd>${m.growth_3m == null ? "No 3-month comparison yet" : `${m.growth_3m.toFixed(2)}× its share of comparable companies' text 3 months ago`}</dd>
          <dt>Share of text</dt><dd>${share(m.share, 1)} of passages in the last six months</dd>
          <dt>Companies</dt><dd>${m.n_issuers ?? "—"}${m.new_issuers_3m?.length ? `, ${m.new_issuers_3m.length} new in 3 months (${esc(m.new_issuers_3m.join(", "))})` : ""}</dd>
          <dt>Persistence</dt><dd>Discussed in ${m.persistence_12 ?? "—"} of the last 12 months</dd>
          <dt>Sources</dt><dd>${share(mgmt)} management · ${share(roles.filing)} filings · ${share(roles.analyst_question)} analyst questions</dd>
          ${m.recently_added?.length ? `<dt>Recently added</dt><dd>${esc(m.recently_added.join(", "))}: shown, but not counted in growth until they have enough history</dd>` : ""}
        </dl></section>
      <section class="panel" aria-labelledby="h-mkt"><h2 id="h-mkt">Stock basket</h2>
        <p class="small muted">How the companies' shares have done. A well-discussed theme can still have a falling basket.</p>
        ${p.status === "ok" ? `<div class="table-wrap"><table class="stack"><thead><tr><th>Period</th><th class="num">Basket</th>${B.map((b) => `<th class="num">${esc(b)}</th>`).join("")}${B.map((b) => `<th class="num">vs ${esc(b)}</th>`).join("")}</tr></thead>
          <tbody>${retRow("3m", "3 months")}${retRow("6m", "6 months")}${retRow("12m", "12 months")}${rec ? retRow("reconstructed", "Simulated period") : ""}${live ? retRow("live", "Live period") : ""}${retRow("full_period", "Whole history")}</tbody></table></div>
          <p class="small muted">pts: percentage points (basket return minus benchmark return).
          ${trend.trend ? `Relative performance is <b>${esc(trend.trend === "gaining" ? "improving" : trend.trend === "losing" ? "weakening" : "steady")}</b>: the 3-month return vs ${esc(B[0])} is ${num(trend.excess_3m_pp, 1)} pts, ${num(trend.change_pp_1m, 1)} pts from the previous month-end.` : ""}</p>
          ${togetherHTML(t.trading_together)}`
        : `<p class="empty">${esc(p.note || "No prices yet.")}</p>`}
      </section>
    ${p.status === "ok" ? `<section class="panel" aria-labelledby="h-cons"><h2 id="h-cons">Companies in the basket</h2>
      <p class="small muted">Version ${p.current_version}, bought at the close on ${esc(fmtDate(p.current_entry))}. These are exactly the holdings in the index above. ${p.coverage_latest != null && p.coverage_latest < 1 ? `<b>${share(1 - p.coverage_latest)} of the basket's value is priced at an older close</b> (see Price column).` : "Every holding has a current price."}</p>
      ${t.pending_version ? `<p class="notice"><span>Version ${t.pending_version.version} has been decided (${esc(t.pending_version.constituents.join(", "))}) and will be bought at the first close after ${esc(fmtDate(t.pending_version.entry_after))}.</span></p>` : ""}
      <div class="table-wrap"><table class="stack"><thead><tr><th>Company</th><th>Sector</th><th>Role</th><th class="num">Start weight</th><th class="num">Weight now</th><th class="num">Return</th><th class="num">Contribution</th><th>Price</th></tr></thead>
      <tbody>${p.constituents.map((c) => `<tr><td class="first mono">${esc(c.symbol)}</td><td data-k="Sector">${esc(c.sector || "—")}</td><td data-k="Role">${esc(c.role)}</td>
        <td class="num" data-k="Start">${share(c.start_weight, 1)}</td><td class="num" data-k="Now">${share(c.weight_now, 1)}</td>
        <td class="num" data-k="Return">${pct(c.return_since_entry)}</td><td class="num" data-k="Contribution">${pts(c.contribution, 2)}</td>
        <td data-k="Price">${esc(priceText(c))}</td></tr>`).join("")}</tbody></table></div></section>` : ""}
    <section class="panel" aria-labelledby="h-ev"><h2 id="h-ev">Evidence</h2>
      <p class="small muted">Passages closest to the centre of the theme in the latest month (${esc(fmtDate(t.evidence.latest_t))}), one per company first. Analyst questions show what investors asked; they are not company statements.</p>
      <div class="quotes">${t.evidence.latest.map((q) => quoteHTML(q, cited)).join("")}</div>
      ${t.evidence.first_t && t.evidence.first_t !== t.evidence.latest_t ? `<details><summary>Evidence when first seen (${esc(fmtDate(t.evidence.first_t))})</summary><div class="body quotes">${t.evidence.first.map((q) => quoteHTML(q, cited)).join("")}</div></details>` : ""}
    </section>
    <details><summary>How this basket is calculated</summary><div class="body prose">
      <p>When a theme is published, each company with enough of its text in the theme gets an equal share of starting capital and is bought at the first closing price after that moment. The shares are then held without rebalancing, so weights drift with prices.</p>
      <p>If the companies in the theme change and the change holds for two month-ends, a new basket version starts at the index level reached so far (chain-linking). Companies only count from the day they were added; nothing is backfilled.</p>
      <p>Prices are in US dollars and include dividends. A company without a fresh price keeps its last price and is marked; an acquired company becomes what its holders received under the recorded terms.</p>
      <p><b>Simulated vs live.</b> The monitor started on ${esc(fmtDate(S.index.go_live))}. Before a theme's publication, the basket is a reconstruction of what the rule would have held. Only days after publication (and after each basket version was decided) count as live.</p>
      <p><a href="#/method">Full method</a></p></div></details>
    <details><summary>Membership history</summary><div class="body table-wrap"><table><thead><tr><th>Version</th><th>Decided at</th><th>Basis</th><th>Why</th><th>Companies</th></tr></thead><tbody>${versions}</tbody></table></div></details>
    <details><summary>Names, status and lineage</summary><div class="body prose"><p class="small">Theme ID <span class="mono">${esc(t.id)}</span> never changes.</p><ul>${nameHist}</ul></div></details>
    <details><summary>Discussion history by month</summary><div class="body table-wrap"><table><thead><tr><th>Month</th><th class="num">Share</th><th class="num">Growth</th><th class="num">Companies</th><th class="num">Coherence</th><th>Emergence flag</th></tr></thead>
      <tbody>${[...t.measures].reverse().slice(0, 24).map((r) => `<tr><td>${esc(fmtDate(r.t, true))}</td><td class="num">${r.present ? share(r.share, 2) : "absent"}</td><td class="num">${r.growth_3m == null ? "—" : r.growth_3m.toFixed(2) + "×"}</td><td class="num">${r.n_issuers ?? "—"}</td><td class="num">${r.coherence?.toFixed(2) ?? "—"}</td><td>${r.flagged_comparable ? "flagged" : ""}</td></tr>`).join("")}</tbody></table></div></details>
    <details><summary>Keywords and research diagnostics</summary><div class="body"><dl class="dl">
      <dt>Keywords</dt><dd>${esc((t.keywords || []).join(", "))}</dd>
      <dt>Keyword name</dt><dd>${esc(t.name)}</dd>
      <dt>Theme ID</dt><dd class="mono">${esc(t.id)}</dd>
      <dt>First seen</dt><dd>${esc(fmtDate(t.first_seen))}</dd>
      <dt>Published</dt><dd>${esc(fmtDate(t.published_snapshot))} (month-end), recorded ${esc(t.published_at)}</dd>
      <dt>Coherence</dt><dd>${m.coherence?.toFixed(3) ?? "—"} (mean similarity of passages to the theme centre)</dd>
      <dt>Segments</dt><dd>${m.n_segments ?? "—"}</dd><dt>Documents</dt><dd>${m.n_documents ?? "—"} in the latest window</dd>
      <dt>Comparable companies</dt><dd>${esc((m.comparable_issuers || []).join(", ") || "—")}</dd>
      ${t.label?.note ? `<dt>Label check</dt><dd>${esc(t.label.note)}</dd>` : ""}
    </dl></div></details>`;
  if (p.status === "ok") mountChart(t);
  mountDiscussion(t);
  window.scrollTo(0, 0);
  $("#main").focus({ preventScroll: true });
}

function priceText(c) {
  const h = { converted: "acquired, converted", converted_cash: "acquired for cash", delisted_cash: "delisted, held as cash" }[c.holding];
  const base = { fresh: "Current", lagging: `${c.sessions_since_close} sessions old`, stale: c.sessions_since_close > 10000 ? "No price" : `Stale (${c.sessions_since_close} sessions)`, not_bought: "Never priced: held as cash" }[c.price_status] || c.price_status;
  return h ? `${base} · ${h}` : base;
}

function mountDiscussion(t) {
  const rows = t.measures.slice(-36).map((r) => ({ date: r.t.slice(0, 10), present: r.present,
    v: { share: 100 * (r.share || 0) } }));
  if (rows.length < 2) return;
  const pos = Object.fromEntries(rows.map((r, i) => [r.date, i]));
  const markers = (t.status_points || []).filter((sp) => sp.t.slice(0, 10) in pos).map((sp) => ({
    index: pos[sp.t.slice(0, 10)], kind: sp.kind,
    label: `${sp.label}${sp.ratio != null ? ` (${sp.ratio.toFixed(2)}×)` : ""}` }));
  const hi = Math.max(...rows.map((r) => r.v.share));
  const dp = hi < 2 ? 2 : 1;
  S.disc?.destroy();
  S.disc = lineChart($("#disc-chart"), { rows, markers, base: null,
    series: [{ key: "share", label: "Share", color: "var(--series-1)", width: 2.5 }],
    title: "Share of all monitored text discussing this theme, by month-end",
    format: (v) => `${v.toFixed(dp)}%`, tick: (v) => `${v.toFixed(dp)}%`,
    describe: (r) => (r.present ? "" : "not found that month"),
    announce: (x) => { $("#disc-readout").textContent = x; } });
}

function mountChart(t) {
  const p = t.performance, B = t.benchmarks || S.B, all = p.series, H = p.hindsight;
  const hasLive = all.some((r) => r.basis === "live");
  let range = all.length > 252 ? "1y" : "all", before = false;
  // Events on the chart. A month-end event belongs to the session that acted on
  // it (the next one); a basket version to its own entry session.
  const firstFlag = t.measures.find((m) => m.flagged_comparable ?? m.flagged);
  const events = [];
  if (firstFlag) events.push({ date: firstFlag.t, label: "First flagged as emerging" });
  (t.status_points || []).forEach((sp) => events.push({ date: sp.t, label: `Discussion ${sp.label.toLowerCase()}`, kind: sp.kind }));
  if (!firstFlag || t.published_snapshot.slice(0, 10) !== firstFlag.t.slice(0, 10))
    events.push({ date: t.published_snapshot, label: "Entered the monitor" });
  t.basket_versions.slice(1).forEach((v) => events.push({ date: all.find((r) => r.date >= v.entry_after.slice(0, 10))?.date || v.entry_after, label: `Basket v${v.version}`, entry: true }));
  const sessionIn = (list, e) => list.findIndex((r) => (e.entry ? r.date >= e.date.slice(0, 10) : r.date > e.date.slice(0, 10)));
  // Events before the first basket's month-end happened before there was a basket:
  // they can only be drawn on the hindsight stretch.
  const pre = H ? H.series.slice(0, -1) : [];
  events.forEach((e) => {
    const early = e.date.slice(0, 10) < t.basket_versions[0].snapshot.slice(0, 10);
    e.at = early ? -1 : sessionIn(all, e);
    e.pre = early && pre.length && e.date.slice(0, 10) >= pre[0].date ? sessionIn(pre, e) : -1;
  });
  const keys = [{ key: "theme", label: "Basket", color: "var(--series-1)", width: 2.75, optional: true },
    { key: "hind", label: "Same companies before (hindsight)", color: "var(--series-1)", dash: "2 4", width: 2, optional: true },
    ...B.map((b, i) => ({ key: b, label: b, color: `var(--series-${i + 2})`, dash: "6 4", width: 1.75 }))];
  const draw = () => {
    if (before) range = "all";
    const spec = RANGES.find((r) => r[0] === range);
    let start = 0;
    if (range === "live") start = Math.max(0, all.findIndex((r) => r.basis === "live") - 1);
    else if (spec[2]) start = Math.max(0, all.length - 1 - spec[2]);
    const slice = all.slice(start);
    const base = Object.fromEntries(keys.map((k) => [k.key, slice[0][k.key]]));
    const basket = slice.map((r) => ({ date: r.date, basis: r.basis, v: Object.fromEntries(keys.map((k) => [k.key, r[k.key] == null || !base[k.key] ? null : (100 * r[k.key]) / base[k.key]])) }));
    // Hindsight rows are already scaled to 100 at the entry close, where the basket
    // starts at 100 in the All range; the dotted line joins the basket there.
    const head = before && pre.length ? pre.map((r) => ({ date: r.date, basis: "hindsight", v: { theme: null, hind: r.theme, ...Object.fromEntries(B.map((b) => [b, r[b] ?? null])) } })) : [];
    if (head.length) basket[0].v.hind = 100;
    const rows = [...head, ...basket];
    const markers = [
      ...events.filter((e) => e.at >= start).map((e) => ({ index: head.length + e.at - start, label: e.label, kind: e.kind })),
      ...(head.length ? events.filter((e) => e.pre >= 0).map((e) => ({ index: e.pre, label: e.label, kind: e.kind })) : [])];
    S.chart?.destroy();
    S.chart = lineChart($("#chart"), { rows, series: keys.filter((k) => k.key !== "hind" || head.length), format: (v) => v.toFixed(1),
      title: `Basket and benchmarks, ${spec[1]} range${head.length ? " with the six months before (hindsight)" : ""}, index 100 at ${fmtDate(basket[0].date)}`,
      announce: (s) => { $("#readout").textContent = s; }, markers });
    const early = events.filter((e) => e.at >= 0 && e.at < start);
    $("#events-note").textContent = events.length ? [
      ...events.map((e) => `${e.label}: ${fmtDate(e.date)}${e.at === 0 ? " (the basket starts here)" : e.at === -1 ? (head.length && e.pre >= 0 ? " (before the basket existed; shown on the hindsight stretch)" : " (before the basket existed, so not on the basket line)") : ""}`),
      early.length ? `${early.length === 1 ? "One event is" : "Some events are"} before this range; choose All to see ${early.length === 1 ? "it" : "them"}` : ""].filter(Boolean).join(". ") + "." : "";
    $("#hind-note").textContent = head.length ? `Hindsight: the ${H.companies.length} companies in the first basket, held equally from ${fmtDate(H.start)}, ${plainPct(H.theme_return)} by the day the basket started (${B.map((b) => `${b} ${plainPct(H[`${b}_return`])}`).join(", ")}). They were chosen with information from that day, so nobody could have held them; this stretch is not part of any return on this page.${H.left_out.length ? ` Left out for lack of prices: ${H.left_out.join(", ")}.` : ""}` : "";
    // Headline numbers: the basket's own days only.
    const last = basket[basket.length - 1], kinds = new Set(slice.slice(1).map((r) => r.basis));
    const basis = kinds.size === 1 ? [...kinds][0] : "mixed";
    const ret = (k) => last.v[k] == null ? null : last.v[k] / 100 - 1;
    $("#kpis").innerHTML = `<div class="kpi"><div class="small muted">Basket, ${esc(fmtDate(basket[0].date))} – ${esc(fmtDate(last.date))}</div><div class="v">${pct(ret("theme"))}</div>${basisTag(basis)}</div>
      ${B.map((b) => `<div class="kpi"><div class="small muted">vs ${esc(b)} (${esc(plainPct(ret(b)))})</div><div class="v">${pts(ret("theme") != null && ret(b) != null ? ret("theme") - ret(b) : null)}</div></div>`).join("")}
      <div class="kpi"><div class="small muted">Priced today</div><div class="v">${share(p.coverage_latest)}</div><div class="small muted">of basket value</div></div>`;
    $("#main").querySelectorAll("[data-range]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.range === range));
      const sp = RANGES.find((r) => r[0] === b.dataset.range);
      b.disabled = (b.dataset.range === "live" && !hasLive) || (sp[2] != null && all.length <= sp[2]);
      if (b.disabled) b.title = b.dataset.range === "live" ? "No live prices yet" : "Not enough history";
    });
    const shown = keys.filter((k) => k.key !== "hind" || head.length);
    const monthly = rows.filter((r, i) => i === 0 || i === rows.length - 1 || r.date.slice(0, 7) !== rows[i + 1]?.date.slice(0, 7) || r.basis !== rows[i + 1]?.basis);
    $("#chart-table").innerHTML = `<table><caption>Month-end values, index = 100 at ${esc(fmtDate(basket[0].date))}</caption><thead><tr><th>Date</th><th>Basis</th>${shown.map((k) => `<th class="num">${esc(k.label)}</th>`).join("")}</tr></thead><tbody>${
      monthly.map((r) => `<tr><td>${esc(fmtDate(r.date))}</td><td>${{ live: "Live", hindsight: "Hindsight" }[r.basis] || "Simulated"}</td>${shown.map((k) => `<td class="num">${r.v[k.key] == null ? "—" : r.v[k.key].toFixed(1)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  };
  $("#main").querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => {
    range = b.dataset.range;
    if (range !== "all" && before) { before = false; const c = $("#hind-toggle"); if (c) c.checked = false; }
    draw(); b.focus();
  }));
  $("#hind-toggle")?.addEventListener("change", (e) => { before = e.target.checked; draw(); });
  draw();
}

// -- method and data -----------------------------------------------------------------
function method() {
  const U = S.U, st = S.status || {}, a = S.index.data_as_of || {};
  const stages = Object.entries(st.last_attempt || {});
  $("#main").innerHTML = `
    <section class="intro"><h1>Method and data</h1><p class="lede">How themes are found, how baskets are measured, what is covered, and how fresh the data is.</p></section>
    ${notices()}
    <section class="panel prose"><h2>What this is</h2>
      <p>The monitor reads the earnings-call transcripts and SEC filings of a fixed set of companies each month and groups passages that talk about the same thing. A group that is broad (several companies) and either growing quickly or persistent becomes a published theme with a permanent ID.</p>
      <p>Each theme has three separate kinds of measure, never combined into one score:</p>
      <ul><li><b>Emergence:</b> how fast the theme's share of comparable companies' text is growing, and how many companies newly mention it.</li>
        <li><b>Theme strength:</b> how consistently and widely it is discussed (months present, companies, sectors, coherence).</li>
        <li><b>Market strength:</b> how an equal-weight basket of the companies' shares has done against ${esc(S.B.join(" and "))}.</li></ul>
      <p>“Relative performance improving” means the basket's 3-month return minus ${esc(S.B[0])}'s rose by at least 5 percentage points since the previous month-end; “weakening” means it fell that much. It describes prices only.</p>
      <p>Themes are not deleted. A theme that is no longer discussed becomes inactive and keeps its history and its basket, including losses.</p></section>
    <section class="panel prose"><h2>Baskets</h2>
      <ul><li>Equal starting capital per company, bought at the first close after the theme (or a new basket version) was decided; then held without rebalancing.</li>
        <li>Basket versions are chain-linked: the new version starts at the index level already reached. Later additions never change earlier returns.</li>
        <li>US-dollar, dividend-adjusted prices for companies and benchmarks alike (${esc(S.B.join(", "))}).</li>
        <li>Missing prices are carried from the last close and reported, never dropped. Acquired companies convert on their recorded terms.</li>
        <li><b>Simulated vs live.</b> The monitor went live on ${esc(fmtDate(S.index.go_live))}. Index days before a theme's publication (or before a basket version was decided) are a historical simulation of the rule; only later days are observed. Returns are shown separately for each and labelled.</li></ul></section>
    <section class="panel prose"><h2>Benchmarks</h2>
      <p>Every theme is compared with ${esc(S.B.map((b) => `${b}${(S.index.benchmark_labels || {})[b] ? ` (${S.index.benchmark_labels[b]})` : ""}`).join(" and "))}.${Object.keys(S.index.benchmark_rule?.sector_benchmarks || {}).length ? ` A theme also gets one sector reference when at least ${Math.round(S.index.benchmark_rule.min_share * 100)}% of its first basket's companies are in one sector: ${esc(Object.entries(S.index.benchmark_rule.sector_benchmarks).map(([k, v]) => `${k}: ${v}`).join("; "))}. The choice is fixed when the first basket is decided, from its composition only, never from returns.` : ""}</p>
      <p>Benchmarks are capitalisation-weighted, while theme baskets give each company the same starting amount, so part of any difference reflects company size.</p></section>
    <section class="panel prose" id="coverage"><h2>Coverage</h2>
      <p><b>${esc(U.name)}</b> (version ${esc(U.version)}): ${esc(U.description)}</p>
      <p>${U.n_companies} companies: ${Object.entries(U.sectors || {}).map(([k, v]) => `${esc(k)} (${v})`).join(", ")}. A theme can span several sectors. “Strongest” always means strongest among these companies.</p>
      <p>Companies added to coverage are shown at once but count in growth comparisons only after 12 months of documents and once they were covered at the comparison date, so new coverage never looks like a theme taking off and never changes past basket returns.${(U.recently_added || []).length ? ` Recently added: ${esc(U.recently_added.join(", "))}.` : ""}</p>
      ${(U.changes || []).length ? `<ul>${U.changes.map((c) => `<li>${esc(fmtDate(c.date))}, version ${esc(c.version)}: ${esc(c.note || "")}${c.added?.length ? ` Added ${esc(c.added.join(", "))}.` : ""}${c.removed?.length ? ` Removed ${esc(c.removed.join(", "))}.` : ""}</li>`).join("")}</ul>` : ""}</section>
    <section class="panel" id="freshness"><h2>Freshness and the last update</h2>
      <dl class="dl"><dt>Prices through</dt><dd>${esc(fmtDate(a.prices))}</dd><dt>Calls and filings through</dt><dd>${esc(fmtDate(a.documents))}</dd>
        <dt>Themes processed through</dt><dd>${esc(fmtDate(a.themes))}</dd><dt>This release</dt><dd class="mono">${esc(S.index.release)}</dd>
        <dt>Generated</dt><dd>${esc(S.index.generated_at)}</dd>
        ${S.index.effective ? `<dt>Configuration</dt><dd>${esc(S.index.effective.title)} (profile ${esc(S.index.effective.profile)}), universe version ${esc(S.index.effective.universe_version)}: ${esc(Object.values(S.index.effective.files).flat().map((x) => `${x.path} ${x.sha256.slice(0, 8)}`).join(", "))}</dd>` : ""}
        ${S.index.fork ? `<dt>Started from</dt><dd>a copy of the ${esc(S.index.fork.from_profile)} registry at ${esc(fmtDate(S.index.fork.at_snapshot))} (${S.index.fork.inherited_themes.length} published themes kept with their IDs, baskets and history)</dd>` : ""}<dt>Last update attempt</dt><dd>${esc(st.checked_at || "—")} · ${st.ok ? "succeeded" : "did not complete"}</dd></dl>
      ${stages.length ? `<div class="table-wrap"><table><thead><tr><th>Stage</th><th>Result</th><th>Detail</th></tr></thead><tbody>${stages.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.ok ? "OK" : "<b>Failed</b>"}</td><td class="small">${esc(v.ok ? (typeof v.detail === "string" ? v.detail : "") : v.error)}</td></tr>`).join("")}${Object.entries(st.skipped || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>Skipped</td><td class="small">${esc(v)}</td></tr>`).join("")}${Object.entries(st.warnings || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>Warning</td><td class="small">${esc(v)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <p class="small muted">A release is published only when every stage succeeds; otherwise the previous release stays online and this page says what failed.</p></section>
    <section class="panel prose"><h2>Evaluation status</h2><p>${esc(S.index.evaluation_note || "")}</p>
      <p>This is a research tool, not investment advice. Simulated history shows what a rule would have done; it is not a track record.</p></section>`;
}

// -- shell ---------------------------------------------------------------------------
function setNav(name) {
  document.querySelectorAll(".nav a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.nav === name));
  document.querySelectorAll(".nav a[aria-current]").forEach((a) => a.setAttribute("aria-current", "page"));
}

async function route() {
  const token = ++S.nav;
  S.chart?.destroy(); S.chart = null;
  S.disc?.destroy(); S.disc = null;
  const h = location.hash || "#/";
  const [path, query] = h.slice(1).split("?");
  const m = path.match(/^\/theme\/(.+)$/);
  if (m) { setNav("themes"); await themePage(decodeURIComponent(m[1]), token); return; }
  document.title = "Theme Monitor";
  if (path === "/themes") { setNav("themes"); allThemes(new URLSearchParams(query || "")); }
  else if (path === "/method") { setNav("method"); method(); }
  else { setNav("overview"); overview(); }
  if (token === S.nav && !location.hash.includes("#coverage") && !location.hash.includes("#freshness")) window.scrollTo(0, 0);
}

function themeToggle() {
  const btn = $("#theme-toggle"), order = ["auto", "light", "dark"];
  const apply = (v) => {
    if (v === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = v;
    btn.textContent = { auto: "Auto", light: "Light", dark: "Dark" }[v];
    btn.setAttribute("aria-label", `Colour scheme: ${v}. Change`);
  };
  let cur = store.get("tm.scheme", "auto");
  apply(cur);
  btn.addEventListener("click", () => { cur = order[(order.indexOf(cur) + 1) % 3]; store.set("tm.scheme", cur); apply(cur); });
}

async function start() {
  themeToggle();
  try {
    S.status = await getJSON("data/status.json").catch(() => null);
    const latest = await getJSON("data/latest.json");
    S.base = "data/" + latest.path;
    S.index = await getJSON(S.base + "index.json");
  } catch (e) {
    $("#main").removeAttribute("aria-busy");
    const failed = S.status && !S.status.ok;
    $("#main").innerHTML = `<section class="intro"><h1>Theme Monitor</h1></section><div class="notice ${failed ? "bad" : ""}"><div><b>No published data yet.</b> ${failed ? `The first update did not complete (${esc(Object.keys(S.status.errors || {}).join(", "))}).` : "The first update has not run."} Run <span class="mono">themegraph monitor refresh</span> to publish one.</div></div>`;
    return;
  }
  S.U = S.index.universe || {};
  S.B = S.index.benchmarks || S.U.benchmarks || S.B;
  S.byId = Object.fromEntries(S.index.themes.map((r) => [r.id, r]));
  $("#main").removeAttribute("aria-busy");
  $("#release").textContent = `Release ${S.index.release}`;
  window.addEventListener("hashchange", route);
  route();
}

start();
