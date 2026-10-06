// UNIVERSE FLOW panel: coverage map + money-flow graph, generic in the
// universe schema (verticals -> companies, edges). batch 7 built it for
// ai_buildout; batch 8 extends it to market_structure via a universe
// selector — no new tab, no AI-specific hardcoding. The same renderer must
// later accept bank, crypto and fixed-income universes.
//
// Layout for scale (50-70 nodes can't all show on a phone):
//   1. universe selector chips (AI BUILDOUT / MARKET STRUCTURE)
//   2. aggregate stacked chart (metric comes from the universe's aggregates)
//   3. search box + vertical filter chips
//   4. vertical-grouped company cards (financials + risk flags); tap a card
//      for the detail view with an EGO GRAPH (node + 1-hop deal neighbors)
//   5. MONEY FLOW section: hub-selector chips + full-universe map toggle
//      (desktop widths; horizontal scroll on phone)
//   6. concentration/cashflow risks + risk notes
// Deal amounts are press-reported commitment sizes, not verified cash flows.
import { fmtAge, isStale } from "../fmt.js";
import { ecosystemMap } from "../ecosystem-map.js";

const STALE_MINUTES = 20160; // 2x the weekly universe-graph cadence

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtUsd = (v) => {
  if (v == null) return "—";
  const neg = v < 0, a = Math.abs(v);
  const s = a >= 1e9 ? `$${(a / 1e9).toFixed(1)}B` : `$${(a / 1e6).toFixed(0)}M`;
  return neg ? `-${s}` : s;
};

// macro group -> color (generic; groups come from the universe file)
const GROUP_COLORS = {
  // batch 7 (AI buildout)
  "Financial firms": "#8fa8d8", "AI labs": "#e8c96a", "Neoclouds": "#e08a8a",
  "Hyperscalers": "#7fd4a8", "Supply chain": "#b48ae0", "Power & real estate": "#e0a35e",
  // batch 8 (market structure)
  "Trading tech": "#4f8ff7", "Venues": "#e0a35e", "Liquidity": "#7fd4a8",
  "Quant": "#b48ae0", "Brokers & banks": "#8fa8d8",
};
const groupColor = (g) => GROUP_COLORS[g] || "#9a9a9a";

// per-universe state (selector keeps each universe's search/ego intact)
const _states = {};
const stateFor = (uid, defaults) =>
  _states[uid] || (_states[uid] = { q: "", vertical: "all", ego: defaults.ego, fullMap: false, landscape: false, _sel: null });

// edge kind -> svg style (batch 7 money-flow kinds + batch 8 deal kinds)
const edgeStyle = (k) =>
  k === "equity" ? `stroke="#e8e8e8" stroke-width="2"` :
  k === "backstop" ? `stroke="#e8e8e8" stroke-width="3.5" stroke-dasharray="7 4"` :
  k === "debt" ? `stroke="#8fa8d8" stroke-width="1.6"` :
  k === "lease" ? `stroke="#b48ae0" stroke-width="1.6" stroke-dasharray="3 3"` :
  k === "acquire" ? `stroke="#e05252" stroke-width="2.4"` :
  k === "invest" ? `stroke="#7fd4a8" stroke-width="1.8"` :
  k === "pfof" ? `stroke="#e8c96a" stroke-width="1.6" stroke-dasharray="4 3"` :
  k === "owns" ? `stroke="#8fa8d8" stroke-width="2"` :
  k === "spinoff" ? `stroke="#b48ae0" stroke-width="1.6" stroke-dasharray="3 3"` :
  k === "affiliate" ? `stroke="#777" stroke-width="1.2" stroke-dasharray="2 2"` :
  `stroke="#777" stroke-width="1.2"`;

