// Themegraph research application. Reads the static feed written by `themegraph
// monitor refresh` (docs/METHOD.md) and renders:
//   #/               overview: what is emerging, changing and at risk
//   #/themes         theme explorer: sortable, filterable table of every theme
//   #/theme/<id>     one theme: performance, discussion, companies, evidence, record
//   #/companies      every company; #/company/<symbol> one company
//   #/clusters       market structure: stocks that trade together (HAC)
//   #/network        relationships: price, discussion and stated business links
//   #/macro          rates, oil, inflation and the dollar
//   #/research       what is measured, what is validated, what is unproven
//   #/method         methodology, coverage, freshness, release details
// Every figure shown comes from the feed. Nothing is estimated in the browser.
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
  analyst_question: "Analyst question", filing: "SEC filing", ir_document: "Company IR document" };
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
// Company display: the name with its ticker when the coverage list has one.
const nameOf = (sym) => S.index?.company_names?.[sym] || sym;
const coHref = (sym) => `#/company/${encodeURIComponent(sym)}`;
const company = (sym) => { const n = S.index?.company_names?.[sym]; return `<a class="co" href="${coHref(sym)}">${n ? `${esc(n)} <span class="tk">${esc(sym)}</span>` : `<span class="tk solo">${esc(sym)}</span>`}</a>`; };
const firstSentence = (t) => { const m = String(t || "").match(/^.{20,}?[.!?](?=\s|$)/); return m ? m[0] : String(t || ""); };
// Plain words beside every signed difference, so direction never rests on colour.
const aheadBehind = (x) => x == null ? "" : x > 0 ? "ahead" : x < 0 ? "behind" : "level";
const BASIS_WORD = { live: "live", reconstructed: "simulated", mixed: "simulated and live" };