// per-universe display options (kept out of the vendored JSON so the
// schema stays data-only)
const UNIVERSE_OPTS = {
  ai_buildout: {
    tabLabel: "AI BUILDOUT",
    hubs: ["nvidia", "openai", "anthropic", "microsoft", "amazon"],
    ego: "nvidia",
    chartTitle: "AI CAPEX — BIG-6 QUARTERLY",
    chartNote: "Big-6 = MSFT+GOOGL+AMZN+META+ORCL+AAPL, summed by as-reported quarter-end (fiscal quarters not re-aligned). USD.",
    legend: `— equity &nbsp;<span style="border-top:3px dashed #e8e8e8;display:inline-block;width:26px"></span> backstop &nbsp;<span style="color:#8fa8d8">—</span> debt &nbsp;<span style="color:#b48ae0">- -</span> lease &nbsp;<span style="color:#777">—</span> purchase`,
  },
  market_structure: {
    tabLabel: "MARKET STRUCTURE",
    hubs: ["citadel-securities", "virtu", "schwab", "ubs", "memx"],
    ego: "citadel-securities",
    chartTitle: "REVENUE — BANKS + EXCHANGES (QUARTERLY)",
    chartNote: "MSB6 = JPM+BAC+C+MS+GS+UBS · EXCH4 = CME+ICE+NDAQ+CBOE, summed by as-reported quarter-end (fiscal quarters not re-aligned). Revenue — capex tags are not meaningful for banks/brokers. USD.",
    legend: `<span style="color:#e05252">—</span> acquire &nbsp;<span style="color:#7fd4a8">—</span> invest &nbsp;<span style="color:#e8c96a">- -</span> pfof &nbsp;<span style="color:#8fa8d8">—</span> owns &nbsp;<span style="color:#b48ae0">- -</span> spinoff`,
  },
  bank_fixed_income: {
    tabLabel: "BANKS & FI",
    hubs: ["jpmorgan", "bank-of-america", "fannie-mae", "jpm-securities", "goldman-sachs"],
    ego: "jpmorgan",
    chartTitle: "REVENUE — BIG-4 + REGIONALS (QUARTERLY)",
    chartNote: "BIG4 = JPM+BAC+C+WFC · REG8 = USB+PNC+TFC+FITB+KEY+RF+HBAN+COF, summed by as-reported quarter-end (fiscal quarters not re-aligned). Revenue — capex tags are not meaningful for banks. USD.",
    legend: `<span style="color:#e05252">—</span> acquire &nbsp;<span style="color:#7fd4a8">—</span> purchase &nbsp;<span style="color:#8fa8d8">—</span> affiliate`,
  },
  technology: {
    tabLabel: "TECHNOLOGY",
    hubs: ["fis", "stripe", "ion-group", "exegy", "coinbase"],
    ego: "fis",
    chartTitle: "REVENUE — FINTECH + PAY NETWORKS (QUARTERLY)",
    chartNote: "FINT = FIS+FISV+JKHY+QTWO+MQ+PYPL+GPN · PAYNET = V+MA, summed by as-reported quarter-end (fiscal quarters not re-aligned). USD.",
    legend: `<span style="color:#e05252">—</span> acquire &nbsp;<span style="color:#999">- -</span> terminated`,
  },
  vendor: {
    tabLabel: "VENDOR",
    hubs: ["spgi", "lseg", "moodys", "msci", "factset"],
    ego: "spgi",
    chartTitle: "REVENUE — DATA & ANALYTICS (QUARTERLY)",
    chartNote: "DATA = SPGI+MCO+MSCI+FDS+MORN · IDX = MSCI+SPGI, summed by as-reported quarter-end (fiscal quarters not re-aligned). USD.",
    legend: `<span style="color:#e05252">—</span> acquire &nbsp;<span style="color:#8fa8d8">—</span> owns`,
  },
  etf: {
    tabLabel: "ETF ECOSYSTEM",
    hubs: ["blackrock", "state-street", "citadel-securities", "jpmorgan", "sp-dow-jones"],
    ego: "blackrock",
    chartTitle: "REVENUE — ISSUERS + APs (QUARTERLY)",
    chartNote: "ISSUER4 = BLK+STT+IVZ+SCHW · AP5 = JPM+GS+MS+BAC+C, summed by as-reported quarter-end (fiscal quarters not re-aligned). Revenue — capex tags are not meaningful for issuers/banks. USD.",
    legend: `<span style="color:#8fa8d8">—</span> license &nbsp;<span style="color:#7fd4a8">—</span> liquidity &nbsp;<span style="color:#e8c96a">- -</span> ap_flow`,
  },
  crypto: {
    tabLabel: "CRYPTO",
    hubs: ["coinbase", "strategy", "circle", "mara", "blackrock-ibit"],
    ego: "coinbase",
    chartTitle: "REVENUE — MINERS + EXCHANGES (QUARTERLY)",
    chartNote: "MINERS = MARA+RIOT+CLSK+HUT+BITF+CORZ · CEX = COIN+BLSH+HOOD+CRCL, summed by as-reported quarter-end (fiscal quarters not re-aligned). Revenue — capex tags are not meaningful for exchanges/miners. USD.",
    legend: `<span style="color:#e05252">—</span> acquire &nbsp;<span style="color:#7fd4a8">—</span> invest`,
  },
};
const optsFor = (doc) => UNIVERSE_OPTS[doc.universe_id] || {
  tabLabel: (doc.universe_id || "UNIVERSE").toUpperCase().replace(/_/g, " "),
  hubs: [], ego: null, chartTitle: "AGGREGATE — QUARTERLY", chartNote: "", legend: "",
};

const allCompanies = (doc) =>
  (doc.verticals || []).flatMap((v) => (v.companies || []).map((c) => ({ ...c, _v: v.id, _vlabel: v.label, _group: v.group })));

const findCompany = (doc, id) => allCompanies(doc).find((c) => c.id === id);

// ---------- aggregate stacked chart (metric comes from the aggregates) ----------
function stackChart(doc, opts) {
  const stack = doc.capex_stack || {};
  const tickers = Object.keys(stack).filter((t) => !t.startsWith("__agg__") && (stack[t] || []).length);
  if (!tickers.length) return `<div class="muted">Aggregate history not yet available.</div>`;
  const dates = [...new Set(tickers.flatMap((t) => stack[t].map((p) => p[0])))].sort().slice(-8);
  const W = 560, H = 170, pad = 34;
  const byDate = {};
  dates.forEach((d) => { byDate[d] = {}; tickers.forEach((t) => { byDate[d][t] = 0; }); });
  tickers.forEach((t) => stack[t].forEach(([d, v]) => { if (byDate[d]) byDate[d][t] = v || 0; }));
  const totals = dates.map((d) => tickers.reduce((s, t) => s + byDate[d][t], 0));
  const max = Math.max(...totals, 1);
  const bw = (W - pad * 2) / dates.length;
  const colors = ["#4f8ff7", "#7fd4a8", "#e8c96a", "#e08a8a", "#b48ae0", "#9a9a9a"];
  let bars = "";
  dates.forEach((d, i) => {
    let y = H - pad;
    tickers.forEach((t, j) => {
      const v = byDate[d][t], h = (v / max) * (H - pad * 2);
      if (h > 0.5) bars += `<rect x="${(pad + i * bw + 3).toFixed(1)}" y="${(y - h).toFixed(1)}" width="${(bw - 6).toFixed(1)}" height="${h.toFixed(1)}" fill="${colors[j % colors.length]}"><title>${t} ${d}: ${fmtUsd(v)}</title></rect>`;
      y -= h;
    });
    bars += `<text x="${(pad + i * bw + bw / 2).toFixed(1)}" y="${H - 12}" text-anchor="middle" font-size="9" fill="var(--muted)">${d.slice(2, 7)}</text>`;
  });
  const legend = tickers.map((t, j) => `<span style="color:${colors[j % colors.length]}">■</span> ${t}`).join(" &nbsp; ");
  return `<div class="ai-capex-wrap"><svg viewBox="0 0 ${W} ${H}" class="ai-capex-svg" role="img" aria-label="${esc(opts.chartTitle)}">${bars}<text x="${W - 4}" y="14" text-anchor="end" font-size="10" fill="var(--muted)">peak ${fmtUsd(Math.max(...totals))}/qtr</text></svg><div class="ai-legend">${legend}</div><div class="muted" style="font-size:10px">${esc(opts.chartNote)}</div></div>`;
}

// ---------- company cards ----------
function flagBadges(n) {
  return (n.flags || []).filter((x) => x !== "private_no_financials")
    .map((x) => `<span class="ai-flag">${esc(x.replace(/_/g, " "))}</span>`).join("");
}

function companyCard(n) {
  const f = n.financials || {};
  const fcfCls = f.fcf != null ? (f.fcf < 0 ? "down" : "up") : "";
  const sub = n.kind === "public"
    ? `<div class="ai-card-fin"><span class="${fcfCls}">FCF ${fmtUsd(f.fcf)}</span><span class="muted">capex ${fmtUsd(f.capex)}</span></div>`
    : `<div class="ai-card-fin muted">private — deals only</div>`;
  return `<div class="ai-card" data-id="${esc(n.id)}" role="button" tabindex="0">
    <div class="ai-card-head">${n.logo ? `<img class="ai-logo" src="${esc(n.logo)}" alt="" loading="lazy" onerror="this.remove()" style="width:22px;height:22px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:6px">` : ""}<strong>${esc(n.name)}</strong>
      ${n.ticker ? `<span class="muted">${esc(n.ticker)}</span>` : ""}
      ${f.fcf != null && f.fcf < 0 ? `<span class="ai-dot-red" title="negative FCF"></span>` : ""}</div>
    ${sub}
    <div class="ai-card-flags">${flagBadges(n)}</div>
  </div>`;
}