function getJSON(url, signal) {
  return fetch(url, { cache: "no-store", signal }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} for ${url}`);
    return r.json();
  });
}

function notices(demo = false) {
  const out = [];
  const st = S.status;
  if (S.index?.fixture && demo) {
    out.push(`<div class="notice warn" role="note"><span class="icon" aria-hidden="true">!</span><div><b>Demonstration data.</b> The companies, documents and prices on this site are synthetic. They show how the monitor works; nothing here is a real theme or a real return.</div></div>`);
  }
  if (st && (Object.values(st.errors || {}).some(Boolean) || Object.keys(st.skipped || {}).length)) {
    const failed = Object.entries(st.errors || {}).filter(([, v]) => v).map(([k]) => k);
    out.push(`<div class="notice bad" role="alert"><span class="icon" aria-hidden="true">×</span><div><b>The latest update failed</b> (${esc(failed.join(", ") || "incomplete")}, ${esc(fmtDate(st.checked_at))}). You are seeing the last complete release, from ${esc(fmtDate(S.index?.generated_at))}. <a href="#/method#freshness">Details</a></div></div>`);
  }
  const gaps = S.index?.price_gaps || [];
  if (gaps.length) {
    out.push(`<div class="notice warn" role="note"><span class="icon" aria-hidden="true">!</span><div><b>${gaps.length === 1 ? "One price is" : `${gaps.length} prices are`} not up to date:</b> ${esc(gaps.slice(0, 8).map((g) => g.symbol).join(", "))}${gaps.length > 8 ? ` and ${gaps.length - 8} more` : ""}. Baskets holding them use the last close. <a href="#/method#freshness">Details</a></div></div>`);
  }
  const pend = S.index?.pending_snapshots || [];
  if (pend.length) {
    out.push(`<div class="notice" role="note"><span class="icon" aria-hidden="true">i</span><div><b>${pend.length === 1 ? "One month-end is" : `${pend.length} month-ends are`} waiting for documents.</b> Themes are shown through ${esc(fmtDate(S.index.themes_processed_through))}; ${esc(fmtDate(pend[0].t))} is not processed yet: ${esc(pend[0].reason)}. <a href="#/method#freshness">Details</a></div></div>`);
  }
  const late = S.index?.late_documents;
  if (late?.total) {
    out.push(`<div class="notice" role="note"><span class="icon" aria-hidden="true">i</span><div><b>${late.total} documents arrived late.</b> They were eligible for month-ends already processed and are not added to those; they count in later months. <a href="#/method#freshness">Details</a></div></div>`);
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
  if (m.status !== "ok") return { na: m.status === "awaiting_entry" ? "Not bought yet" : m.status === "unavailable" ? "Not valued" : "No prices" };
  const r = m.returns?.[S.period];
  if (!r) return { na: `${months(m.sessions)} months of prices; not enough for ${PERIODS[S.period][1]}` };
  return r;
}

function toneTag(row) {
  const d = row.tone?.direction;
  if (d === "headwind") return `<span class="tag hw" title="Companies are describing problems: much more problem language than usual">Headwind</span>`;
  if (d === "tailwind") return `<span class="tag tw" title="Companies are describing growth and demand">Tailwind</span>`;
  return "";
}

// Theme families (display only): themes that mean nearly the same thing.
function familyOf(row) {
  const f = row.family;
  if (!f || f.size < 2) return [];
  return S.index.themes.filter((r) => r.family?.id === f.id && r.id !== row.id);
}

function topicLine(t) {
  const g = t.group;
  if (!g) return "";
  const others = g.members.filter((id) => id !== t.id).map((id) => S.byId[id]).filter(Boolean);
  return `<p class="small family"><span class="muted">Part of the topic</span> <a href="#/themes?topic=${esc(g.id)}"><b>${esc(g.name)}</b></a>${others.length ? `<span class="muted">, with</span> ${others.slice(0, 4).map((r) => `<a href="#/theme/${esc(r.id)}">${esc(r.title)}</a>`).join(", ")}${others.length > 4 ? ` <span class="muted">+${others.length - 4}</span>` : ""}` : ""}</p>`;
}

// One row per family in overview lists: a family already shown in an earlier
// section is not repeated.
function onePerFamily(rows, shown) {
  return rows.filter((r) => {
    const k = r.family?.id || r.id;
    if (shown.has(k)) return false;
    shown.add(k);
    return true;
  });
}

// -- the theme table -------------------------------------------------------------------
// One table used everywhere themes are listed. Each column says what it shows and
// how it sorts; a missing value is a dash and sorts last, never a zero.
const STATUS_ORDER = { new: 0, strengthening: 1, stable: 2, cooling: 3, inactive: 4, merged: 5 };
const TOGETHER = { together: "Together", less: "Less than usual", usual: "Not unusual" };
const ROUTE = { emergence: "Emergence", established: "Established", newly_covered: "Newly covered" };
const COLS = {
  theme: { h: "Theme", cls: "t", text: true, sort: (r) => r.title.toLowerCase(),
    cell: (r) => `<a href="#/theme/${esc(r.id)}">${esc(r.title)}</a>${S.expanded && r.label_source !== "keywords" && r.summary ? `<span class="sub">${esc(firstSentence(r.summary))}</span>` : ""}<span class="sub">${esc((r.sectors || []).join(", "))}${familyOf(r).length ? ` · overlaps ${familyOf(r).length} similar` : ""}</span>` },
  status: { h: "Status", sort: (r) => -STATUS_ORDER[r.status], tip: "Discussion status: the last three months against the three before",
    cell: (r) => `<span class="meta">${chip(r.status)}${toneTag(r)}${r.route === "newly_covered" ? `<span class="tag" title="Visible because coverage expanded; not counted as emerging">Newly covered</span>` : ""}</span>` },
  topic: { h: "Topic", text: true, sort: (r) => (r.group?.name || "").toLowerCase() || null, cell: (r) => r.group ? esc(r.group.name) : `<span class="na">—</span>` },
  route: { h: "Entered by", text: true, sort: (r) => r.route, cell: (r) => esc(ROUTE[r.route] || r.route || "—") },
  first: { h: "Published", sort: (r) => r.published_snapshot, tip: "Month-end at which the theme entered the monitor", cell: (r) => `<span style="white-space:nowrap">${esc(fmtDate(r.published_snapshot, true))}</span>` },
  cos: { h: "Companies", num: true, sort: (r) => r.strength.n_issuers, tip: "Companies discussing the theme in the latest month", cell: (r) => r.strength.n_issuers ?? "—" },
  share: { h: "Share of text", num: true, sort: (r) => r.emergence.share, tip: "Share of all monitored passages in the last six months", cell: (r) => share(r.emergence.share, 1) },
  growth: { h: "Growth, 3m", num: true, sort: (r) => r.emergence.growth_3m, tip: "Latest month's share of comparable companies' text against three months ago. The status uses three-month averages, so the two can differ.",
    cell: (r) => r.emergence.growth_3m == null ? `<span class="na">—</span>` : `${r.emergence.growth_3m.toFixed(2)}×` },
  months: { h: "Months of 12", num: true, sort: (r) => r.strength.persistence_12, tip: "Months in the last twelve in which the theme was discussed", cell: (r) => r.strength.persistence_12 ?? "—" },
  ret: { h: () => `${PERIODS[S.period][0]} return`, num: true, sort: (r) => periodReturn(r).theme, tip: "Equal-weight basket, not rebalanced",
    cell: (r) => { const x = periodReturn(r); return x.na ? `<span class="na" title="${esc(x.na)}">n/a</span>` : `${pct(x.theme)} ${basisTag(x.basis)}`; } },
  ex: { h: () => `vs ${bench()}`, num: true, sort: (r) => periodReturn(r)[`excess_vs_${bench()}`], tip: "Basket return minus the benchmark's, in percentage points",
    cell: (r) => { const x = periodReturn(r); return x.na ? `<span class="na">—</span>` : pts(x[`excess_vs_${bench()}`]); } },
  trend: { h: "3m vs bench", num: true, sort: (r) => r.market.trend?.excess_3m_pp, tip: "Three-month return minus the benchmark's, in points", cell: (r) => r.market.trend?.excess_3m_pp == null ? `<span class="na">—</span>` : pts(r.market.trend.excess_3m_pp / 100) },
  change: { h: "1m change", num: true, sort: (r) => r.market.trend?.change_pp_1m, tip: "Change in that three-month difference since the previous month-end", cell: (r) => r.market.trend?.change_pp_1m == null ? `<span class="na">—</span>` : pts(r.market.trend.change_pp_1m / 100) },
  together: { h: "Co-movement", sort: (r) => r.market.trading_together_pct, tip: "Whether the basket's stocks moved together beyond the market over six months, against random baskets",
    cell: (r) => r.market.trading_together_verdict ? esc(TOGETHER[r.market.trading_together_verdict] || r.market.trading_together_verdict) : `<span class="na">—</span>` },
  spark: { h: "Basket vs bench", cls: "sp", tip: "Basket (solid) and benchmark (dashed); shaded where live",
    cell: (r) => { const k = r.market.spark; return k ? sparkline({ dates: k.dates, theme: k.theme, benchmark: k.benchmark, liveFrom: k.live_from, label: `Basket and ${esc(k.bench)} for ${esc(r.title)}` }) : ""; } },
};
const colHead = (c) => typeof c.h === "function" ? c.h() : c.h;
function sortRows(rows, key, dir) {
  const c = COLS[key];
  if (!c?.sort) return rows;
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((x, y) => {
    const a = c.sort(x), b = c.sort(y);
    if (a == null && b == null) return 0;
    if (a == null) return 1;      // missing values last in either direction
    if (b == null) return -1;
    return (a < b ? -1 : a > b ? 1 : 0) * sign;
  });
}
function themeTable(rows, cols, { empty = "No themes.", sort = null, dir = "desc", sortable = false, caption = "" } = {}) {
  if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
  const head = cols.map((k) => { const c = COLS[k], on = sortable && sort === k;
    const label = esc(colHead(c));
    return `<th${c.num ? ' class="num"' : ""}${c.tip ? ` title="${esc(c.tip)}"` : ""}${on ? ` aria-sort="${dir === "asc" ? "ascending" : "descending"}"` : ""}>${sortable && c.sort ? `<button type="button" data-sort="${k}">${label}</button>` : label}</th>`; }).join("");
  return `<div class="table-wrap"><table class="themes">${caption ? `<caption>${esc(caption)}</caption>` : ""}<thead><tr>${head}</tr></thead><tbody>${
    rows.map((r) => `<tr>${cols.map((k) => { const c = COLS[k]; return `<td class="${[c.num ? "num" : "", c.cls || ""].join(" ").trim()}">${c.cell(r)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>`;
}
const stat = (k, v, s = "", href = "") => `<${href ? `a href="${href}"` : "div"} class="stat"><span class="k">${k}</span><span class="v${/^[\d+−<]/.test(String(v).replace(/<[^>]+>/g, "")) ? "" : " word"}">${v}</span>${s ? `<span class="s">${s}</span>` : ""}</${href ? "a" : "div"}>`;
const pageHead = (title, lede, right = "", eyebrow = "") => `<div class="page-head"><div>${eyebrow ? `<p class="eyebrow">${esc(eyebrow)}</p>` : ""}<h1>${esc(title)}</h1>${lede ? `<p class="lede">${lede}</p>` : ""}</div>${right}</div>`;

// -- overview ------------------------------------------------------------------------
function overview() {
  const L = S.index.lists, by = S.byId, b = bench(), T = S.index.themes;
  const live = (r) => r && !["inactive", "merged"].includes(r.status);
  const active = T.filter(live);
  const n = (f) => active.filter(f).length;
  const emergingIds = new Set(L.emerging);
  const shown = new Set();
  const rising = active.filter((r) => !emergingIds.has(r.id) && ["new", "strengthening"].includes(r.status))
    .sort((x, y) => (y.emergence.growth_3m ?? 0) - (x.emergence.growth_3m ?? 0));
  const featured = onePerFamily([...L.emerging.map((id) => by[id]).filter(live), ...rising], shown);
  const headwinds = onePerFamily((L.headwinds || []).map((id) => by[id]).filter(Boolean), shown);
  const established = onePerFamily(L.established.map((id) => by[id]).filter(Boolean), shown);
  const newly = (L.newly_covered || []).map((id) => by[id]).filter(Boolean);
  const list = (ids) => ids.map((id) => by[id]).filter(Boolean);
  const a = S.index.data_as_of || {}, U = S.U;
  const cos = S.index.companies || [], inTheme = cos.filter((c) => c.themes.length).length;
  const latest = String(S.index.themes_processed_through || "").slice(0, 10);
  const fresh = active.filter((r) => String(r.published_snapshot || "").slice(0, 10) === latest);
  const joiners = active.filter((r) => (r.emergence.new_issuers_3m || []).length)
    .sort((x, y) => y.emergence.new_issuers_3m.length - x.emergence.new_issuers_3m.length);
  const G = S.index.theme_groups || [];
  const render = () => {
    const cap = (rows, key) => rows.slice(0, S[key] ? undefined : 8);
    const more = (rows, key, id) => rows.length > 8 ? `<button type="button" class="link-btn" id="${id}" aria-expanded="${!!S[key]}">${S[key] ? "Show fewer" : `Show all ${rows.length}`}</button>` : "";
    $("#main").innerHTML = `
      ${pageHead("Overview", `What ${U.n_companies ?? "the monitored"} companies are discussing in earnings calls and filings, and how the shares involved have traded. Discussion and prices are measured separately and never combined into one score.`, periodControl(), U.name || "")}
      ${notices(true)}
      <div class="stats">
        ${stat("Active themes", active.length, `${T.length} published in all`, "#/themes?status=active")}
        ${stat("Emerging", n((r) => emergingIds.has(r.id)), "flagged by the emergence rule", "#/themes?status=active&route=emergence&sort=growth")}
        ${stat("Strengthening", n((r) => r.status === "strengthening"), "discussion up on three months", "#/themes?status=strengthening")}
        ${stat("Cooling", n((r) => r.status === "cooling"), "discussion down on three months", "#/themes?status=cooling")}
        ${stat("Describing problems", n((r) => r.tone?.direction === "headwind"), "far more problem language than usual", "#/themes?status=active&tone=headwind")}
        ${stat("Companies in a theme", `${inTheme}<span class="muted" style="font-size:.6em;font-weight:500"> of ${cos.length}</span>`, "in at least one active theme", "#/companies?show=in")}
      </div>
      <div class="grid g-main">
        <div class="stackv">
          <section class="panel" aria-labelledby="h-em"><div class="panel-head"><div><h2 id="h-em">Emerging and strengthening</h2>
            <p class="note">Companies are talking about these more than three months ago. A description of discussion, not a forecast for the shares.</p></div>
            <a class="more" href="#/themes?status=active&sort=growth">All ${active.length} active →</a></div>
            ${themeTable(cap(featured, "allFeatured"), ["theme", "status", "cos", "growth", "ret", "ex", "spark"], { empty: "No theme is emerging or strengthening right now. Themes appear here when discussion of a topic grows across several companies." })}
            <p class="more">${more(featured, "allFeatured", "more-em")}</p></section>
          <section class="panel" aria-labelledby="h-hw"><div class="panel-head"><div><h2 id="h-hw">Where companies describe problems</h2>
            <p class="note">Far more talk of weak demand, excess inventory or pricing pressure than usual. Whether these baskets go on to lag is being tested, not established.</p></div>
            ${headwinds.length ? `<a class="more" href="#/themes?status=active&tone=headwind">All →</a>` : ""}</div>
            ${themeTable(headwinds.slice(0, 8), ["theme", "status", "cos", "growth", "ret", "ex", "spark"], { empty: "No theme stands out for problem language right now." })}</section>
          <section class="panel" aria-labelledby="h-es"><div class="panel-head"><div><h2 id="h-es">Long-running themes</h2>
            <p class="note">Discussed in at least 6 of the last 12 months by four or more companies.</p></div></div>
            ${themeTable(cap(established, "allEstablished"), ["theme", "status", "cos", "months", "ret", "ex", "spark"], { empty: "No long-running themes yet." })}
            <p class="more">${more(established, "allEstablished", "more-es")}</p></section>
          ${newly.length ? `<section class="panel"><div class="panel-head"><div><h2>Newly covered</h2><p class="note">Visible because coverage expanded: long-running discussion among newly added companies, found in their earlier documents. Not counted as emerging, and tracked only from publication.</p></div></div>
            ${themeTable(newly.slice(0, 8), ["theme", "status", "cos", "months", "ret", "ex"], {})}</section>` : ""}
        </div>
        <div class="stackv">
          <section class="panel"><div class="panel-head"><h2>Latest month-end${latest ? `: ${esc(fmtDate(latest))}` : ""}</h2></div>
            <dl class="dl"><dt>First published</dt><dd>${fresh.length ? fresh.slice(0, 6).map((r) => `<a href="#/theme/${esc(r.id)}">${esc(r.title)}</a>`).join("; ") + (fresh.length > 6 ? ` <span class="muted">+${fresh.length - 6}</span>` : "") : `<span class="muted">No new theme this month</span>`}</dd>
              <dt>New participants</dt><dd>${joiners.length ? joiners.slice(0, 5).map((r) => `<a href="#/theme/${esc(r.id)}">${esc(r.title)}</a> <span class="muted">+${r.emergence.new_issuers_3m.length} (${esc(r.emergence.new_issuers_3m.slice(0, 3).map(nameOf).join(", "))}${r.emergence.new_issuers_3m.length > 3 ? "…" : ""})</span>`).join("<br>") : `<span class="muted">No theme gained a company in three months</span>`}</dd></dl>
            <p class="note" style="margin-top:8px">New participants: companies that began discussing a theme in the last three months.</p></section>
          <section class="panel"><div class="panel-head"><div><h2>Baskets gaining ground against ${esc(b)}</h2><p class="note">Three-month return minus ${esc(b)}'s rose by 5 points or more since the previous month-end. Prices only.</p></div></div>
            ${themeTable(list(L.market_gaining).slice(0, 6), ["theme", "trend", "change"], { empty: "None this month." })}</section>
          <section class="panel"><div class="panel-head"><div><h2>Baskets losing ground</h2><p class="note">The same measure fell by 5 points or more. A well-discussed theme can still have a falling basket.</p></div></div>
            ${themeTable(list(L.market_losing).slice(0, 6), ["theme", "trend", "change"], { empty: "None this month." })}</section>
          ${G.length ? `<section class="panel"><div class="panel-head"><div><h2>Topics</h2><p class="note">Active themes close in meaning, grouped for reading. Each theme keeps its own basket.</p></div></div>
            <ul class="plain subthemes">${G.map((g) => `<li><a href="#/themes?topic=${esc(g.id)}">${esc(g.name)}</a> <span class="small muted">${g.members.length} themes · ${g.companies.length} companies</span></li>`).join("")}</ul></section>` : ""}
          <section class="panel"><div class="panel-head"><h2>Data</h2><a class="more" href="#/research">Validation status →</a></div>
            <dl class="dl"><dt>Prices through</dt><dd>${esc(fmtDate(a.prices))}</dd><dt>Calls and filings through</dt><dd>${esc(fmtDate(a.documents))}</dd>
              <dt>Themes through</dt><dd>${esc(fmtDate(a.themes))}</dd><dt>Coverage</dt><dd>${U.n_companies ?? "—"} companies · <a href="#/method#coverage">what is covered</a></dd>
              <dt>Quiet themes</dt><dd><a href="#/themes?status=cooling">${(L.cooling || []).length} cooling</a> · <a href="#/themes?status=inactive">${L.inactive.length} inactive</a>, kept with their history</dd></dl></section>
        </div>
      </div>
      <p class="note" style="margin-top:12px">Returns: each basket started with equal money in every company and is not rebalanced. ${esc(PERIODS[S.period][1])} return to ${esc(fmtDate(a.prices))}, against ${esc(b)} in percentage points (pts). <span class="tag sim">Simulated</span> marks history rebuilt after the fact; <span class="tag live">Live</span> marks prices observed after the theme was published.</p>`;
    bindPeriod($("#main"), render);
    $("#more-es")?.addEventListener("click", () => { S.allEstablished = !S.allEstablished; render(); $("#more-es")?.focus(); });
    $("#more-em")?.addEventListener("click", () => { S.allFeatured = !S.allFeatured; render(); $("#more-em")?.focus(); });
  };
  render();
}

// -- all themes ----------------------------------------------------------------------
const LEGACY_SORT = { return: "ret", excess: "ex", growth: "growth", companies: "cos", persistence: "months", recent: "first", name: "theme" };
function allThemes(params) {
  const sectors = [...new Set(S.index.themes.flatMap((r) => r.sectors || []))].sort();
  const sort0 = LEGACY_SORT[params.get("sort")] || (COLS[params.get("sort")] ? params.get("sort") : "ret");
  const q = { q: params.get("q") || "", status: params.get("status") || "", sector: params.get("sector") || "",
    route: params.get("route") || "", tone: params.get("tone") || "", topic: params.get("topic") || "",
    sort: sort0, dir: params.get("dir") || (COLS[sort0].text ? "asc" : "desc") };
  const opts = (list, cur) => list.map(([v, l]) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(l)}</option>`).join("");
  S.expanded = store.get("tm.expanded", "0") === "1";
  $("#main").innerHTML = `
    ${pageHead("Themes", "Every theme the monitor has published, including cooling, inactive and merged ones. Nothing is deleted. Click a column heading to sort.", periodControl())}
    ${notices()}
    <form class="toolbar" role="search" onsubmit="return false">
      <label class="field">Filter<input id="f-q" type="search" placeholder="Theme, company or term" value="${esc(q.q)}"></label>
      <label class="field">Status<select id="f-status">${opts([["", "Any status"], ["active", "Active (not inactive)"], ...Object.entries(STATUS)], q.status)}</select></label>
      <label class="field">Sector<select id="f-sector">${opts([["", "Any sector"], ...sectors.map((x) => [x, x])], q.sector)}</select></label>
      <label class="field">Entered by<select id="f-route">${opts([["", "Any"], ["emergence", "Emergence rule"], ["established", "Established (persistent)"], ["newly_covered", "Newly covered"]], q.route)}</select></label>
      <label class="field">Kind of news<select id="f-tone">${opts([["", "Any"], ["headwind", "Headwind (problems)"], ["tailwind", "Tailwind (growth)"], ["mixed", "Mixed"]], q.tone)}</select></label>
      ${(S.index.theme_groups || []).length ? `<label class="field">Topic<select id="f-topic">${opts([["", "Any topic"], ...S.index.theme_groups.map((g) => [g.id, g.name])], q.topic)}</select></label>` : ""}
      <div class="seg" role="group" aria-label="Row detail"><button type="button" id="v-compact" aria-pressed="${!S.expanded}">Compact</button><button type="button" id="v-expanded" aria-pressed="${S.expanded}">With summaries</button></div>
    </form>
    <p class="count" id="count" aria-live="polite"></p>
    <section class="panel flush" id="list"></section>`;
  let sort = q.sort, dir = q.dir;
  const read = () => ({ q: $("#f-q").value.trim(), status: $("#f-status").value, sector: $("#f-sector").value,
    route: $("#f-route").value, tone: $("#f-tone").value, topic: $("#f-topic")?.value || "" });
  const draw = (focusSort) => {
    const f = read();
    const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
    const rows = sortRows(S.index.themes.filter((r) => {
      if (f.status === "active" ? ["inactive", "merged"].includes(r.status) : f.status && r.status !== f.status) return false;
      if (f.sector && !(r.sectors || []).includes(f.sector)) return false;
      if (f.route && r.route !== f.route) return false;
      if (f.tone && r.tone?.direction !== f.tone) return false;
      if (f.topic && r.group?.id !== f.topic) return false;
      const hay = [r.title, r.summary, r.name, r.id, ...(r.sectors || []), ...(r.key_constituents || []).flatMap((c) => [c.symbol, nameOf(c.symbol)])].join(" ").toLowerCase();
      return words.every((w) => hay.includes(w));
    }), sort, dir);
    $("#count").textContent = `${rows.length} of ${S.index.themes.length} themes`;
    const cols = ["theme", "status", ...((S.index.theme_groups || []).length ? ["topic"] : []), "first", "cos", "growth", "months", "ret", "ex", "spark", "together", "share"];
    $("#list").innerHTML = themeTable(rows, cols, { empty: "No themes match these filters.", sort, dir, sortable: true });
    $("#list").querySelectorAll("[data-sort]").forEach((btn) => btn.addEventListener("click", () => {
      const k = btn.dataset.sort;
      if (k === sort) dir = dir === "asc" ? "desc" : "asc"; else { sort = k; dir = COLS[k].text ? "asc" : "desc"; }
      draw(k);
    }));
    if (focusSort) $(`#list [data-sort="${focusSort}"]`)?.focus();
    const p = new URLSearchParams(Object.entries({ ...f, sort: sort === "ret" ? "" : sort, dir: dir === (COLS[sort].text ? "asc" : "desc") ? "" : dir }).filter(([, v]) => v));
    history.replaceState(null, "", `#/themes${p.toString() ? "?" + p : ""}`);
  };
  ["f-q", "f-status", "f-sector", "f-route", "f-tone", "f-topic"].forEach((id) => $("#" + id)?.addEventListener(id === "f-q" ? "input" : "change", () => draw()));
  const setView = (v) => { S.expanded = v; store.set("tm.expanded", v ? "1" : "0"); $("#v-compact").setAttribute("aria-pressed", String(!v)); $("#v-expanded").setAttribute("aria-pressed", String(v)); draw(); };
  $("#v-compact").addEventListener("click", () => setView(false));
  $("#v-expanded").addEventListener("click", () => setView(true));
  bindPeriod($("#main"), () => draw());
  draw();
}

// -- one theme -----------------------------------------------------------------------
const RANGES = [["3m", "3M", 63], ["6m", "6M", 126], ["1y", "1Y", 252], ["all", "All", null], ["live", "Live", null]];

// Macro sensitivity (prices only): how a basket or cluster has moved with rates,
// oil, inflation and the dollar, beyond the market. A description, not a forecast.
const lowerFactor = (t) => t.toLowerCase().replace("us dollar", "the US dollar");
const signed = (v) => `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`;
function macroLine(g) {
  const M = S.index.macro;
  if (!M?.available || !g) return "";
  const short = (f) => lowerFactor(f.label.split(" (")[0]);
  const parts = [];
  for (const f of M.factors) for (const up of [true, false]) {
    const spans = M.spans.filter((sp) => { const x = g[sp.key]?.[f.key]; return x?.clear && (x.beta > 0) === up; });
    if (spans.length) parts.push(`${up ? "rose" : "fell"} when ${esc(short(f))} rose (${spans.map((sp) => sp.label.replace(" days", "")).join(", ")} days)`);
  }
  if (!parts.length) return `<p class="small muted">Macro: no clear link to ${M.factors.map(short).join(", ")} at any time frame.</p>`;
  return `<p class="small">Macro (beyond ${esc(M.index)}): ${parts.join("; ")}.</p>`;
}
function macroHTML(g) {
  const M = S.index.macro;
  if (!M?.available) return "";
  const head = `<h2>Rates, oil, inflation and the dollar</h2>`;
  if (!g) return `<div class="macro">${head}<p class="small muted">Not enough price history for this basket.</p></div>`;
  const cell = (f, sp) => { const x = g[sp.key]?.[f.key]; return x ? `<td class="num">${x.clear ? `<b>${signed(x.beta)}</b>` : `<span class="muted">${signed(x.beta)}</span>`}</td>` : `<td class="num muted">—</td>`; };
  const months = Math.max(...M.spans.map((sp) => g[sp.key]?.months || 0));
  return `<div class="macro">${head}
    <p class="small muted">Over each time frame: when the factor rose over that span, did this basket tend to rise (+) or fall (−) over the same span, beyond what ${esc(M.index)} and the other factors explain? These are changes in the factors, not their levels. Up to ${months} months of history to ${esc(M.end)}, current companies, equal weight.</p>
    <div class="table-wrap"><table class="stack"><thead><tr><th>Factor</th>${M.spans.map((sp) => `<th class="num">${esc(sp.label)}</th>`).join("")}</tr></thead>
    <tbody>${M.factors.map((f) => `<tr><td>${esc(f.label)}</td>${M.spans.map((sp) => cell(f, sp)).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="small muted"><b>Bold</b> is a clear link: larger than in 95% of trials with the factor history slid out of step with the returns. Grey is not distinguishable from chance. A value of +0.40 means the basket's return over the span was 0.4 of its usual move higher when the factor rose by one of its usual moves. A dash means too little history for that span. With five factors and five time frames some bold values appear by chance, and baskets that share companies tend to show the same ones: a description of the past, not a forecast.${M.missing.length ? ` Not available: ${esc(M.missing.join(", "))}.` : ""}</p></div>`;
}
function factorMovesHTML() {
  const M = S.index.macro;
  if (!M?.available || !M.factor_moves) return "";
  const fmt = (v, unit) => v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}${unit === "pp" ? " pts" : "%"}`;
  return `<section class="section" aria-labelledby="h-fm"><div><h2 id="h-fm">What the macro factors did</h2>
    <p class="muted small">The change in each factor over each time frame, to ${esc(M.end)}. Yields in percentage points; oil, the dollar and consumer prices in percent. CPI is counted from the month it was published, so it runs about two months behind.</p></div>
    <div class="table-wrap"><table class="stack"><thead><tr><th>Factor</th>${M.spans.map((sp) => `<th class="num">${esc(sp.label)}</th>`).join("")}</tr></thead>
    <tbody>${M.factors.map((f) => { const x = M.factor_moves[f.key]; return `<tr><td>${esc(f.label)}</td>${M.spans.map((sp) => `<td class="num">${fmt(x?.moves?.[sp.key], x?.unit)}</td>`).join("")}</tr>`; }).join("")}</tbody></table></div></section>`;
}

function togetherHTML(tt) {
  if (!tt) return "";
  const names = S.index.benchmark_labels || {};
  const idx = `${tt.index}${names[tt.index] ? ` (${names[tt.index]})` : ""}`;
  const num = (x) => Number(x).toLocaleString("en-US");
  const mix = (x) => x.segment_matched ? "the same mix of segments"
    : `segments matched where possible (${Math.round(x.same_segment_share * 100)}% of places)`;
  const line = (x, when) => {
    if (!x) return `<li>${when}: not enough price history.</li>`;
    if (!x.informative) return `<li>${when}: cannot be judged — ${esc(x.note || "not enough price history")}.</li>`;
    return `<li>${when} (${esc(fmtDate(x.start))} – ${esc(fmtDate(x.end))}): the ${x.companies} companies moved together more than <b>${num(x.below)} of ${num(x.random_baskets)}</b> random baskets with ${mix(x)}${x.ties ? `, and the same as ${num(x.ties)}` : ""}.</li>`;
  };
  const n = tt.now, b = tt.before;
  const verdict = n && n.informative ? { together: "Trading together", less: "Less connected than usual", usual: "Not unusual" }[n.verdict] : "";
  return `<div class="together"><h2>Co-movement, beyond the market</h2>
    <p class="small muted">Whether these stocks move as a group once the part explained by ${esc(idx)} is removed. Prices only: separate from how much the theme is discussed and from the basket's return.</p>
    ${verdict ? `<p class="verdict">${verdict}</p>` : n ? `<p class="verdict">Cannot be judged</p>` : ""}
    ${n && n.informative && !n.segment_matched && n.same_segment_share < 0.5 ? `<p class="small muted">Few other monitored companies share these segments, so most random baskets come from other segments. Part of this may be ordinary sector co-movement rather than the theme.</p>` : ""}
    <ul class="plain">${line(n, "Last 6 months")}${line(b, "6 months before")}</ul>
    ${n && n.excess_corr != null ? `<details><summary>How this is measured</summary><div class="body prose small">
      <p>Daily returns of every monitored company with its own close on at least 90% of the sessions, linked across days without a close. Their covariance is shrunk toward a single-index model on ${esc(tt.index)} (Ledoit and Wolf, 2003; shrinkage ${n.shrinkage.toFixed(2)} for the last 6 months), and each pair's correlation beyond what ${esc(tt.index)} explains is averaged over the basket: ${n.excess_corr.toFixed(3)} now${b && b.excess_corr != null ? `, ${b.excess_corr.toFixed(3)} before` : ""}.</p>
      <p>With this model every pair's level is scaled down by the same factor (one minus the shrinkage), so the comparison with random baskets of the same size and segment mix is the measure, not the level. At a shrinkage of 1 nothing is left beyond the market and no comparison is made. ${n.left_out?.length ? `Left out for too few closes, or no movement apart from the market: ${esc(n.left_out.join(", "))}.` : ""}</p></div></details>` : ""}
  </div>`;
}

// The headline facts, each in its own cell: discussion, breadth, returns and how the
// shares trade are separate questions and are never combined.
function themeStats(t, m, B, held, others) {
  const R = t.performance.returns || {}, r = R["12m"] || R.full_period, b = B[0];
  const g = m.growth_3m, ex = r ? r[`excess_vs_${b}`] : null;
  const row = S.byId[t.id] || {}, tt = t.trading_together?.now, tone = row.tone?.direction;
  return `<div class="stats">
    ${stat("Discussion", esc(STATUS[t.status] || t.status), g == null ? "No three-month comparison yet" : `Latest month ${g.toFixed(2)}× its share three months ago`)}
    ${stat("Companies", m.n_issuers ?? "—", held.length ? `${held.length} held in the basket${others.length ? `, ${others.length} discussing only` : ""}` : "No basket yet")}
    ${stat("Share of text", share(m.share, 1), `Discussed in ${m.persistence_12 ?? "—"} of the last 12 months`)}
    ${stat(r ? `Basket · ${r === R["12m"] ? "12 months" : "since start"}` : "Basket", r ? pct(r.theme) : "—", r ? `${basisTag(r.basis)} ${esc(fmtDate(r.start))} – ${esc(fmtDate(r.end))}` : esc(t.performance.note || "Not valued yet"))}
    ${stat(`Against ${esc(b)}`, r ? pts(ex) : "—", r ? `${esc(b)} ${esc(plainPct(r[b]))} over the same days` : "")}
    ${tt ? stat("Co-movement", tt.informative ? esc(TOGETHER[tt.verdict] || tt.verdict) : "Cannot be judged", "Six months, beyond the market, against random baskets") : ""}
    ${tone && tone !== "none" ? stat("Language", esc({ headwind: "Headwind", tailwind: "Tailwind", mixed: "Mixed" }[tone] || tone), "Problem or growth wording against the usual level") : ""}
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
    <b>${company(q.symbol)}</b><span>${esc(fmtDate(q.date))}</span><span>${esc(q.doc_type || "")}</span>
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
      $("#main").innerHTML = `${notices()}<p class="notice bad">Theme ${esc(id)} is not in the current release. <a href="#/themes">Themes</a></p>`;
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
  document.title = `${t.title} · Themegraph`;
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

  // Who is in the theme: held in the basket, or discussing it without being held.
  const held = p.status === "ok" ? p.constituents : (t.basket_versions.at(-1)?.constituents || []);
  const heldSet = new Set(held.map((c) => c.symbol));
  const others = (m.issuers || []).filter((x) => !heldSet.has(x));
  const quotes = t.evidence.latest || [];
  const tt = t.trading_together?.now;

  const histTable = `<div class="table-wrap"><table class="dense"><thead><tr><th>Month</th><th class="num">Share</th><th class="num">Growth</th><th class="num">Companies</th><th class="num">Coherence</th><th>Emergence flag</th></tr></thead>
      <tbody>${[...t.measures].reverse().slice(0, 24).map((r) => `<tr><td>${esc(fmtDate(r.t, true))}</td><td class="num">${r.present ? share(r.share, 2) : r.observed === false ? "no evidence" : "absent"}</td><td class="num">${r.growth_3m == null ? "—" : r.growth_3m.toFixed(2) + "×"}</td><td class="num">${r.n_issuers ?? "—"}</td><td class="num">${r.coherence?.toFixed(2) ?? "—"}</td><td>${r.flagged_comparable ? "flagged" : ""}</td></tr>`).join("")}</tbody></table></div>`;
  const related = t.family?.related || [];
  $("#main").innerHTML = `
    <nav class="crumb" aria-label="Breadcrumb"><a href="#/">Overview</a> / <a href="#/themes">Themes</a> / <span class="mono">${esc(t.id)}</span></nav>
    ${notices()}
    <header class="hero"><div class="card-top"><h1>${esc(t.title)}</h1><span class="meta">${toneTag(S.byId[t.id] || {})} ${chip(t.status)}</span></div>
      <p class="summary">${esc(t.summary)}</p>
      <p class="note">${labelNote(t)}</p>
      ${t.newly_covered ? `<p class="notice"><span><b>Newly covered.</b> This theme became visible when coverage expanded: ${t.newly_covered.new_companies.length} of its companies were added recently. Their earlier documents show the topic in ${t.newly_covered.backfill_present} of the ${t.newly_covered.of_last} months before ${esc(fmtDate(t.newly_covered.at))}; that is historical analysis, not an earlier discovery. Tracking began when it was published on ${esc(fmtDate(t.published_at))}.</span></p>` : ""}
      ${t.merged_into ? `<p class="notice warn">Merged into <a href="#/theme/${esc(t.merged_into)}">${esc(S.byId[t.merged_into]?.title || t.merged_into)}</a>. Its history and basket are kept here.</p>` : ""}
      ${topicLine(t)}
    </header>
    ${themeStats(t, m, B, held, others)}
    <nav class="jump" aria-label="On this page">${[...(p.status === "ok" ? [["h-mkt", "Performance"]] : []), ["h-disc", "Discussion"], ["h-ev", "Evidence"], ["h-cons", "Companies"], ...(p.status === "ok" && t.trading_together ? [["h-tt", "Co-movement"]] : []), ...(p.status === "ok" && S.index.macro?.available ? [["h-macro", "Macro"]] : []), ["h-detail", "Record"]].map(([id, l]) => `<button type="button" data-jump="${id}">${l}</button>`).join("")}</nav>

    <div class="grid g-main">
    <div class="stackv">
    <section class="panel" aria-labelledby="h-mkt"><div class="panel-head"><div><h2 id="h-mkt">Basket performance</h2>
      <p class="note">How the companies' shares have done. A separate question from how much the theme is discussed.</p></div>
      ${p.status === "ok" ? `<div class="seg" role="group" aria-label="Chart range">${RANGES.map(([k, l]) => `<button type="button" data-range="${k}">${l}</button>`).join("")}</div>` : ""}</div>
      ${p.status === "ok" ? `
        <div class="kpis" id="kpis"></div>
        <div class="legend"><span><i class="key" style="border-top-color:var(--series-1);border-top-width:3px"></i>Theme basket</span>${B.map((b, i) => `<span><i class="key dash" style="border-top-color:var(--series-${i + 2})"></i>${esc(b)}</span>`).join("")}<span><i class="band-key"></i>Live (observed)</span><span><span class="event-key" aria-hidden="true">▼</span>Event</span>${p.hindsight ? `<span><i class="hind-key"></i>Before entry (hindsight, when shown)</span>` : ""}<span class="muted">Unshaded: simulated</span></div>
        ${p.hindsight ? `<label class="check"><input type="checkbox" id="hind-toggle"> Show the ${Math.round(p.hindsight.sessions / 21)} months before the basket started (hindsight)</label>` : ""}
        <div id="chart"></div><p class="sr" aria-live="polite" id="readout"></p>
        <p class="note" id="hind-note"></p>
        <p class="note" id="events-note"></p>
        <p class="note">Index = 100 at the start of the selected range. ${esc(p.price_basis)}.</p>
        <div class="table-wrap"><table><thead><tr><th>Period</th><th class="num">Basket</th>${B.map((b) => `<th class="num">${esc(b)}</th>`).join("")}${B.map((b) => `<th class="num">vs ${esc(b)}</th>`).join("")}</tr></thead>
          <tbody>${retRow("3m", "3 months")}${retRow("6m", "6 months")}${retRow("12m", "12 months")}${rec ? retRow("reconstructed", "Simulated period") : ""}${live ? retRow("live", "Live period") : ""}${retRow("full_period", "Whole history")}</tbody></table></div>
        <details><summary>How to read these returns</summary><div class="body"><ul class="plain notes note">
          <li><b>How the basket works:</b> each company started with an equal share of the money, bought at the first close after the decision. It is not rebalanced, so weights drift with prices. No trading costs, taxes or fees are deducted.</li>
          <li><b>“vs”</b> is the basket's return minus the benchmark's, in percentage points (pts). Prices through ${esc(fmtDate(S.index.data_as_of?.prices))}.</li>
          <li><b>Simulated or live:</b> ${seriesNote.trim()}</li>
          <li>${benchText(t, B)}</li>
          ${trend.trend ? `<li>Against ${esc(B[0])} the basket is <b>${esc(trend.trend === "gaining" ? "gaining ground" : trend.trend === "losing" ? "losing ground" : "holding steady")}</b>: its 3-month return is ${num(trend.excess_3m_pp, 1)} pts ${aheadBehind(trend.excess_3m_pp)}, a change of ${num(trend.change_pp_1m, 1)} pts since the previous month-end.</li>` : ""}
        </ul></div></details>
        <details><summary>Chart data as a table</summary><div class="body table-wrap" id="chart-table"></div></details>`
        : `<p class="empty">${esc(p.note || "No prices yet.")}</p>`}
    </section>

    <section class="panel" aria-labelledby="h-disc"><div class="panel-head"><div><h2 id="h-disc">Discussion over time</h2>
        <p class="note">How much and how widely companies talk about this theme. Independent of share prices.</p></div></div>
        <div class="legend"><span><i class="key" style="border-top-color:var(--series-1);border-top-width:3px"></i>Share of all monitored text (%)</span><span><span class="event-key up" aria-hidden="true">▲</span>Strengthening or recovered</span><span><span class="event-key down" aria-hidden="true">▼</span>Cooling, levelling off or going quiet</span></div>
        <div id="disc-chart"></div><p class="sr" aria-live="polite" id="disc-readout"></p>
        <p class="note">Markers are changes of status, decided at each month-end looking back: the average of the last three months against the three before, within a fixed group of companies. One unusually large month can therefore move the status when it arrives and again when it leaves the comparison. Adding or removing companies can move the line without a marker.${t.measures.slice(-36).some((r) => !r.present && r.observed === false) ? " Breaks in the line are months with missing evidence: too few of the theme's companies had documents to tell whether it was discussed. Zero means the documents were there and the theme was not found." : ""}</p>
        <dl class="dl cols">
          <dt>Latest month vs 3 months ago</dt><dd>${m.growth_3m == null ? "No comparison yet" : `${m.growth_3m.toFixed(2)}× (share of comparable companies' text; one month against one month, so it can differ from the status)`}</dd>
          <dt>Share of text</dt><dd>${share(m.share, 1)} of passages in the last six months</dd>
          <dt>Persistence</dt><dd>Discussed in ${m.persistence_12 ?? "—"} of the last 12 months${t.first_seen && t.published_snapshot && t.first_seen.slice(0, 7) < t.published_snapshot.slice(0, 7) ? ` (first seen ${esc(fmtDate(t.first_seen, true))}, before it was published)` : ""}</dd>
          <dt>Sources</dt><dd>${share(mgmt)} management · ${share(roles.filing)} filings · ${share(roles.analyst_question)} analyst questions</dd>
          <dt>Entered</dt><dd>${esc(fmtDate(t.published_snapshot))}, via the ${{ emergence: "emergence rule", established: "established-theme rule", newly_covered: "newly-covered rule" }[t.route] || esc(t.route)}</dd>
          ${m.recently_added?.length ? `<dt>Recently added</dt><dd>${esc(m.recently_added.map(nameOf).join(", "))}: shown, but not counted in growth until they have enough history</dd>` : ""}
        </dl>
        <details><summary>Month by month</summary><div class="body" style="padding-left:0">${histTable}</div></details></section>

    <section class="panel evidence" aria-labelledby="h-ev"><div class="panel-head"><div><h2 id="h-ev">What companies are saying</h2>
      <p class="note">Passages closest to the centre of the theme in the latest month (${esc(fmtDate(t.evidence.latest_t))}), quoted as written, one per company first. Analyst questions show what investors asked; they are not company statements.</p></div></div>
      <div class="quotes">${quotes.slice(0, 4).map((q) => quoteHTML(q, cited)).join("") || `<p class="empty">No passages in the latest month.</p>`}</div>
      ${quotes.length > 4 ? `<details><summary>${quotes.length - 4} more passages</summary><div class="body quotes" style="padding-left:0">${quotes.slice(4).map((q) => quoteHTML(q, cited)).join("")}</div></details>` : ""}
      ${t.evidence.first_t && t.evidence.first_t !== t.evidence.latest_t ? `<details><summary>Passages from when the theme was first seen (${esc(fmtDate(t.evidence.first_t))})</summary><div class="body quotes" style="padding-left:0">${t.evidence.first.map((q) => quoteHTML(q, cited)).join("")}</div></details>` : ""}
    </section>
    </div>

    <div class="stackv">
    <section class="panel" aria-labelledby="h-cons"><div class="panel-head"><div><h2 id="h-cons">Companies</h2>
      <p class="note">${held.length ? (p.status === "ok" ? `${held.length} held, bought at the close on ${esc(fmtDate(p.current_entry))} (basket version ${p.current_version}). ${p.coverage_latest != null && p.coverage_latest < 1 ? `<b>${share(1 - p.coverage_latest)} of the basket's value is priced at an older close.</b>` : "Every holding has a current price."}` : "Decided, not bought yet.") : "No basket yet."}</p></div></div>
      ${t.pending_version ? `<p class="notice"><span>A new basket has been decided (${esc(t.pending_version.constituents.map(nameOf).join(", "))}) and will be bought at the first close after ${esc(fmtDate(t.pending_version.entry_after))}.</span></p>` : ""}
      ${held.length ? (p.status === "ok" ? `<div class="table-wrap"><table class="dense"><thead><tr><th>Held in the basket</th><th class="num">Weight</th><th class="num">Return</th><th class="num" title="Points contributed to the basket's return since the current basket version was bought, not over the whole history">Contrib.</th></tr></thead>
        <tbody>${p.constituents.map((c) => `<tr><td>${company(c.symbol)}<span class="sub">${esc(c.sector || "")}${c.price_status && c.price_status !== "fresh" ? ` · ${esc(priceText(c))}` : ""}</span></td>
          <td class="num">${share(c.weight_now, 1)}</td><td class="num">${pct(c.return_since_entry)}</td><td class="num">${pts(c.contribution, 2)}</td></tr>`).join("")}</tbody></table></div>
        <details><summary>Starting weights and roles</summary><div class="body table-wrap"><table class="dense"><thead><tr><th>Company</th><th>Role</th><th class="num">Start</th><th class="num">Now</th><th>Price</th></tr></thead>
        <tbody>${p.constituents.map((c) => `<tr><td>${company(c.symbol)}</td><td>${esc(c.role)}</td><td class="num">${share(c.start_weight, 1)}</td><td class="num">${share(c.weight_now, 1)}</td><td>${esc(priceText(c))}</td></tr>`).join("")}</tbody></table></div></details>`
        : `<ul class="plain names">${held.map((c) => `<li>${company(c.symbol)}</li>`).join("")}</ul>`) : ""}
      <h4>Discussing it, not held (${others.length})</h4>
      ${others.length ? `<ul class="plain names" style="margin-top:6px">${others.map((x) => `<li>${company(x)}</li>`).join("")}</ul>` : `<p class="note">Every company discussing the theme this month is in the basket.</p>`}
      <p class="note" style="margin-top:8px">A company joins the basket when enough of its own recent text is about the theme, and a change has to hold for two month-ends before it is bought.${m.new_issuers_3m?.length ? ` New to the discussion in the last 3 months: ${esc(m.new_issuers_3m.map(nameOf).join(", "))}.` : ""}</p>
    </section>
    ${p.status === "ok" && t.trading_together ? `<section class="panel" id="h-tt">${togetherHTML(t.trading_together)}</section>` : ""}
    ${p.status === "ok" && S.index.macro?.available ? `<section class="panel" id="h-macro">${macroHTML(t.macro)}</section>` : ""}
    ${related.length ? `<section class="panel"><div class="panel-head"><div><h2>Overlapping themes</h2><p class="note">Close in meaning (similarity ${esc(String(S.index.families?.min_cosine ?? 0.85))} or more). Each keeps its own basket and history.</p></div></div>
        <ul class="plain subthemes">${related.map((r) => `<li><a href="#/theme/${esc(r.id)}">${esc(S.byId[r.id]?.title || r.id)}</a> <span class="small muted">similarity ${r.cosine.toFixed(2)}${r.shared_companies.length ? ` · shares ${esc(r.shared_companies.slice(0, 5).map(nameOf).join(", "))}${r.shared_companies.length > 5 ? "…" : ""}` : ""}</span></li>`).join("")}</ul></section>` : ""}
    </div>
    </div>

    <section class="panel" aria-labelledby="h-detail" style="margin-top:12px"><div class="panel-head"><div><h2 id="h-detail">Record</h2>
      <p class="note">Background and the full history. Nothing here changes the figures above.</p></div></div>
    ${(t.overview || []).length ? `<details class="about"><summary>About this theme, in plain language</summary><div class="body prose">${t.overview.map((p) => `<div><h3>${esc(p.heading)}</h3><p>${esc(p.text)}</p></div>`).join("")}</div></details>` : ""}
    <details><summary>How this basket is calculated</summary><div class="body prose">
      <p>When a theme is published, each company with enough of its text in the theme gets an equal share of starting capital and is bought at the first closing price after that moment. The shares are then held without rebalancing, so weights drift with prices.</p>
      <p>If the companies in the theme change and the change holds for two month-ends, a new basket version starts at the index level reached so far (chain-linking). Companies only count from the day they were added; nothing is backfilled.</p>
      <p>Prices are in US dollars and include dividends. A company without a fresh price keeps its last price and is marked; an acquired company becomes what its holders received under the recorded terms.</p>
      <p><b>Simulated vs live.</b> The monitor started on ${esc(fmtDate(S.index.go_live))}. Before a theme's publication, the basket is a reconstruction of what the rule would have held. Only days after publication (and after each basket version was decided) count as live.</p>
      <p><a href="#/method">Methodology</a> · <a href="#/research">Research and validation</a></p></div></details>
    <details><summary>Membership history</summary><div class="body table-wrap"><table class="dense"><thead><tr><th>Version</th><th>Decided at</th><th>Basis</th><th>Why</th><th>Companies</th></tr></thead><tbody>${versions}</tbody></table></div></details>
    <details><summary>Names, status and lineage</summary><div class="body prose"><p class="small">Theme ID <span class="mono">${esc(t.id)}</span> never changes.</p><ul>${nameHist}</ul></div></details>
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
    </dl></div></details></section>`;
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
  // A month without the theme's companies' documents is missing evidence: a gap,
  // never a zero. Zero is a month that was observed and the theme was not found.
  const rows = t.measures.slice(-36).map((r) => ({ date: r.t.slice(0, 10), present: r.present,
    observed: r.present || r.observed !== false,
    v: { share: r.present ? 100 * r.share : r.observed === false ? null : 0 } }));
  if (rows.length < 2) return;
  const pos = Object.fromEntries(rows.map((r, i) => [r.date, i]));
  const markers = (t.status_points || []).filter((sp) => sp.t.slice(0, 10) in pos).map((sp) => ({
    index: pos[sp.t.slice(0, 10)], kind: sp.kind,
    label: `${sp.label}${sp.ratio != null ? ` (${sp.ratio.toFixed(2)}×)` : ""}` }));
  const hi = Math.max(...rows.map((r) => r.v.share));
  const dp = hi < 2 ? 2 : 1;
  S.disc?.destroy();
  S.disc = lineChart($("#disc-chart"), { rows, markers, base: null,
    series: [{ key: "share", label: "Share", color: "var(--series-1)", width: 2.5,
      missing: "no evidence" }],
    title: "Share of all monitored text discussing this theme, by month-end",
    format: (v) => `${v.toFixed(dp)}%`, tick: (v) => `${v.toFixed(dp)}%`,
    describe: (r) => (r.present ? "" : r.observed ? "not found that month"
      : "missing evidence: too few of its companies had documents that month"),
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
// Forward tests: counts until a test is read; a read shows its nominal result; a
// claim is called supported only from the family (Holm) result over all three.
function forwardHTML() {
  const rows = S.index.forward_tests || [];
  if (!rows.length) return "";
  const allRead = rows.every((r) => r.adjusted);
  const status = (r) => {
    if (!r.result) return r.status === "ready to read" ? "Ready to read" : "Collecting events";
    const p = r.result.primary;
    const nominal = `median ${num(p.median_excess * 100)} pts vs SPY over six months; ${p.wins} of ${p.n_sign} non-tied outcomes as claimed (${p.n_valid} valid${p.n_ties ? `, ${p.n_ties} exact ties` : ""}); nominal p = ${p.p_value}`;
    return r.adjusted ? `<b>${r.adjusted.supported ? "Supported" : "Not supported"}</b> after correction for three tests (adjusted p = ${r.adjusted.p_adjusted}). ${nominal}.`
      : `Read ${esc(fmtDate(r.result.read_at))}: ${nominal}. <b>Unadjusted</b>: no conclusion until all three tests are read.`;
  };
  const unusable = rows.filter((r) => Object.keys(r.unusable || {}).length);
  return `<section class="panel" id="forward-tests"><h2>Does it predict anything? Forward tests</h2>
      <p class="small muted">Nothing on this site has yet been shown to predict returns. Each claim below was written down before any outcome existed. The monitor logs an event, with the basket held at that moment, whenever the condition is met in a live update; history rebuilt after the fact never counts. A test is read once, when 20 events have a completed six-month window and a usable outcome. Until then only counts are shown, so progress can be watched without seeing results.</p>
      <div class="table-wrap"><table><thead><tr><th>Claim</th><th>Registered</th><th class="num">Events logged</th><th class="num">Usable six-month outcomes</th><th>Status</th></tr></thead>
      <tbody>${rows.map((r) => `<tr><td>${esc(r.hypothesis)}</td><td>${esc(fmtDate(r.registered))}</td><td class="num">${r.logged}</td><td class="num">${r.usable ?? r.complete} of ${r.needed}</td><td>${status(r)}</td></tr>`).join("")}</tbody></table></div>
      ${unusable.length ? `<p class="small muted">Completed windows without a usable outcome: ${unusable.map((r) => `${esc(r.test)}: ${Object.entries(r.unusable).map(([k, v]) => `${esc(k)} (${v})`).join(", ")}`).join("; ")}.</p>` : ""}
      <p class="small muted">${allRead ? "All three tests are read; “supported” uses Holm's correction across them." : "Three claims are tested, so one result at the 5% level is weaker evidence than it looks. A claim is called supported only after all three are read and corrected together (Holm)."} The test treats events as independent: events sharing half their companies within six months are counted once, but baskets that share fewer companies, a sector or the same market months are still related, and the correction does not remove that.</p></section>`;
}

function method() {
  const U = S.U, st = S.status || {}, a = S.index.data_as_of || {};
  const stages = Object.entries(st.last_attempt || {});
  $("#main").innerHTML = `
    ${pageHead("Methodology and data", "How themes are found, how baskets are measured, what is covered, and how fresh the data is. For what has and has not been validated, see <a href=\"#/research\">Research and validation</a>.")}
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
    <section class="panel prose" id="forward"><h2>Forward tests</h2><p>Three claims were written down before any outcome existed and are being tested on live data only. Their progress and the rules for reading them are on <a href="#/research">Research and validation</a>.</p></section>
    <section class="panel prose" id="coverage"><h2>Coverage</h2>
      <p><b>${esc(U.name)}</b> (version ${esc(U.version)}): ${esc(U.description)}</p>
      <p>${U.n_companies} companies: ${Object.entries(U.sectors || {}).map(([k, v]) => `${esc(k)} (${v})`).join(", ")}. A theme can span several sectors. “Strongest” always means strongest among these companies.</p>
      <p>Companies added to coverage are shown at once but count in growth comparisons only after 12 months of documents and once they were covered at the comparison date, so new coverage never looks like a theme taking off and never changes past basket returns.${(U.recently_added || []).length ? ` Recently added: ${esc(U.recently_added.join(", "))}.` : ""}</p>
      ${(U.changes || []).length ? `<ul>${U.changes.map((c) => `<li>${esc(fmtDate(c.date))}, version ${esc(c.version)}: ${esc(c.note || "")}${c.added?.length ? ` Added ${esc(c.added.join(", "))}.` : ""}${c.removed?.length ? ` Removed ${esc(c.removed.join(", "))}.` : ""}</li>`).join("")}</ul>` : ""}</section>
    <section class="panel" id="freshness"><h2>Freshness and the last update</h2>
      <dl class="dl"><dt>Prices through</dt><dd>${esc(fmtDate(a.prices))}</dd><dt>Calls and filings through</dt><dd>${esc(fmtDate(a.documents))}</dd>
        <dt>Themes processed through</dt><dd>${esc(fmtDate(a.themes))}</dd><dt>This release</dt><dd class="mono">${esc(S.index.release)}</dd>
        ${S.index.completeness?.doc_watermark ? `<dt>Documents last downloaded</dt><dd>${S.index.completeness.doc_watermark.at ? esc(fmtDate(S.index.completeness.doc_watermark.at)) : "no complete download yet"}. A month-end is processed only after a download made at least ${S.index.completeness.settle_days} days after it; this shows a download was made, not that providers had published everything.</dd>` : ""}
        ${(S.index.pending_snapshots || []).map((p) => `<dt>Waiting</dt><dd>${esc(fmtDate(p.t))}: ${esc(p.reason)}.</dd>`).join("")}
        ${S.index.late_documents ? `<dt>Late documents</dt><dd>${S.index.late_documents.total} eligible for month-ends already processed (${Object.entries(S.index.late_documents.by_snapshot).map(([t, v]) => `${esc(fmtDate(t))}: ${v.documents}`).join(", ")}). Not added to those month-ends; used in later ones.</dd>` : ""}
        ${(S.index.price_gaps || []).length ? `<dt>Prices not up to date</dt><dd>${S.index.price_gaps.map((g) => `<span class="mono">${esc(g.symbol)}</span> ${g.last_price ? `last close ${esc(fmtDate(g.last_price))} (${g.sessions_behind} sessions behind)` : esc(g.reason)}`).join("; ")}. The last update's price stage below says what the provider returned.</dd>` : ""}
        <dt>Generated</dt><dd>${esc(S.index.generated_at)}</dd>
        ${S.index.effective ? `<dt>Configuration</dt><dd>${esc(S.index.effective.title)} (profile ${esc(S.index.effective.profile)}), universe version ${esc(S.index.effective.universe_version)}: ${esc(Object.values(S.index.effective.files).flat().map((x) => `${x.path} ${x.sha256.slice(0, 8)}`).join(", "))}</dd>` : ""}
        ${S.index.fork ? `<dt>Started from</dt><dd>a copy of the ${esc(S.index.fork.from_profile)} registry at ${esc(fmtDate(S.index.fork.at_snapshot))} (${S.index.fork.inherited_themes.length} published themes kept with their IDs, baskets and history)</dd>` : ""}<dt>Last update attempt</dt><dd>${esc(st.checked_at || "—")} · ${st.ok ? "succeeded" : "did not complete"}</dd></dl>
      ${stages.length ? `<div class="table-wrap"><table><thead><tr><th>Stage</th><th>Result</th><th>Detail</th></tr></thead><tbody>${stages.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.ok ? "OK" : "<b>Failed</b>"}</td><td class="small">${esc(v.ok ? (typeof v.detail === "string" ? v.detail : "") : v.error)}</td></tr>`).join("")}${Object.entries(st.skipped || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>Skipped</td><td class="small">${esc(v)}</td></tr>`).join("")}${Object.entries(st.warnings || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>Warning</td><td class="small">${esc(v)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <p class="small muted">A release is published only when every stage succeeds; otherwise the previous release stays online and this page says what failed.</p></section>
    <section class="panel prose"><h2>Evaluation status</h2><p>${esc(S.index.evaluation_note || "")}</p>
      <p>This is a research tool, not investment advice. Simulated history shows what a rule would have done; it is not a track record.</p></section>`;
}

// -- companies -------------------------------------------------------------------------
function companiesPage(params) {
  const all = S.index.companies || [];
  const sectors = [...new Set(all.map((c) => c.sector).filter(Boolean))].sort();
  const opts = (list, cur) => list.map(([v, l]) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(l)}</option>`).join("");
  const q0 = { q: params.get("q") || "", sector: params.get("sector") || "", show: params.get("show") || "" };
  const none = all.filter((c) => !c.themes.length).length;
  const mc = S.index.market_clusters;
  const clusterOf = Object.fromEntries((mc?.leaves || []).map((l) => [l.symbol, l.cluster]));
  $("#main").innerHTML = `
    ${pageHead("Companies", `Every monitored company and the active themes it belongs to. ${all.length - none} of ${all.length} are in at least one theme. Select a company for its themes, market links and stated relationships.`)}
    ${notices()}
    <form class="toolbar" role="search" onsubmit="return false">
      <label class="field">Filter<input id="c-q" type="search" placeholder="Company, ticker or theme" value="${esc(q0.q)}"></label>
      <label class="field">Sector<select id="c-sector">${opts([["", "Any sector"], ...sectors.map((x) => [x, x])], q0.sector)}</select></label>
      <label class="field">Show<select id="c-show">${opts([["", "All companies"], ["in", "In a theme"], ["none", "In no theme"], ["many", "In three or more themes"]], q0.show)}</select></label>
    </form>
    <p class="count" id="c-count" aria-live="polite"></p>
    <div id="c-list"></div>
    <p class="note">A filled tag means the company is held in that theme's basket; an outlined tag means it discusses the theme this month but is not held (a change needs two months in a row). The percentage is the company's share of the theme's passages.</p>`;
  const tag = (t) => {
    const row = S.byId[t.id];
    if (!row) return "";
    return `<a class="ctag${t.in_basket ? " held" : ""}" href="#/theme/${esc(t.id)}" title="${esc(row.title)} — ${t.in_basket ? "held in the basket" : "discusses it; not in the basket"}">${esc(row.title)}${t.share_of_theme ? ` <span class="pc">${Math.round(t.share_of_theme * 100)}%</span>` : ""}</a>`;
  };
  const draw = () => {
    const f = { q: $("#c-q").value.trim().toLowerCase(), sector: $("#c-sector").value, show: $("#c-show").value };
    const rows = all.filter((c) => {
      if (f.sector && c.sector !== f.sector) return false;
      if (f.show === "in" && !c.themes.length) return false;
      if (f.show === "none" && c.themes.length) return false;
      if (f.show === "many" && c.themes.length < 3) return false;
      if (!f.q) return true;
      const hay = [c.symbol, nameOf(c.symbol), c.sector, ...c.themes.map((t) => S.byId[t.id]?.title || "")].join(" ").toLowerCase();
      return f.q.split(/\s+/).every((w) => hay.includes(w));
    });
    $("#c-count").textContent = `${rows.length} of ${all.length} companies`;
    $("#c-list").innerHTML = rows.length ? `<section class="panel flush"><div class="table-wrap"><table class="companies dense"><thead><tr><th>Company</th><th>Sector</th><th title="Group of stocks it has traded with, beyond the market">Market cluster</th><th class="num">Held</th><th class="num">Discussing</th><th>Themes</th></tr></thead>
      <tbody>${rows.map((c) => { const cl = clusterOf[c.symbol]; const held = c.themes.filter((t) => t.in_basket).length;
        return `<tr><td>${company(c.symbol)}</td><td>${esc(c.sector || "—")}</td><td>${cl ? `<a href="#/clusters">${esc(clusterName(mc, cl).replace(/^ \(|\)$/g, "") || `Cluster ${cl}`)}</a>` : `<span class="na">—</span>`}</td><td class="num">${held}</td><td class="num">${c.themes.length - held}</td><td>${c.themes.length ? `<div class="ctags">${c.themes.map(tag).join("")}</div>` : `<span class="na">In no active theme</span>`}</td></tr>`; }).join("")}</tbody></table></div></section>`
      : `<p class="empty">No companies match these filters.</p>`;
    const p = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
    history.replaceState(null, "", `#/companies${p.toString() ? "?" + p : ""}`);
  };
  ["c-q", "c-sector", "c-show"].forEach((id) => $("#" + id).addEventListener(id === "c-q" ? "input" : "change", draw));
  draw();
}

// -- one company -------------------------------------------------------------------------
// Everything the feed holds about one company: its themes, the stocks it trades
// with, its macro links and what companies have said about doing business with it.
function companyPage(sym) {
  const c = (S.index.companies || []).find((x) => x.symbol === sym);
  if (!c) {
    $("#main").innerHTML = `${notices()}<p class="notice bad">${esc(sym)} is not a monitored company in this release. <a href="#/companies">Companies</a></p>`;
    return;
  }
  const name = nameOf(sym), b = bench();
  document.title = `${name} · Themegraph`;
  const net = S.index.network, mc = S.index.market_clusters, X = S.index.company_macro;
  const leaf = (mc?.leaves || []).find((l) => l.symbol === sym);
  const cl = leaf?.cluster ? (mc.clusters || []).find((x) => x.id === leaf.cluster) : null;
  const price = (net?.price_links || []).filter((e) => e.a === sym || e.b === sym).map((e) => [e.a === sym ? e.b : e.a, e.corr]).sort((x, y) => y[1] - x[1]);
  const biz = (S.index.business?.links || []).filter((l) => l.a === sym || l.b === sym).sort((x, y) => y.last_said.localeCompare(x.last_said));
  const held = c.themes.filter((t) => t.in_basket);
  const said = S.index.themes.filter((r) => r.quote?.symbol === sym);
  const spans = X?.available ? X.spans.filter((sp) => X.by_span[sp.key]?.available) : [];
  const mrow = (sp) => X.by_span[sp.key].companies.find((x) => x.symbol === sym);
  const anyMacro = spans.some((sp) => mrow(sp));
  const render = () => {
    const rows = c.themes.map((t) => ({ t, r: S.byId[t.id] })).filter((x) => x.r);
    $("#main").innerHTML = `
      <nav class="crumb" aria-label="Breadcrumb"><a href="#/">Overview</a> / <a href="#/companies">Companies</a> / <span class="mono">${esc(sym)}</span></nav>
      ${notices()}
      ${pageHead(name, `<span class="mono">${esc(sym)}</span> · ${esc(c.sector || "No sector")}`, periodControl())}
      <div class="stats">
        ${stat("Themes held in", held.length, "in the theme's basket")}
        ${stat("Discussing only", c.themes.length - held.length, "talks about it; not held")}
        ${stat("Market cluster", cl ? esc(cl.name || `Cluster ${cl.id}`) : "None", cl ? `with ${cl.size - 1} other ${cl.size - 1 === 1 ? "company" : "companies"}` : mc?.available ? "no group above the cut" : "not available", "#/clusters")}
        ${stat("Price links", net ? price.length : "—", net?.price_cut != null ? `correlation beyond ${esc(net.window.index)} above ${net.price_cut.toFixed(2)}` : "")}
        ${S.index.business ? stat("Stated relationships", biz.length, "named by this or another covered company") : ""}
      </div>
      <div class="grid g-main">
        <div class="stackv">
          <section class="panel"><div class="panel-head"><div><h2>Themes</h2><p class="note">Active themes this company is part of. “Share of theme” is its share of the theme's passages; “weight” is its share of the basket's value now.</p></div></div>
            ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Theme</th><th>Status</th><th>Role</th><th class="num">Share of theme</th><th class="num">Weight</th><th class="num">${esc(PERIODS[S.period][0])} basket return</th><th class="num">vs ${esc(b)}</th></tr></thead>
              <tbody>${rows.map(({ t, r }) => { const x = periodReturn(r); return `<tr><td class="t"><a href="#/theme/${esc(r.id)}">${esc(r.title)}</a></td><td><span class="meta">${chip(r.status)}${toneTag(r)}</span></td><td>${t.in_basket ? "Held" : "Discussing"}</td>
                <td class="num">${t.share_of_theme ? share(t.share_of_theme) : `<span class="na">—</span>`}</td><td class="num">${t.in_basket && t.weight_now != null ? share(t.weight_now, 1) : `<span class="na">—</span>`}</td>
                <td class="num">${x.na ? `<span class="na" title="${esc(x.na)}">n/a</span>` : `${pct(x.theme)} ${basisTag(x.basis)}`}</td><td class="num">${x.na ? `<span class="na">—</span>` : pts(x[`excess_vs_${b}`])}</td></tr>`; }).join("")}</tbody></table></div>
              <p class="note" style="margin-top:8px">Returns are the whole basket's, not this company's own.</p>` : `<p class="empty">In no active theme this month.</p>`}</section>
          ${S.index.business ? `<section class="panel"><div class="panel-head"><div><h2>Stated business relationships</h2><p class="note">What covered companies have said about each other in calls and filings over the last ${S.index.business.lookback_months} months, each read from the quoted passage. A statement can be out of date; unnamed counterparties cannot be found.</p></div></div>
            ${biz.length ? `<ul class="plain bizlist">${biz.slice(0, 12).map(bizItem).join("")}</ul>${biz.length > 12 ? `<p class="more"><a href="#/network">All ${biz.length} on Relationships →</a></p>` : ""}` : `<p class="empty">${S.index.business.pairs_read ? "No stated relationship with another covered company has been found." : "Relationships have not been read yet in this release."}</p>`}</section>` : ""}
          ${said.length ? `<section class="panel"><div class="panel-head"><div><h2>Quoted in themes</h2><p class="note">Passages from this company that lead a theme's evidence this month.</p></div></div>
            <div class="quotes">${said.slice(0, 6).map((r) => `<div class="quote ${esc(r.quote.role)}"><div class="src"><span class="tag ${r.quote.role === "analyst_question" ? "q" : ""}">${esc(ROLE[r.quote.role] || r.quote.role || "")}</span><span>${esc(fmtDate(r.quote.date))}</span><a href="#/theme/${esc(r.id)}">${esc(r.title)}</a></div><blockquote>${esc(r.quote.text)}…</blockquote></div>`).join("")}</div></section>` : ""}
        </div>
        <div class="stackv">
          <section class="panel"><div class="panel-head"><div><h2>Trades with</h2><p class="note">${net ? `Stocks whose returns moved with this one beyond ${esc(net.window.index)}, ${esc(fmtDate(net.window.start))} – ${esc(fmtDate(net.window.end))}. Co-movement is not evidence of a business relationship.` : "Not available in this release."}</p></div></div>
            ${cl ? `<p class="small"><b>${esc(cl.name || `Cluster ${cl.id}`)}</b> <span class="muted">(cluster ${cl.id})</span>: <span class="tickers">${cl.members.filter((x) => x !== sym).map((x) => `<a href="${coHref(x)}">${esc(x)}</a>`).join(" · ")}</span></p>` : ""}
            ${price.length ? `<div class="table-wrap"><table class="dense"><thead><tr><th>Company</th><th class="num">Correlation beyond ${esc(net.window.index)}</th></tr></thead><tbody>${price.slice(0, 12).map(([o, v]) => `<tr><td>${company(o)}</td><td class="num">${v.toFixed(2)}</td></tr>`).join("")}</tbody></table></div>${price.length > 12 ? `<p class="note" style="margin-top:6px">${price.length - 12} more on <a href="#/network">Relationships</a>.</p>` : ""}` : net ? `<p class="empty">No price link above the cut.</p>` : ""}</section>
          ${X?.available ? `<section class="panel"><div class="panel-head"><div><h2>Rates, oil, inflation and the dollar</h2><p class="note">How the stock moved over each time frame when a factor rose, beyond ${esc(X.index)} and the other factors. <b>Bold</b> is larger than in 95% of trials with the factor history slid out of step. A description of the past, not a forecast.</p></div></div>
            ${anyMacro ? `<div class="table-wrap"><table class="dense"><thead><tr><th>Factor</th>${spans.map((sp) => `<th class="num"${X.by_span[sp.key].beyond_chance ? "" : ` title="At this time frame the number of companies passing is not distinguishable from chance"`}>${esc(sp.label.replace(" days", "d"))}${X.by_span[sp.key].beyond_chance ? "" : "*"}</th>`).join("")}</tr></thead>
              <tbody>${X.factors.map((fa) => `<tr><td>${esc(fa.label.split(" (")[0])}</td>${spans.map((sp) => { const x = mrow(sp)?.links?.[fa.key]; return `<td class="num">${x ? (x.clear ? `<b>${signed(x.beta)}</b>` : `<span class="muted">${signed(x.beta)}</span>`) : `<span class="na">—</span>`}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
              <p class="note" style="margin-top:6px">${spans.some((sp) => !X.by_span[sp.key].beyond_chance) ? "* At these time frames no more companies pass than chance gives: treat bold values there as noise. " : ""}A dash means too little history. <a href="#/macro">All companies →</a></p>` : `<p class="empty">Not enough price history for this company.</p>`}</section>` : ""}
        </div>
      </div>`;
    bindPeriod($("#main"), render);
  };
  render();
}

// -- business relationships ----------------------------------------------------------------
// What covered companies say about doing business with each other, each backed by a
// quoted passage. Direction is in the wording, never in colour alone.
const BIZ = { supply: "Supplier and customer", partner: "Partners", competitor: "Competitors", ownership: "Ownership" };
function bizSentence(l) {
  const a = esc(nameOf(l.a)), b = esc(nameOf(l.b));
  return { supply: `<b>${a}</b> supplies <b>${b}</b>`, partner: `<b>${a}</b> and <b>${b}</b> work together`,
    competitor: `<b>${a}</b> and <b>${b}</b> compete`, ownership: `<b>${a}</b> owns all or part of <b>${b}</b>` }[l.kind] || `${a} – ${b}`;
}
function bizItem(l) {
  const st = l.statements[0];
  return `<li class="biz"><p>${bizSentence(l)} <span class="small muted">· said by ${l.both_sides ? "both companies" : esc(nameOf(st.said_by))}, last on ${esc(fmtDate(l.last_said))}</span></p>
    ${l.statements.slice(0, 2).map((x) => `<blockquote class="pull"><p>“${esc(x.text)}”</p><footer>${esc(nameOf(x.said_by))} · ${esc(ROLE[x.role] || x.role || "")} · ${esc(fmtDate(x.date))}${x.url ? ` · <a href="${esc(x.url)}" rel="noopener noreferrer" target="_blank">Source</a>` : ""}</footer></blockquote>`).join("")}</li>`;
}
function businessSection() {
  const B = S.index.business;
  if (!B) return "";
  const L = B.links || [];
  const head = `<div><h2 id="h-biz">Who does business with whom</h2>
    <p class="muted small">What the monitored companies have said about each other in their own earnings calls and filings over the last ${B.lookback_months} months: supplier and customer, partners, competitors, ownership. Each link is read from a quoted passage, shown with it. A company that is not named (“a large cloud customer”) cannot be found, a statement can be out of date, and banks and lenders are not covered. ${B.pairs_waiting ? `<b>${B.pairs_waiting} pairs of companies are still waiting to be read</b>, so this list is incomplete.` : ""}</p></div>`;
  if (!L.length) return `<section class="section" aria-labelledby="h-biz">${head}<p class="empty">${B.pairs_read ? "No stated relationship has been found yet." : "Nothing has been read yet. Run “themegraph monitor relations” where the refresh runs."}</p></section>`;
  const opts = (list, cur) => list.map(([v, l]) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(l)}</option>`).join("");
  const kinds = Object.keys(BIZ).filter((k) => L.some((l) => l.kind === k));
  return `<section class="section" aria-labelledby="h-biz">${head}
    <form class="toolbar" onsubmit="return false">
      <label class="field">Kind<select id="b-kind">${opts([["", `All kinds (${L.length})`], ...kinds.map((k) => [k, `${BIZ[k]} (${L.filter((l) => l.kind === k).length})`])], "")}</select></label>
      <label class="field">Company<input id="b-q" type="search" placeholder="Name or ticker"></label>
    </form>
    <p class="count" id="b-count" aria-live="polite"></p><ul class="plain bizlist" id="b-list"></ul></section>`;
}
function bindBusiness() {
  const L = S.index.business?.links || [];
  if (!$("#b-list")) return;
  const draw = () => {
    const kind = $("#b-kind").value, q = $("#b-q").value.trim().toLowerCase();
    const rows = L.filter((l) => (!kind || l.kind === kind) && (!q || [l.a, l.b, nameOf(l.a), nameOf(l.b)].join(" ").toLowerCase().includes(q)))
      .sort((x, y) => y.last_said.localeCompare(x.last_said));
    $("#b-count").textContent = `Showing ${Math.min(rows.length, 60)} of ${rows.length} relationships, most recently stated first`;
    $("#b-list").innerHTML = rows.slice(0, 60).map(bizItem).join("") || `<li class="muted">No relationship matches.</li>`;
  };
  $("#b-kind").addEventListener("change", draw);
  $("#b-q").addEventListener("input", draw);
  draw();
}

// -- network -----------------------------------------------------------------------------
// Companies as dots, two kinds of link: trading together (solid) and discussing the
// same theme (dashed). Positions come with the release (monitor/network.py), so the
// same release always draws the same picture. Selecting a theme, a sector or a
// company highlights it; identity is never carried by colour alone.
function networkPage() {
  const net = S.index.network;
  if (!net || !net.nodes?.length) {
    $("#main").innerHTML = `${pageHead("Relationships", "")}${notices()}<p class="empty">Not available in this release.</p>`;
    return;
  }
  const sectors = [...new Set(net.nodes.map((n) => n.sector).filter(Boolean))].sort();
  const themes = S.index.themes.filter((r) => !["inactive", "merged"].includes(r.status))
    .sort((a, b) => a.title.localeCompare(b.title));
  const opts = (list) => list.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  $("#main").innerHTML = `
    ${pageHead("Relationships", "How the monitored companies connect: by trading together, by talking about the same themes, and by what they say about doing business with each other. The three kinds of link are separate: a price link is not evidence of a business relationship.")}
      <p class="note">${net.price_links.length} price links${net.price_cut != null ? ` (correlation beyond ${esc(net.window.index)} above ${net.price_cut.toFixed(2)}, ${esc(fmtDate(net.window.start))} – ${esc(fmtDate(net.window.end))})` : ""} and ${net.discussion_links.length} discussion links (both companies discuss the same active theme this month). Companies that sit close together are linked, directly or through others; distance has no other meaning.</p>
    ${notices()}
    <section class="panel">
      <form class="controls" onsubmit="return false">
        <label class="check"><input type="checkbox" id="n-price" checked> <i class="key" style="border-top-color:var(--series-1);border-top-width:2px"></i>Trading together</label>
        <label class="check"><input type="checkbox" id="n-disc" checked> <i class="key dash" style="border-top-color:var(--series-2);border-top-width:2px"></i>Discussing the same theme</label>
        <label class="field">Highlight a theme<select id="n-theme">${opts([["", "None"], ...themes.map((t) => [t.id, t.title])])}</select></label>
        <label class="field">Highlight a sector<select id="n-sector">${opts([["", "None"], ...sectors.map((s) => [s, s])])}</select></label>
        ${(S.index.business?.links || []).length ? `<label class="check"><input type="checkbox" id="n-biz" checked> <i class="key dot" style="border-top-color:var(--series-3);border-top-width:3px"></i>Stated business relationship</label>` : ""}
        <label class="check"><input type="checkbox" id="n-labels"> Show every ticker</label>
      </form>
      <div class="net-wrap"><div id="net" class="net"></div><aside id="net-info" class="net-info" aria-live="polite"></aside></div>
      <p class="note" style="margin-top:8px">Select a company to see its links; the <a href="#/companies">Companies</a> page has the same memberships as a table.</p>
    </section>
    ${businessSection()}`;
  bindBusiness();
  const biz = (S.index.business?.links || []).filter((l) => l.a in Object.fromEntries(net.nodes.map((n) => [n.symbol, 1])) && l.b in Object.fromEntries(net.nodes.map((n) => [n.symbol, 1])));
  const by = Object.fromEntries(net.nodes.map((n) => [n.symbol, n]));
  const nb = {}; // symbol -> {price: [[other, corr]], disc: [[other, themes]]}
  net.nodes.forEach((n) => { nb[n.symbol] = { price: [], disc: [], biz: [] }; });
  biz.forEach((l) => { nb[l.a].biz.push(l); nb[l.b].biz.push(l); });
  net.price_links.forEach((e) => { nb[e.a].price.push([e.b, e.corr]); nb[e.b].price.push([e.a, e.corr]); });
  net.discussion_links.forEach((e) => { nb[e.a].disc.push([e.b, e.themes]); nb[e.b].disc.push([e.a, e.themes]); });
  let picked = null;
  const host = $("#net");
  const draw = () => {
    host.textContent = "";
    const W = Math.max(host.clientWidth, 300), small = narrowNet(W);
    const H = Math.round(small ? W * 1.25 : Math.min(Math.max(W * 0.74, 420), Math.max(560, window.innerHeight - 220))), pad = 28;
    const X = (n) => pad + n.x * (W - 2 * pad), Y = (n) => pad + n.y * (H - 2 * pad);
    const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "netsvg", role: "group",
      "aria-label": `Network of ${net.nodes.length} companies. Each company is a focusable point; the Companies page lists the same memberships as a table.` }, host);
    const showP = $("#n-price").checked, showD = $("#n-disc").checked;
    const theme = $("#n-theme").value, sector = $("#n-sector").value, all = $("#n-labels").checked;
    const lit = new Set();
    const showB = $("#n-biz")?.checked;
    if (picked) { lit.add(picked); if (showP) nb[picked].price.forEach(([o]) => lit.add(o)); if (showD) nb[picked].disc.forEach(([o]) => lit.add(o)); if (showB) nb[picked].biz.forEach((l) => { lit.add(l.a); lit.add(l.b); }); }
    net.nodes.forEach((n) => { if ((theme && n.themes.includes(theme)) || (sector && n.sector === sector)) lit.add(n.symbol); });
    const focus = picked || theme || sector;
    const edge = (e, cls) => {
      const a = by[e.a], b = by[e.b];
      const on = picked ? (e.a === picked || e.b === picked) : theme ? (cls === "d" && e.themes.includes(theme)) || (cls === "p" && lit.has(e.a) && lit.has(e.b)) : sector ? lit.has(e.a) && lit.has(e.b) : true;
      svgEl("line", { x1: X(a), y1: Y(a), x2: X(b), y2: Y(b), class: `ne ${cls}${focus ? (on ? " on" : " off") : ""}` }, svg);
    };
    if (showD) net.discussion_links.forEach((e) => edge(e, "d"));
    if (showP) net.price_links.forEach((e) => edge(e, "p"));
    if (showB) biz.forEach((e) => edge({ ...e, themes: [] }, "b"));
    const maxDeg = Math.max(1, ...net.nodes.map((n) => n.degree));
    const labelCut = [...net.nodes].sort((a, b) => b.degree - a.degree)[Math.min(net.nodes.length - 1, narrowNet(W) ? 14 : 34)].degree;
    net.nodes.forEach((n) => {
      const on = !focus || lit.has(n.symbol);
      const g = svgEl("g", { class: `nn${on ? "" : " off"}${n.symbol === picked ? " picked" : ""}`, tabindex: "0", role: "button",
        "aria-label": `${n.symbol}, ${n.sector || "no sector"}, ${n.themes.length} themes, ${nb[n.symbol].price.length} price links` }, svg);
      svgEl("circle", { cx: X(n), cy: Y(n), r: (small ? 3 : 4) + (small ? 3 : 5) * Math.sqrt(n.degree / maxDeg) }, g);
      if (all || (focus ? lit.has(n.symbol) : n.degree >= labelCut)) {
        const t = svgEl("text", { x: X(n), y: Y(n) - 9 - 5 * Math.sqrt(n.degree / maxDeg), "text-anchor": "middle" }, g);
        t.textContent = n.symbol;
      }
      svgEl("title", {}, g).textContent = `${n.symbol}${n.sector ? ` · ${n.sector}` : ""}`;
      const pick = () => { picked = picked === n.symbol ? null : n.symbol; draw(); info(); host.querySelector(".nn.picked")?.focus(); };
      g.addEventListener("click", pick);
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
    });
  };
  const info = () => {
    const box = $("#net-info");
    if (!picked) { box.innerHTML = `<p class="muted small">Select a company to see what it is linked to.</p>`; return; }
    const n = by[picked];
    const price = [...nb[picked].price].sort((a, b) => b[1] - a[1]);
    const disc = [...nb[picked].disc].sort((a, b) => b[1].length - a[1].length);
    box.innerHTML = `<h3>${company(n.symbol)}</h3><p class="small"><a href="${coHref(n.symbol)}">Company page →</a></p><p class="small muted">${esc(n.sector || "")}${n.cluster ? ` · <a href="#/clusters">market cluster ${n.cluster}${clusterName(S.index.market_clusters, n.cluster)}</a>` : ""}</p>
      <h4>Themes (${n.themes.length})</h4>${n.themes.length ? `<ul class="plain">${n.themes.map((id) => `<li><a href="#/theme/${esc(id)}">${esc(S.byId[id]?.title || id)}</a></li>`).join("")}</ul>` : `<p class="small muted">In no active theme.</p>`}
      ${nb[picked].biz.length ? `<h4>Does business with (${nb[picked].biz.length})</h4><ul class="plain small">${nb[picked].biz.slice(0, 12).map((l) => `<li>${bizSentence(l)} <span class="muted">(${esc(fmtDate(l.last_said))})</span></li>`).join("")}</ul>` : ""}
      <h4>Trades with (${price.length})</h4>${price.length ? `<p class="small">${price.slice(0, 12).map(([o, c]) => `<span class="mono">${esc(o)}</span> ${c.toFixed(2)}`).join(" · ")}${price.length > 12 ? ` · +${price.length - 12}` : ""}</p>` : `<p class="small muted">No price link above the cut.</p>`}
      <h4>Discusses the same themes as (${disc.length})</h4>${disc.length ? `<p class="small">${disc.slice(0, 16).map(([o, th]) => `<span class="mono">${esc(o)}</span>${th.length > 1 ? ` ×${th.length}` : ""}`).join(" · ")}${disc.length > 16 ? ` · +${disc.length - 16}` : ""}</p>` : `<p class="small muted">None this month.</p>`}`;
  };
  ["n-price", "n-disc", "n-biz", "n-theme", "n-sector", "n-labels"].forEach((id) => $("#" + id)?.addEventListener("change", () => { if (id === "n-theme" || id === "n-sector") picked = null; draw(); info(); }));
  let w = host.clientWidth;
  window.addEventListener("resize", () => { if ($("#net") === host && host.clientWidth !== w) { w = host.clientWidth; draw(); } });
  draw(); info();
}
const narrowNet = (w) => w < 560;

// -- market clusters ---------------------------------------------------------------------
const SVGNS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

// Horizontal dendrogram: one row per company, groups join further right the lower
// the correlation at which they join. Branches joined above the cut are drawn in
// ink; the rest are muted. Clusters are told apart by labelled bands, not colour.
function drawDendrogram(host, mc) {
  host.textContent = "";
  const n = mc.pool.length, rowH = 18, top = 30, bottom = 46;
  const narrow = host.clientWidth < 560;
  const W = Math.max(host.clientWidth, 320), H = top + n * rowH + bottom;
  const labW = narrow ? 64 : 120, bandW = 30, right = 16;
  const corrs = mc.merges.map((m) => m[2]);
  const hi = Math.min(1, Math.ceil((Math.max(...corrs) + 0.05) * 10) / 10);
  const lo = Math.floor(Math.min(...corrs, mc.cut.corr) * 10) / 10;
  const x0 = bandW + labW, x1 = W - right;
  const X = (c) => x0 + ((hi - c) / (hi - lo)) * (x1 - x0);
  const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "dendro", role: "img",
    "aria-label": `Tree of ${n} companies by correlation beyond ${mc.index}; ${mc.clusters.length} clusters joined above ${mc.cut.corr.toFixed(2)}. The cluster list below has the same information as text.` }, host);
  const row = Object.fromEntries(mc.leaves.map((l, i) => [l.symbol, i]));
  const yOf = (i) => top + i * rowH + rowH / 2;
  // cluster bands and labels
  mc.clusters.forEach((c) => {
    const rows = c.members.map((s) => row[s]), a = Math.min(...rows), b = Math.max(...rows);
    svgEl("rect", { x: 0, y: top + a * rowH, width: W, height: (b - a + 1) * rowH, class: c.id % 2 ? "band" : "band alt" }, svg);
    const t = svgEl("text", { x: 4, y: yOf(a) + 4, class: "band-label" }, svg);
    t.textContent = `C${c.id}`;
    svgEl("title", {}, t).textContent = `Cluster ${c.id}${c.name ? `: ${c.name}` : ""}`;
  });
  // axis
  const ax = svgEl("g", { class: "axis" }, svg);
  for (let v = hi; v >= lo - 1e-9; v -= hi - lo > 0.6 ? 0.2 : 0.1) {
    svgEl("line", { x1: X(v), x2: X(v), y1: top - 4, y2: H - bottom, class: "grid" }, ax);
    const t = svgEl("text", { x: X(v), y: top - 10, "text-anchor": "middle" }, ax);
    t.textContent = v.toFixed(1);
  }
  const at = svgEl("text", { x: x1, y: H - 8, "text-anchor": "end", class: "axis-title" }, ax);
  at.textContent = `Correlation beyond ${mc.index} at which groups join →`;
  // branches
  const pos = mc.pool.map((s) => ({ x: x0, y: yOf(row[s]) }));
  mc.merges.forEach(([a, b, c, size]) => {
    const A = pos[a], B = pos[b], x = X(c), inside = c >= mc.cut.corr;
    const g = svgEl("path", { d: `M${A.x},${A.y}H${x}V${B.y}H${B.x}`, class: inside ? "br in" : "br" }, svg);
    svgEl("title", {}, g).textContent = `${size} companies joined at correlation ${c.toFixed(2)}`;
    pos.push({ x, y: (A.y + B.y) / 2 });
  });
  // cut line
  svgEl("line", { x1: X(mc.cut.corr), x2: X(mc.cut.corr), y1: top - 4, y2: H - bottom + 4, class: "cut" }, svg);
  const ct = svgEl("text", { x: X(mc.cut.corr) + 4, y: H - bottom + 16, class: "cut-label" }, svg);
  ct.textContent = `cut ${mc.cut.corr.toFixed(2)}`;
  // leaf labels
  mc.leaves.forEach((l, i) => {
    const t = svgEl("text", { x: bandW, y: yOf(i) + 4, class: l.cluster ? "leaf in" : "leaf" }, svg);
    t.textContent = l.symbol;
    svgEl("title", {}, t).textContent = `${l.symbol}${l.sector ? `, ${l.sector}` : ""}${l.cluster ? `, cluster ${l.cluster}${clusterName(mc, l.cluster)}` : ", not in a cluster"}`;
  });
}

function clusterName(mc, id) {
  const c = (mc?.clusters || []).find((x) => x.id === id);
  return c?.name ? ` (${c.name})` : "";
}

// Companies against the macro factors: each company measured on its own, the ones
// that trade against several factors at once, and clusters of similar companies.
function companyMacroInner() {
  const X = S.index.company_macro;
  const short = (f) => f.label.split(" (")[0];
  const avail = X.spans.filter((sp) => X.by_span[sp.key]?.available);
  if (!avail.some((sp) => sp.key === S.macroSpan)) S.macroSpan = avail[0].key;
  const V = X.by_span[S.macroSpan], label = X.spans.find((sp) => sp.key === S.macroSpan).label;
  const FX = X.factors.filter((f) => !V.factors || V.factors.includes(f.key));
  const rows = V.companies.filter((c) => !S.macroMulti || c.n_clear >= 2);
  const cname = Object.fromEntries(V.clusters.map((c) => [c.id, c.name]));
  const chance = `With the factor history slid out of step with returns, ${V.null.mean} companies pass on average and ${Math.round(V.null.p95)} or fewer in 95% of ${V.null.offsets} trials.`;
  return `
    <div class="controls" role="group" aria-label="Time frame"><span class="small muted">Time frame</span><div class="seg">${X.spans.map((sp) => { const ok = X.by_span[sp.key]?.available; return `<button type="button" data-mspan="${sp.key}" aria-pressed="${sp.key === S.macroSpan}"${ok ? "" : ` disabled title="${esc(X.by_span[sp.key]?.note || "Not available")}"`}>${esc(sp.label)}</button>`; }).join("")}</div></div>
    <p class="small muted">Spans of ${esc(label)}, ${V.months} months of history (${esc(V.start)} to ${esc(V.end)}), ${V.n_companies} companies${V.left_out ? `; ${V.left_out} without the full history are left out` : ""}.${V.factors_left_out?.length ? ` <b>Left out at this time frame, for lack of history:</b> ${esc(V.factors_left_out.join("; "))}.` : ""}</p>
    ${V.beyond_chance ? `<p class="small"><b>${V.sensitive} of ${V.n_companies} companies trade against the factors, more than chance gives.</b> ${chance} ${V.multi} have clear links to two or more factors at once.</p>`
      : `<p class="notice"><span><b>Not distinguishable from chance at this time frame.</b> ${V.sensitive} of ${V.n_companies} companies pass. ${chance} Treat the clusters and bold values below as noise until this changes.</span></p>`}
    <h3>Clusters of companies with similar sensitivities</h3>
    ${V.clusters.length ? `<div class="cluster-list">${V.clusters.map((c) => `<article class="card cluster">
      <div class="card-top"><h3>${esc(c.name)}</h3><span class="small muted">${c.size} companies</span></div>
      <p class="tickers">${c.members.map(esc).join(" · ")}</p>
      <p class="small">${Object.entries(c.sectors).map(([k, v]) => `${esc(k)} ${v}`).join(", ")}</p>
      <p class="small muted">Average sensitivity: ${FX.map((f) => `${esc(lowerFactor(short(f)))} ${signed(c.betas[f.key])}`).join(", ")}</p>
    </article>`).join("")}</div>` : `<p class="empty">No two macro-sensitive companies are alike enough to form a cluster at this time frame.</p>`}
    <h3>Every company against each factor</h3>
    <p class="small muted">+ means the stock tended to rise over a span in which the factor rose, − that it fell, each holding the market and the other factors fixed. <b>Bold</b> is a clear link. "Clear links" counts them: two or more means the company has traded against several factors at once. Companies the factors clearly help explain are listed first.</p>
    <label class="check small"><input type="checkbox" id="mc-multi"${S.macroMulti ? " checked" : ""}> Only companies with clear links to two or more factors (${V.multi})</label>
    <div class="table-wrap"><table class="dense"><thead><tr><th>Company</th><th>Sector</th>${FX.map((f) => `<th class="num">${esc(short(f))}</th>`).join("")}<th class="num">Clear links</th><th>Cluster</th></tr></thead>
    <tbody>${rows.map((c) => `<tr><td>${company(c.symbol)}</td><td>${esc(c.sector || "")}</td>${FX.map((f) => { const x = c.links[f.key]; return `<td class="num">${x.clear ? `<b>${signed(x.beta)}</b>` : `<span class="muted">${signed(x.beta)}</span>`}</td>`; }).join("")}<td class="num">${c.n_clear}</td><td class="small">${c.cluster ? esc(cname[c.cluster]) : c.sensitive ? `<span class="muted">Sensitive, no cluster</span>` : `<span class="muted">—</span>`}</td></tr>`).join("") || `<tr><td colspan="${FX.length + 4}" class="muted">No company has clear links to two or more factors at this time frame.</td></tr>`}</tbody></table></div>
    <p class="small muted">Values are in the stock's usual moves over the span per usual move of the factor. A link is clear when it is larger than in 95% of trials with the factor history slid out of step; with five factors per company, some bold values appear by chance. Today's companies are used throughout, and this describes the past: it is not a forecast.</p>`;
}
function macroClustersHTML() {
  const X = S.index.company_macro;
  if (!X) return "";
  const head = `<h2 id="h-mcl">Companies against the macro factors</h2>`;
  if (!X.available) return `<section class="section" aria-labelledby="h-mcl">${head}<p class="empty">${esc(X.note || "Not available.")}</p></section>`;
  return `<section class="section" aria-labelledby="h-mcl"><div>${head}
    <p class="muted small">Each company measured on its own against changes in ${X.factors.map((f) => esc(lowerFactor(f.label.split(" (")[0]))).join(", ")}, beyond ${esc(X.index)}. This is about how a stock trades against the factors, not against other stocks. Companies the factors clearly help explain are then grouped when their factor-driven moves are alike (hierarchical clustering, average linkage, cut at ${X.min_corr}).</p></div>
    <div id="mc-host">${companyMacroInner()}</div></section>`;
}
function bindCompanyMacro() {
  const host = $("#mc-host");
  if (!host) return;
  host.querySelectorAll("[data-mspan]").forEach((b) => b.addEventListener("click", () => { S.macroSpan = b.dataset.mspan; host.innerHTML = companyMacroInner(); bindCompanyMacro(); host.querySelector(`[data-mspan="${S.macroSpan}"]`)?.focus(); }));
  $("#mc-multi")?.addEventListener("change", (e) => { S.macroMulti = e.target.checked; host.innerHTML = companyMacroInner(); bindCompanyMacro(); $("#mc-multi")?.focus(); });
}

function clustersPage() {
  const mc = S.index.market_clusters;
  const names = S.index.benchmark_labels || {};
  if (!mc || !mc.available) {
    $("#main").innerHTML = `${pageHead("Market structure", "")}${notices()}<p class="empty">${esc(mc?.note || "Not available in this release.")}</p>`;
    return;
  }
  const idx = `${mc.index}${names[mc.index] ? ` (${names[mc.index]})` : ""}`;
  const loose = mc.leaves.filter((l) => !l.cluster).map((l) => l.symbol);
  $("#main").innerHTML = `
    ${pageHead("Market structure", `Which monitored stocks have been trading together, beyond what ${esc(idx)} explains, over the last ${mc.window_sessions} sessions (${esc(fmtDate(mc.start))} – ${esc(fmtDate(mc.end))}). Prices only: a different question from the themes, which come from what companies say.`)}
    ${notices()}
    <div class="stats">${stat("Clusters", mc.clusters.length, `among ${mc.companies} companies`)}${stat("In a cluster", mc.companies - loose.length, `${loose.length} in none`)}${stat("Cut", mc.cut.corr.toFixed(2), `correlation beyond ${esc(mc.index)}; set from shuffled returns`)}${stat("Shrinkage", mc.shrinkage.toFixed(2), "towards a single-index model")}</div>
    <section class="section" aria-labelledby="h-cl"><div class="panel-head"><div><h2 id="h-cl">Clusters</h2>
      <p class="note">Groups of stocks whose returns moved together more than chance allows. Each is named after the sector most of its companies are in: a description of who is in it, not a reason the stocks move together.</p></div></div>
      <details><summary>How clusters are found</summary><div class="body prose small"><p>Hierarchical clustering (average linkage) of each pair's correlation beyond ${esc(mc.index)}. The tree is cut at a correlation of ${mc.cut.corr.toFixed(2)}: shuffling each company's returns in time removes any co-movement, and in ${Math.round((1 - mc.cut.alpha) * 100)}% of ${mc.cut.draws} shuffles no pair of companies reached that correlation. A cluster only joins companies whose average correlation beats that. No number of clusters is chosen in advance. Each cluster is named after the sector most of its companies are in (or its two largest sectors): a description of who is in it, not a reason the stocks move together.${S.index.macro?.available ? ` The "Macro" line on each cluster says how it moved when rates, oil, inflation or the dollar rose, beyond ${esc(mc.index)}, and over which time frames (30 to 360 days); only clear links are named.` : ""} ${loose.length} companies are in no cluster.</p></div></details>
      <div class="cluster-list">${mc.clusters.map((c) => `<article class="card cluster" id="c${c.id}">
        <div class="card-top"><h3>${esc(c.name || `Cluster ${c.id}`)}</h3><span class="small muted">Cluster ${c.id} · ${c.size} companies</span></div>
        <p class="tickers">${c.members.map((x) => `<a href="${coHref(x)}" title="${esc(nameOf(x))}">${esc(x)}</a>`).join(" · ")}</p>
        <p class="small">${Object.entries(c.sectors).map(([k, v]) => `${esc(k)} ${v}`).join(", ")}</p>
        <p class="small muted">Average correlation beyond ${esc(mc.index)}: ${c.avg_corr.toFixed(2)} (${c.avg_corr_shrunk.toFixed(2)} after shrinkage ${mc.shrinkage.toFixed(2)})</p>
        ${macroLine(S.index.macro?.clusters?.[String(c.id)])}
        ${c.themes.length ? `<p class="small">In theme baskets: ${c.themes.map((t) => `<a href="#/theme/${esc(t.id)}">${esc(S.byId[t.id]?.title || t.id)}</a> (${t.shared})`).join(", ")}</p>` : `<p class="small muted">No theme basket holds two or more of these companies.</p>`}
      </article>`).join("")}</div></section>
    ${S.index.macro?.available || S.index.company_macro ? `<p class="note">How companies and clusters moved with rates, oil, inflation and the dollar is on <a href="#/macro">Macro</a>.</p>` : ""}
    <section class="panel" aria-labelledby="h-tree"><h2 id="h-tree">The tree (price clusters)</h2>
      <p class="small muted">Each row is a company. Lines join companies, then groups, at the correlation shown on the axis; joins to the left of the dashed cut are clusters (labelled bands). Hover a join or a ticker for details.</p>
      <div id="dendro" class="dendro-wrap"></div></section>
    ${loose.length ? `<section class="section"><h2>Not in a cluster</h2><p class="tickers">${loose.map((x) => `<a href="${coHref(x)}" title="${esc(nameOf(x))}">${esc(x)}</a>`).join(" · ")}</p></section>` : ""}`;
  const host = $("#dendro");
  drawDendrogram(host, mc);
  let w = host.clientWidth;
  const onResize = () => { if ($("#dendro") === host && host.clientWidth !== w) { w = host.clientWidth; drawDendrogram(host, mc); } };
  window.addEventListener("resize", onResize);
}

// -- macro ---------------------------------------------------------------------------------
function macroPage() {
  const M = S.index.macro, X = S.index.company_macro;
  if (!M?.available && !X) {
    $("#main").innerHTML = `${pageHead("Macro", "")}${notices()}<p class="empty">Not available in this release.</p>`;
    return;
  }
  $("#main").innerHTML = `
    ${pageHead("Macro", "How the monitored stocks have moved with changes in interest rates, oil, inflation and the dollar, beyond the market. Prices only. These are descriptions of the past with a stated chance comparison; none is a forecast.")}
    ${notices()}
    ${factorMovesHTML()}
    ${macroClustersHTML()}
    <p class="note">Each theme page shows the same measure for its basket, and <a href="#/clusters">Market structure</a> shows it for each price cluster.</p>`;
  bindCompanyMacro();
}

// -- research and validation -------------------------------------------------------------
// What the system measures, how each measure is checked, and what is not established.
// Every count on this page is read from the release; the wording never goes beyond it.
function researchPage() {
  const T = S.index.themes, U = S.U, a = S.index.data_as_of || {}, st = S.status || {};
  const valued = T.filter((r) => r.market?.status === "ok");
  const byBasis = (k) => valued.filter((r) => r.basis === k).length;
  const days = (x, y) => (x && y ? Math.round((Date.parse(String(y).slice(0, 10)) - Date.parse(String(x).slice(0, 10))) / 864e5) : null);
  const liveDays = days(S.index.go_live, a.prices);
  const F = S.index.forward_tests || [];
  const ft = (name) => { const r = F.find((x) => x.test === name); const label = name.replace(/^./, (c) => c.toUpperCase());
    return r ? (r.adjusted ? `${label} test read: ${r.adjusted.supported ? "supported" : "not supported"}` : `${label} test running: ${r.usable ?? r.complete} of ${r.needed} outcomes`) : "No forward test"; };
  const pill = (cls, text) => `<span class="status-pill ${cls}">${esc(text)}</span>`;
  const sectors = Object.entries(U.sectors || {}), maxSec = Math.max(1, ...sectors.map(([, v]) => v));
  const gaps = S.index.price_gaps || [], pend = S.index.pending_snapshots || [];
  const rows = [
    ["Themes", "Passages from different companies that discuss the same subject, grouped by meaning.", "Every theme page quotes its passages with sources. Titles and summaries are written by a language model from cited passages and can be wrong.", pill("", "Descriptive")],
    ["Discussion status and emergence", "Whether a theme's share of comparable companies' text is growing, steady or shrinking.", "Rules and thresholds were fixed from the text, not from returns. One large month can move the status twice.", pill("part", ft("emergence"))],
    ["Headwind and tailwind", "Whether problem or growth wording is far above its usual level.", "A fixed list of phrases. It does not tell who benefits from a development and who is hurt by it.", `${pill("part", ft("headwind"))} ${pill("part", ft("tailwind"))}`],
    ["Basket returns", "An equal-weight, unrebalanced basket of each theme's companies against benchmarks.", "Arithmetic is covered by automated tests on synthetic data. Simulated and live periods are always labelled.", pill("no", "Past returns only")],
    ["Co-movement", "Whether a basket's stocks moved together beyond the market.", "Compared with random baskets of the same size and segment mix.", pill("no", "Not tested as a predictor")],
    ["Market clusters", "Groups of stocks that traded together beyond the market.", "The cut is set from returns shuffled in time; no number of clusters is chosen.", pill("no", "Not tested as a predictor")],
    ["Macro sensitivity", "How stocks moved when rates, oil, inflation or the dollar changed.", "Compared with the factor history slid out of step with returns; with many factors and time frames some links appear by chance.", pill("no", "Not tested as a predictor")],
    ["Business relationships", "Supplier, customer, partner, competitor and ownership links companies state about each other.", "Read by a language model from a quoted passage shown beside each link. Not independently adjudicated.", pill("", "Descriptive")],
  ];
  $("#main").innerHTML = `
    ${pageHead("Research and validation", "What this system measures, how each measure is checked, and what has not been established. Read this before relying on anything else here.")}
    ${notices(true)}
    <div class="notice warn" role="note"><span class="icon" aria-hidden="true">!</span><div><b>Nothing on this site has been shown to predict returns.</b> It is a monitoring and research tool. The only evidence that will count is the forward tests below, which use live data only and have not yet been read.</div></div>
    <div class="stats">
      ${stat("Live since", esc(fmtDate(S.index.go_live)), liveDays == null ? "" : `${liveDays} days of observed prices`)}
      ${stat("Baskets valued", valued.length, `of ${T.length} published themes`)}
      ${stat("Simulated only", byBasis("reconstructed"), "no day observed after publication")}
      ${stat("Simulated and live", byBasis("mixed"), "history rebuilt, then observed")}
      ${stat("Live only", byBasis("live"), "published and bought after go-live")}
      ${stat("Forward tests read", `${F.filter((r) => r.result).length}<span class="muted" style="font-size:.6em;font-weight:500"> of ${F.length}</span>`, "each needs 20 completed outcomes")}
    </div>
    <section class="panel"><div class="panel-head"><div><h2>What is measured, and how far each measure can be trusted</h2>
      <p class="note">The measures are kept apart and never combined into a single score.</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>Measure</th><th>What it describes</th><th>How it is checked</th><th>Evidence of predictive value</th></tr></thead>
      <tbody>${rows.map(([k, w, h, p]) => `<tr><td><b>${k}</b></td><td>${w}</td><td>${h}</td><td>${p}</td></tr>`).join("")}</tbody></table></div></section>
    ${forwardHTML() || `<section class="panel"><h2>Forward tests</h2><p class="empty">No forward test is registered in this release.</p></section>`}
    <div class="grid g-2">
      <section class="panel"><div class="panel-head"><h2>Simulated and live results</h2></div><div class="prose">
        <p>The monitor went live on <b>${esc(fmtDate(S.index.go_live))}</b>. Everything before a theme's publication is a reconstruction of what the published rules would have held, built afterwards from history. Only days after publication, and after each basket version was decided, are observed.</p>
        <p>Returns are reported separately for the two and tagged <span class="tag sim">Simulated</span> or <span class="tag live">Live</span> wherever they appear. Simulated history is not a track record.</p>
        ${S.index.evaluation_note ? `<p>${esc(S.index.evaluation_note)}</p>` : ""}</div></section>
      <section class="panel"><div class="panel-head"><h2>Survivorship in the simulated history</h2></div><div class="prose">
        <p><b>The history is rebuilt with the companies covered today</b> (${U.n_companies ?? "—"} companies, list version ${esc(U.version || "—")}). The list was chosen with hindsight: it holds companies that turned out to matter, and leaves out most that were acquired, failed or faded before it was drawn up.</p>
        <p>This flatters simulated returns and makes past themes look broader than they would have at the time. It does not affect live results. It cannot be removed without a list of which companies would have been covered at each date, with prices and documents for those no longer listed; that data is not in the system.</p></div></section>
    </div>
    <section class="panel"><div class="panel-head"><h2>Known limitations</h2></div>
      <ul class="limits">
        <li><b>Coverage is a chosen list.</b> “Strongest”, “emerging” and every share of text mean among the ${U.n_companies ?? ""} monitored companies, not the market.</li>
        <li><b>Benchmarks are capitalisation-weighted; baskets are equal-weighted and not rebalanced.</b> Part of any difference reflects company size. No trading costs, taxes or fees are deducted.</li>
        <li><b>Status can swing on one month.</b> It compares three-month averages, so an unusually large month changes it when it arrives and again when it leaves the comparison.</li>
        <li><b>Headwind and tailwind come from a phrase list.</b> A shortage can help a supplier and hurt its customer; the measure does not separate them.</li>
        <li><b>Shares listed in Seoul, Tokyo and Taipei close hours before the US market.</b> Co-movement and cluster measures use same-day returns without adjusting for this, so those companies can look more connected to each other than they are.</li>
        <li><b>Model-written text.</b> Titles, summaries and business relationships are written or read by a language model from quoted passages. The passages are shown so they can be checked; the wording has not been independently reviewed.</li>
        <li><b>Themes overlap.</b> Baskets share companies, sectors and market conditions, so results across themes are not independent observations.</li>
      </ul></section>
    <div class="grid g-2">
      <section class="panel" id="coverage"><div class="panel-head"><h2>Coverage</h2><a class="more" href="#/method#coverage">Details →</a></div>
        <div class="table-wrap"><table class="dense"><thead><tr><th>Sector</th><th class="num">Companies</th><th></th></tr></thead>
        <tbody>${sectors.sort((x, y) => y[1] - x[1]).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v}</td><td style="width:40%"><div class="bar" aria-hidden="true"><i style="width:${(100 * v / maxSec).toFixed(1)}%"></i></div></td></tr>`).join("")}</tbody></table></div>
        ${(U.changes || []).length ? `<h4>Changes to the list</h4><ul class="plain small">${U.changes.map((c) => `<li>${esc(fmtDate(c.date))}, version ${esc(c.version)}: ${esc(c.note || "")}${c.added?.length ? ` <span class="muted">(${c.added.length} added)</span>` : ""}</li>`).join("")}</ul>` : ""}
        ${(U.recently_added || []).length ? `<p class="note" style="margin-top:8px">Recently added, not yet counted in growth comparisons: ${esc(U.recently_added.join(", "))}.</p>` : ""}</section>
      <section class="panel" id="freshness"><div class="panel-head"><h2>Freshness</h2><a class="more" href="#/method#freshness">Details →</a></div>
        <dl class="dl"><dt>Prices through</dt><dd>${esc(fmtDate(a.prices))}${st.stale?.prices ? " · <b>out of date</b>" : ""}</dd>
          <dt>Calls and filings through</dt><dd>${esc(fmtDate(a.documents))}${st.stale?.documents ? " · <b>out of date</b>" : ""}</dd>
          <dt>Themes processed through</dt><dd>${esc(fmtDate(a.themes))}</dd>
          <dt>Prices not current</dt><dd>${gaps.length ? `${gaps.length}: ${esc(gaps.slice(0, 10).map((g) => g.symbol).join(", "))}${gaps.length > 10 ? "…" : ""}` : "None"}</dd>
          <dt>Month-ends waiting</dt><dd>${pend.length ? pend.map((p) => `${esc(fmtDate(p.t))}: ${esc(p.reason)}`).join("; ") : "None"}</dd>
          <dt>Late documents</dt><dd>${S.index.late_documents?.total ?? 0}</dd>
          <dt>Last update</dt><dd>${esc(st.checked_at ? fmtDate(st.checked_at) : "—")} · ${st.ok ? "succeeded" : st.ok === false ? "<b>did not complete</b>" : "unknown"}</dd>
          <dt>Release</dt><dd class="mono">${esc(S.index.release)}</dd></dl></section>
    </div>`;
}

// In-page jumps (the URL hash is the route, so sections are reached by scrolling).
document.addEventListener("click", (e) => {
  const b = e.target.closest?.("[data-jump]");
  if (!b) return;
  const el = document.getElementById(b.dataset.jump);
  if (!el) return;
  el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  el.setAttribute("tabindex", "-1");
  el.focus({ preventScroll: true });
});

// -- shell ---------------------------------------------------------------------------
function setNav(name) {
  document.querySelectorAll(".nav a").forEach((a) => { if (a.dataset.nav === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
}

function menu(open) {
  const side = $("#side"), on = open ?? !side.classList.contains("open");
  side.classList.toggle("open", on);
  $("#scrim").hidden = !on;
  $("#menu").setAttribute("aria-expanded", String(on));
}

// Data state, always in view: the dates everything on the page is as of.
function shellStatus() {
  const a = S.index.data_as_of || {}, st = S.status;
  const failed = st && (Object.values(st.errors || {}).some(Boolean) || Object.keys(st.skipped || {}).length);
  const stale = Object.values(st?.stale || {}).some(Boolean) || (S.index.price_gaps || []).length;
  $("#side-status").innerHTML = `<a class="state" href="#/research#freshness" style="color:inherit"><span class="dot ${failed ? "bad" : stale ? "warn" : ""}"></span>${failed ? "Last update failed" : stale ? "Some data out of date" : "Data current"}</a>
    <dl><dt>Prices</dt><dd>${esc(fmtDate(a.prices))}</dd><dt>Documents</dt><dd>${esc(fmtDate(a.documents))}</dd><dt>Themes</dt><dd>${esc(fmtDate(a.themes))}</dd></dl>`;
  $("#asof").innerHTML = `${S.index.fixture ? `<span class="badge" title="Synthetic companies, documents and prices">Demo data</span>` : ""}Prices <b>${esc(fmtDate(a.prices))}</b> · Documents <b>${esc(fmtDate(a.documents))}</b>`;
}

// Global search over theme titles and company names and tickers.
function bindSearch() {
  const inp = $("#gs"), box = $("#gs-results");
  let sel = -1;
  const links = () => [...box.querySelectorAll("a")];
  const close = () => { box.hidden = true; inp.setAttribute("aria-expanded", "false"); sel = -1; };
  const mark = () => links().forEach((a, i) => a.setAttribute("aria-selected", String(i === sel)));
  const draw = () => {
    const q = inp.value.trim().toLowerCase();
    if (!q || !S.index) { close(); return; }
    const w = q.split(/\s+/);
    const quiet = (r) => (["inactive", "merged"].includes(r.status) ? 1 : 0);
    const co = (S.index.companies || []).filter((c) => w.every((x) => `${c.symbol} ${nameOf(c.symbol)}`.toLowerCase().includes(x)))
      .sort((x, y) => (x.symbol.toLowerCase() === q ? -1 : 0) - (y.symbol.toLowerCase() === q ? -1 : 0)).slice(0, 7);
    const th = S.index.themes.filter((r) => w.every((x) => `${r.title} ${r.name} ${r.id}`.toLowerCase().includes(x)))
      .sort((x, y) => quiet(x) - quiet(y)).slice(0, 9);
    box.innerHTML = (co.length ? `<div class="grp">Companies</div>${co.map((c) => `<a role="option" href="${coHref(c.symbol)}"><span>${esc(nameOf(c.symbol))} <span class="tk">${esc(c.symbol)}</span></span><span>${esc(c.sector || "")}</span></a>`).join("")}` : "")
      + (th.length ? `<div class="grp">Themes</div>${th.map((r) => `<a role="option" href="#/theme/${esc(r.id)}"><span>${esc(r.title)}</span><span>${esc(STATUS[r.status] || r.status)}</span></a>`).join("")}` : "")
      || `<div class="none">Nothing matches “${esc(inp.value.trim())}”.</div>`;
    box.hidden = false; inp.setAttribute("aria-expanded", "true"); sel = -1;
  };
  inp.addEventListener("input", draw);
  inp.addEventListener("focus", draw);
  inp.addEventListener("keydown", (e) => {
    const L = links();
    if (e.key === "ArrowDown" && L.length) { e.preventDefault(); sel = (sel + 1) % L.length; mark(); L[sel].scrollIntoView({ block: "nearest" }); }
    else if (e.key === "ArrowUp" && L.length) { e.preventDefault(); sel = (sel - 1 + L.length) % L.length; mark(); L[sel].scrollIntoView({ block: "nearest" }); }
    else if (e.key === "Enter" && L.length) { e.preventDefault(); location.hash = (L[Math.max(sel, 0)]).getAttribute("href"); inp.value = ""; close(); inp.blur(); }
    else if (e.key === "Escape") { inp.value = ""; close(); inp.blur(); }
  });
  box.addEventListener("click", (e) => { if (e.target.closest("a")) { inp.value = ""; close(); } });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) close(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || "")) { e.preventDefault(); inp.focus(); }
  });
}

async function route() {
  const token = ++S.nav;
  S.chart?.destroy(); S.chart = null;
  S.disc?.destroy(); S.disc = null;
  menu(false);
  const h = location.hash || "#/";
  const [full, query] = h.slice(1).split("?");
  const [path, anchor] = full.split("#");
  const params = new URLSearchParams(query || "");
  let m = path.match(/^\/theme\/(.+)$/);
  if (m) { setNav("themes"); await themePage(decodeURIComponent(m[1]), token); return; }
  document.title = "Themegraph";
  if ((m = path.match(/^\/company\/(.+)$/))) { setNav("companies"); companyPage(decodeURIComponent(m[1])); }
  else if (path === "/themes") { setNav("themes"); allThemes(params); }
  else if (path === "/method") { setNav("method"); method(); }
  else if (path === "/research") { setNav("research"); researchPage(); }
  else if (path === "/macro") { setNav("macro"); macroPage(); }
  else if (path === "/clusters") { setNav("clusters"); clustersPage(); }
  else if (path === "/network") { setNav("network"); networkPage(); }
  else if (path === "/companies") { setNav("companies"); companiesPage(params); }
  else { setNav("overview"); overview(); }
  if (token !== S.nav) return;
  const target = anchor && document.getElementById(anchor);
  if (target) target.scrollIntoView({ block: "start" }); else window.scrollTo(0, 0);
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
  $("#menu").addEventListener("click", () => menu());
  $("#scrim").addEventListener("click", () => menu(false));
  try {
    S.status = await getJSON("data/status.json").catch(() => null);
    const latest = await getJSON("data/latest.json");
    S.base = "data/" + latest.path;
    S.index = await getJSON(S.base + "index.json");
  } catch (e) {
    $("#main").removeAttribute("aria-busy");
    const failed = S.status && !S.status.ok;
    $("#main").innerHTML = `${pageHead("Themegraph", "")}<div class="notice ${failed ? "bad" : ""}"><div><b>No published data yet.</b> ${failed ? `The first update did not complete (${esc(Object.keys(S.status.errors || {}).join(", "))}).` : "The first update has not run."} Run <span class="mono">themegraph monitor refresh</span> to publish one.</div></div>`;
    return;
  }
  S.U = S.index.universe || {};
  S.B = S.index.benchmarks || S.U.benchmarks || S.B;
  S.byId = Object.fromEntries(S.index.themes.map((r) => [r.id, r]));
  $("#main").removeAttribute("aria-busy");
  $("#release").textContent = `Release ${S.index.release}`;
  shellStatus();
  bindSearch();
  window.addEventListener("hashchange", route);
  route();
}

start();