function cardsView(doc, state) {
  const q = state.q.trim().toLowerCase();
  const groups = {};
  allCompanies(doc).forEach((n) => {
    if (state.vertical !== "all" && n._v !== state.vertical) return;
    if (q && !(n.name.toLowerCase().includes(q) || (n.ticker || "").toLowerCase().includes(q))) return;
    (groups[n._vlabel] = groups[n._vlabel] || []).push(n);
  });
  const secs = Object.entries(groups).map(([label, list]) => `
    <div class="ai-vlabel">${esc(label)} <span class="muted">(${list.length})</span></div>
    <div class="ai-cards">${list.map(companyCard).join("")}</div>`).join("");
  return secs || `<div class="muted">No companies match.</div>`;
}

// ---------- node detail + ego graph ----------
function egoNodes(doc, centerId) {
  const edges = (doc.edges || []).filter((e) => e.from === centerId || e.to === centerId);
  const ids = new Set([centerId]);
  edges.forEach((e) => { ids.add(e.from); ids.add(e.to); });
  return { nodes: allCompanies(doc).filter((n) => ids.has(n.id)), edges, centerId };
}

const fmtDealAmt = (e) => e.amount_bn != null ? `<strong>$${e.amount_bn}B</strong> ` : "";

function graphSvg(nodes, edges, centerId) {
  const W = 1000, H = 520, top = 44;
  const clipSeq = (graphSvg._seq = (graphSvg._seq || 0) + 1);
  const groups = [...new Set(nodes.map((n) => n._group || "Other"))];
  const pos = {};
  groups.forEach((g, gi) => {
    const list = nodes.filter((n) => (n._group || "Other") === g);
    const x = 120 + gi * ((W - 240) / Math.max(groups.length - 1, 1));
    // center node first in its group, pinned near middle
    list.sort((a, b) => (b.id === centerId) - (a.id === centerId));
    list.forEach((n, i) => {
      const y = n.id === centerId ? H / 2 : top + 30 + i * ((H - top - 80) / Math.max(list.length - 1, 1));
      pos[n.id] = { x, y };
    });
  });
  const maxAmt = Math.max(...edges.map((e) => e.amount_bn || 0), 1);
  const nodeMax = {};
  nodes.forEach((n) => { nodeMax[n.id] = 0; });
  edges.forEach((e) => {
    nodeMax[e.from] = Math.max(nodeMax[e.from] || 0, e.amount_bn || 0);
    nodeMax[e.to] = Math.max(nodeMax[e.to] || 0, e.amount_bn || 0);
  });
  let svg = groups.map((g, gi) => {
    const x = 120 + gi * ((W - 240) / Math.max(groups.length - 1, 1));
    return `<text x="${x}" y="24" text-anchor="middle" font-size="13" font-weight="700" fill="${groupColor(g)}">${esc(g)}</text>`;
  }).join("");
  edges.forEach((e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const mx = (a.x + b.x) / 2;
    svg += `<path d="M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}" fill="none" ${edgeStyle(e.kind)} opacity="0.8"><title>${esc(e.from)} → ${esc(e.to)}${e.amount_bn != null ? `: $${e.amount_bn}B` : ""} ${esc(e.kind)} — ${esc(e.source)}${e.date ? ` (${esc(e.date)})` : ""}</title></path>`;
  });
  edges.forEach((e) => {
    if ((e.amount_bn || 0) < 10) return;
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    svg += `<text x="${(a.x + b.x) / 2}" y="${(a.y + b.y) / 2 - 5}" text-anchor="middle" font-size="10.5" fill="#fff" font-weight="700" style="paint-order:stroke" stroke="#111" stroke-width="3">$${e.amount_bn}B</text>`;
  });
  nodes.forEach((n) => {
    const p = pos[n.id];
    if (!p) return;
    const r = n.id === centerId ? 34 : 15 + 21 * Math.sqrt((nodeMax[n.id] || 0) / maxAmt);
    const neg = (n.flags || []).includes("cashflow_negative");
    const isC = n.id === centerId;
    const rr = r.toFixed(1);
    // Logo clipped to the node circle. The colored circle is drawn first, so a
    // failed/blank logo image simply leaves the circle visible (no JS needed).
    let logoEl = "";
    if (n.logo) {
      const clipId = `lgc${clipSeq}_${String(n.id).replace(/[^a-zA-Z0-9_-]/g, "_")}`;
      logoEl = `<clipPath id="${clipId}"><circle cx="${p.x}" cy="${p.y}" r="${rr}"/></clipPath>` +
        `<image href="${esc(n.logo)}" x="${(p.x - r).toFixed(1)}" y="${(p.y - r).toFixed(1)}" width="${(2 * r).toFixed(1)}" height="${(2 * r).toFixed(1)}" preserveAspectRatio="xMidYMid slice" clip-path="url(#${clipId})"/>`;
    }
    svg += `<g class="ai-node" data-node="${esc(n.id)}" style="cursor:pointer">
      <circle cx="${p.x}" cy="${p.y}" r="${rr}" fill="${groupColor(n._group || "Other")}" fill-opacity="${neg ? 0.35 : 0.85}" stroke="${isC ? "#fff" : neg ? "#e05252" : "#111"}" stroke-width="${isC ? 2.5 : neg ? 2 : 1}"/>${logoEl}
      <text x="${p.x}" y="${(p.y + r + 14).toFixed(1)}" text-anchor="middle" font-size="11" fill="#e8e8e8">${esc(n.name)}</text></g>`;
  });
  return `<div class="ai-graph-scroll"><svg viewBox="0 0 ${W} ${H}" class="ai-graph-svg" role="img" aria-label="money-flow ego graph">${svg}</svg></div>`;
}

function nodeDetail(doc, n) {
  const edges = (doc.edges || []).filter((e) => e.from === n.id || e.to === n.id);
  const f = n.financials || {};
  const deals = edges.map((e) => `<li>${fmtDealAmt(e)}${esc(e.kind)} — ${esc(e.from)} → ${esc(e.to)}${e.label ? `: ${esc(e.label)}` : ""} <span class="muted">(${esc(e.source)}${e.date ? `, ${esc(e.date)}` : ""}; ${esc(e.confidence)})</span></li>`).join("");
  const fin = f.revenue != null ? `<div class="ai-kv-grid">
      <div class="ai-kv"><span>Revenue</span><strong>${fmtUsd(f.revenue)}</strong></div>
      <div class="ai-kv"><span>Capex</span><strong>${fmtUsd(f.capex)}</strong></div>
      <div class="ai-kv"><span>Op. cash flow</span><strong>${fmtUsd(f.ocf)}</strong></div>
      <div class="ai-kv"><span>FCF</span><strong>${fmtUsd(f.fcf)}</strong></div>
      <div class="ai-kv"><span>Capex/rev</span><strong>${f.capex_intensity == null ? "—" : (f.capex_intensity * 100).toFixed(1) + "%"}</strong></div>
      <div class="ai-kv"><span>Debt</span><strong>${fmtUsd(f.debt)}</strong></div>
    </div><div class="muted" style="font-size:10px">Quarter ${esc(String(f.latest_quarter || "").slice(0, 7))} · SEC XBRL${f.currency && f.currency !== "USD" ? ` · figures in ${esc(f.currency)}` : ""}</div>`
    : `<div class="muted">No XBRL financials — deals only.</div>`;
  return `<div class="ai-node-card">
    <div class="ai-node-card-head">${n.logo ? `<img class="ai-logo" src="${esc(n.logo)}" alt="" loading="lazy" onerror="this.remove()" style="width:26px;height:26px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:8px">` : ""}<strong>${esc(n.name)}</strong>
      ${n.ticker ? `<span class="muted">${esc(n.ticker)}</span>` : ""}
      <span class="muted">${esc(n._vlabel || "")}</span></div>
    ${fin}
    <div class="ai-flags">Risk flags: ${flagBadges(n) || "none"}</div>
    <div class="ai-section-sub">DEALS (${edges.length})</div>
    <ul class="ai-deals">${deals || "<li>none recorded</li>"}</ul>
  </div>`;
}

// ---------- main render (generic across universes) ----------
export function renderUniverse(doc, opts, slot) {
  const state = stateFor(doc.universe_id || "unknown", opts);
  const verticals = doc.verticals || [], edges = doc.edges || [];
  if (!verticals.length) {
    slot.innerHTML = `<div class="empty-state">NO DATA — ${esc(opts.tabLabel)} universe graph job has not run yet</div>`;
    return;
  }
  if (!findCompany(doc, state.ego) && opts.ego && findCompany(doc, opts.ego)) state.ego = opts.ego;
  if (!findCompany(doc, state.ego)) state.ego = (allCompanies(doc)[0] || {}).id;
  const nPub = allCompanies(doc).filter((n) => n.kind === "public").length;
  const nPriv = allCompanies(doc).filter((n) => n.kind !== "public").length;
  const chips = [`<button class="ai-chip${state.vertical === "all" ? " on" : ""}" data-v="all">All</button>`]
    .concat(verticals.map((v) => `<button class="ai-chip${state.vertical === v.id ? " on" : ""}" data-v="${esc(v.id)}">${esc(v.label)}</button>`)).join("");
  const hubChips = (opts.hubs || []).map((h) => {
    const n = findCompany(doc, h);
    return n ? `<button class="ai-chip${state.ego === h ? " on" : ""}" data-hub="${esc(h)}">${esc(n.name)}</button>` : "";
  }).join("");

  const ego = egoNodes(doc, state.ego);
  const egoCenter = findCompany(doc, state.ego);
  const selNode = state._sel ? findCompany(doc, state._sel) : null;
  const selDetail = selNode ? nodeDetail(doc, selNode) : "";

  // full-universe map: nodes with any deal, grouped by macro group
  const dealIds = new Set();
  edges.forEach((e) => { dealIds.add(e.from); dealIds.add(e.to); });
  const fullNodes = allCompanies(doc).filter((n) => dealIds.has(n.id));
  const fullBlock = state.fullMap
    ? `<div class="ai-section-sub">FULL DEAL MAP (ALL REPORTED EDGES)</div>${graphSvg(fullNodes, edges, null)}`
    : "";

  const r = doc.rollups || {};
  const notes = (doc.risk_notes || []).map((x) =>
    `<li><strong>${esc(x.id.replace(/-/g, " "))}:</strong> ${esc(x.text)} <span class="muted">(${esc(x.source)})</span></li>`).join("");

  slot.innerHTML = `
    <div class="hyper-section-title">${esc(opts.chartTitle)}</div>
    ${stackChart(doc, opts)}
    <div class="hyper-section-title">COVERAGE MAP — ${nPub} PUBLIC + ${nPriv} PRIVATE</div>
    <input class="ai-search" type="search" placeholder="Search companies…" value="${esc(state.q)}" aria-label="Search companies">
    <div class="ai-chips">${chips}<button class="ai-chip${state.landscape ? " on" : ""} ai-landscape-toggle" title="Messari-style ecosystem landscape: all nodes grouped by category">Landscape</button></div>
    <div class="ai-cards-slot">${state.landscape
      ? ecosystemMap(doc, { asOf: doc.as_of, source: "press-reported deals + SEC XBRL" })
      : cardsView(doc, state)}</div>
    <div class="ai-sel-slot">${selDetail}</div>
    <div class="hyper-section-title">MONEY FLOW — EGO GRAPH</div>
    <div class="ai-chips">${hubChips}
      <button class="ai-chip${state.fullMap ? " on" : ""} ai-fullmap-toggle">Full map</button></div>
    <div class="muted" style="font-size:10px;margin-bottom:4px">Center: <strong>${esc(egoCenter ? egoCenter.name : state.ego)}</strong> — tap any company card above to re-center. ${opts.legend}</div>
    ${graphSvg(ego.nodes, ego.edges, state.ego)}
    ${fullBlock}
    <div class="hyper-section-title">CONCENTRATION &amp; CASHFLOW RISKS</div>
    <div class="ai-rollup muted">${r.total_reported_bn != null ? `$${r.total_reported_bn}B in reported deals across ${r.edge_count} edges. ` : ""}${(r.nodes_cashflow_negative || []).length ? `Negative FCF: <strong>${r.nodes_cashflow_negative.map(esc).join(", ")}</strong>. ` : ""}</div>
    <ul class="ai-risks">${notes}</ul>
    <div class="muted" style="font-size:10px">Deals are press-reported commitment sizes (per-edge source on tap/hover), not verified cash transfers. Financials: SEC EDGAR XBRL, quarterly${doc.capex_overlay ? ` · overlay ${esc(doc.capex_overlay)}` : ""}.</div>`;

  const rerender = () => renderUniverse(doc, opts, slot);
  const search = slot.querySelector(".ai-search");
  if (search) {
    search.addEventListener("input", (e) => { state.q = e.target.value; });
    search.addEventListener("change", rerender);
  }
  slot.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => { state.vertical = b.dataset.v; state._sel = null; rerender(); }));
  slot.querySelectorAll("[data-hub]").forEach((b) => b.addEventListener("click", () => { state.ego = b.dataset.hub; state._sel = null; rerender(); }));
  const ft = slot.querySelector(".ai-fullmap-toggle");
  if (ft) ft.addEventListener("click", () => { state.fullMap = !state.fullMap; rerender(); });
  slot.querySelectorAll(".ai-card").forEach((c) => {
    const go = () => { state._sel = c.dataset.id; state.ego = c.dataset.id; rerender(); };
    c.addEventListener("click", go);
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
  const lt = slot.querySelector(".ai-landscape-toggle");
  if (lt) lt.addEventListener("click", () => { state.landscape = !state.landscape; rerender(); });
  slot.querySelectorAll(".eco-chip").forEach((c) => {
    const go = () => { state._sel = c.dataset.node; state.ego = c.dataset.node; rerender(); };
    c.addEventListener("click", go);
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
  slot.querySelectorAll(".ai-node").forEach((g) => {
    g.addEventListener("click", () => { state._sel = g.dataset.node; state.ego = g.dataset.node; rerender(); });
  });
}

// back-compat wrapper (batch 7 API)
export function renderAiFlow(doc) {
  const body = document.querySelector("#panel-ai-flow .panel-body");
  if (body) renderUniverse(doc, optsFor(doc), body);
}

export function renderMsFlow(doc) {
  const body = document.querySelector("#panel-ai-flow .panel-body");
  if (body) renderUniverse(doc, optsFor(doc), body);
}

const EMPTY = { universe_id: "", verticals: [], edges: [], rollups: {}, capex_stack: {}, risk_notes: [], updated_at: null, source: null };

// Universe selector: renders the toggle + the selected universe into
// #panel-ai-flow. Called by main.js with the dashboard panels (one doc per
// universe, in display order). Generic across N universes.
export function renderUniverseSelector(...docs) {
  const body = document.querySelector("#panel-ai-flow .panel-body");
  const footEl = document.querySelector("#panel-ai-flow .panel-foot");
  if (!body) return;
  const FALLBACK_IDS = ["ai_buildout", "market_structure", "bank_fixed_income", "technology", "vendor", "etf", "crypto"];
  docs = docs.map((d, i) => (d && d.universe_id ? d : { ...EMPTY, universe_id: FALLBACK_IDS[i] || `universe_${i}` }));
  const has = docs.map((d) => (d.verticals || []).length > 0);
  if (!has.some(Boolean)) {
    body.innerHTML = `<div class="empty-state">NO DATA — universe graph jobs have not run yet</div>`;
    if (footEl) footEl.textContent = "DATA: —";
    return;
  }
  let sel = has.findIndex(Boolean);
  const doc = () => docs[sel];
  const renderSel = () => {
    const chips = body.querySelectorAll("[data-uni]");
    chips.forEach((c) => c.classList.toggle("on", Number(c.dataset.uni) === sel));
    renderUniverse(doc(), optsFor(doc()), body.querySelector(".uni-slot"));
    const d = doc();
    if (footEl) {
      const stale = isStale(d.updated_at, STALE_MINUTES);
      footEl.innerHTML = `DATA: ${esc(d.as_of || "—")}${stale ? ' <span class="stale">STALE</span>' : ""} · press-reported deals + SEC XBRL`;
      footEl.title = fmtAge(d.updated_at);
    }
  };
  body.innerHTML = `
    <div class="ai-chips uni-toggle" style="margin-bottom:8px">
      ${docs.map((d, i) => `<button class="ai-chip" data-uni="${i}"${has[i] ? "" : " disabled"}>${esc(optsFor(d).tabLabel)}</button>`).join("")}
    </div>
    <div class="uni-slot"></div>`;
  body.querySelectorAll("[data-uni]").forEach((b) => b.addEventListener("click", () => { sel = Number(b.dataset.uni); renderSel(); }));
  renderSel();
}
