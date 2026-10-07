import { fmtUsd } from "../fmt.js";
import { getSeries } from "../api.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";
import { rangeCells, statsFromValues, RANGE_TH, RANGE_LEGEND } from "../rangeviz.js";

const CHAIN_ABBR = { __proto__: null, Base: "BASE", Ethereum: "ETH", Arbitrum: "ARB" };
const apy = (x) => (x == null ? "—" : x.toFixed(2));
// Pool/protocol names come from a third-party API — escape before innerHTML,
// and only link out to http(s) URLs.
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Every vault name links to Zyfai rather than deep-linking the underlying
// protocol (the per-opportunity `url` the API still carries).
const ZYFAI_URL = "https://www.zyf.ai/";

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
// Per-tier aggregates, derived from the rows on display: TVL-weighted mean APY
// (pools with no TVL can't be weighted and are left out) and median APY.
const tierStats = (rows) => {
  const apys = rows.map((r) => r.apy).filter((x) => x != null);
  const weightable = rows.filter((r) => r.apy != null && r.tvl_usd > 0);
  const tvl = weightable.reduce((s, r) => s + r.tvl_usd, 0);
  return {
    wavg: tvl > 0 ? weightable.reduce((s, r) => s + r.apy * r.tvl_usd, 0) / tvl : null,
    med: apys.length ? median(apys) : null,
  };
};

let defiView = "vaults"; // "vaults" | "markets" | "crypto" | "rwa" — in-memory, default VAULTS (spec §3)

const VIEW_TITLES = { vaults: "CURATED VAULTS — USDC", markets: "MORPHO MARKETS — USDC", crypto: "CRYPTO BREADTH — TOP 50", rwa: "TOKENIZED ASSETS — RWA" };

export function initDefiViewToggle(onChange) {
  const buttons = document.querySelectorAll("#panel-defi .view-toggle button");
  buttons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.view === defiView));
    button.addEventListener("click", () => {
      if (button.dataset.view === defiView) return;
      buttons.forEach((x) => {
        const active = x === button;
        x.classList.toggle("active", active);
        x.setAttribute("aria-pressed", String(active));
      });
      defiView = button.dataset.view;
      onChange();
    });
  });
}

function renderVaults(panel) {
  const body = document.querySelector("#panel-defi .panel-body");
  if (!panel.rows.length) {
    body.innerHTML = `<div class="empty-state">NO DATA</div>`;
    return;
  }
  const groups = new Map(); // rows arrive tier-major; insertion order preserves it
  for (const r of panel.rows) {
    if (!groups.has(r.tier)) groups.set(r.tier, []);
    groups.get(r.tier).push(r);
  }
  const pool = (r) =>
    `<a class="defi-link" href="${ZYFAI_URL}" target="_blank" rel="noopener">${esc(r.pool)}</a>`;
  const section = (tier, rows) => {
    const { wavg, med } = tierStats(rows);
    return `
    <tr class="tier-head" data-sort-row="off"><td colspan="7">${esc(tier).toUpperCase()}
      <span class="tier-stats">TVL-WTD ${apy(wavg)} · MED ${apy(med)}</span></td></tr>
    ${rows.map((r) => `<tr>
      <td class="sym">${pool(r)}</td>
      <td>${esc(r.protocol)}</td>
      <td>${CHAIN_ABBR[r.chain] ?? esc(r.chain)}</td>
      <td>${apy(r.apy)}</td>
      <td>${apy(r.apy_7d)}</td>
      <td>${apy(r.apy_30d)}</td>
      <td>${fmtUsd(r.tvl_usd)}</td>
    </tr>`).join("")}`;
  };
  body.innerHTML = `<table>
    <tr><th>Pool</th><th>Proto</th><th>Chain</th><th>APY</th><th>7D</th><th>30D</th><th>TVL</th></tr>
    ${[...groups.entries()].map(([tier, rows]) => section(tier, rows)).join("")}
  </table>`;
}

function renderMarkets(morphoPanel) {
  const body = document.querySelector("#panel-defi .panel-body");
  const rows = morphoPanel.rows;
  if (!rows.length) {
    body.innerHTML = `<div class="empty-state">NO DATA</div>`;
    return;
  }
  body.innerHTML = `<table data-sortable>
    <tr><th>Collat</th><th>Lltv</th><th>Chain</th><th>Supply</th><th>Borrow</th><th>Util</th><th>Tvl</th></tr>
    ${rows.map((r) => `<tr>
      <td class="sym">${esc(r.collateral)}</td>
      <td>${(Number.isInteger(r.lltv_pct) ? r.lltv_pct.toFixed(0) : r.lltv_pct.toFixed(1))}%</td>
      <td>${CHAIN_ABBR[r.chain] ?? esc(r.chain)}</td>
      <td>${r.supply_apy.toFixed(2)}</td>
      <td>${r.borrow_apy.toFixed(2)}</td>
      <td>${r.utilization_pct.toFixed(0)}%</td>
      <td>${fmtUsd(r.tvl_usd)}</td>
    </tr>`).join("")}
  </table>`;
}

export function renderDefi(panel, morphoPanel) {
  document.getElementById("defi-view-title").textContent = VIEW_TITLES[defiView];
  if (defiView === "markets") {
    renderMarkets(morphoPanel);
  } else if (defiView === "crypto") {
    renderCrypto(panel);
  } else if (defiView === "rwa") {
    renderRwa(panel);
  } else {
    renderVaults(panel);
  }
}

export function defiFootData(defiPanel, morphoPanel) {
  if (defiView === "markets") return morphoPanel;
  if (defiView === "crypto") {
    return { source: "coingecko", updated_at: defiPanel.crypto_updated_at ?? null };
  }
  if (defiView === "rwa") {
    return { source: "defillama+coingecko", updated_at: defiPanel.rwa_updated_at ?? null };
  }
  return defiPanel;
}

// Batch 11: CoinGecko top-50 crypto breadth. Rows arrive rank-ordered.
function renderCrypto(panel) {
  const body = document.querySelector("#panel-defi .panel-body");
  const coins = panel.crypto ?? [];
  if (!coins.length) {
    body.innerHTML = `<div class="empty-state">NO DATA</div>`;
    return;
  }
  const pct = (x) => {
    if (x == null) return "—";
    const cls = x > 0 ? "up" : x < 0 ? "down" : "flat";
    return `<span class="${cls}">${x > 0 ? "+" : ""}${x.toFixed(1)}%</span>`;
  };
  const usd = (x) => (x == null ? "—" : x >= 1
    ? x.toLocaleString("en-US", { maximumFractionDigits: 2 })
    : x.toPrecision(3));
  const big = (x) => (x == null ? "—" : x >= 1e9
    ? `$${(x / 1e9).toFixed(1)}B` : `$${(x / 1e6).toFixed(0)}M`);
  const dom = panel.btc_dominance_pct;
  body.innerHTML =
    (dom != null ? `<div class="muted" style="margin-bottom:6px">BTC dominance ${dom.toFixed(1)}%</div>` : "") +
    `<table class="crypto-table" data-sortable>
    <tr><th>#</th><th>Coin</th><th>Price</th><th>24h</th><th>7d</th><th>MCap</th><th>Vol24h</th></tr>
    ${coins.map((c) => `<tr>
      <td class="num">${c.rank ?? "—"}</td>
      <td class="sym">${esc(c.symbol)} <span class="muted">${esc(c.name)}</span></td>
      <td class="num">$${usd(c.price)}</td>
      <td class="num">${pct(c.chg24h)}</td>
      <td class="num">${pct(c.chg7d)}</td>
      <td class="num">${big(c.mcap)}</td>
      <td class="num">${big(c.vol24h)}</td>
    </tr>`).join("")}
  </table>`;
}

let curvePlot = null;

export function renderMidnight(panel) {
  const body = document.querySelector("#panel-midnight .panel-body");
  if (curvePlot) { curvePlot.destroy(); curvePlot = null; } // before innerHTML wipes its root
  if (!panel.rows.length) {
    body.innerHTML = `<div class="empty-state">NO LIVE MARKETS</div>`;
    return;
  }
  body.innerHTML = `<table data-sortable>
    <tr><th>Mat</th><th>Days</th><th>Lend%</th><th>Borr%</th><th>Depth A/B</th><th>Collat</th></tr>
    ${panel.rows.map((r) => `<tr>
      <td class="sym">${esc(r.maturity)}</td>
      <td>${Math.round(r.days)}</td>
      <td>${apy(r.lend_apy)}</td>
      <td>${apy(r.borrow_apy)}</td>
      <td>${fmtUsd(r.ask_depth_usd)}/${fmtUsd(r.bid_depth_usd)}</td>
      <td>${esc(r.collateral)}</td>
    </tr>`).join("")}
  </table>
  <div id="midnight-curve"></div>`;
  drawCurve(panel.rows);
}

function drawCurve(rows) {
  const pts = rows.filter((r) => r.lend_apy != null).sort((a, b) => a.days - b.days);
  if (pts.length < 2) return; // a one-point "curve" is noise — table only
  const root = document.getElementById("midnight-curve");
  curvePlot = new uPlot({
    width: Math.max(260, root.clientWidth || 300), height: 240,
    scales: { x: { time: false } },
    series: [
      { label: "DAYS", value: (u, v) => (v == null ? "--" : Math.round(v)) },
      { label: "LEND %", stroke: "#2563eb", width: 1.5, points: { show: true, size: 5 },
        value: (u, v) => (v == null ? "--" : v.toFixed(2)) },
    ],
    axes: [
      { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } },
      { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } },
    ],
    // default cursor + legend stay on: hovering reads out DAYS / LEND %
  }, [pts.map((r) => r.days), pts.map((r) => r.lend_apy)], root);
}

// ---- Tokenized Assets (RWA) ----
let rwaPlot = null;
let rwaState = { range: "1y", flows: false };

const RWA_RANGES = [
  ["1m", "1M"], ["3m", "3M"], ["1y", "1Y"], ["max", "MAX"],
];

function rwaDeltaCell(nom, pct) {
  const n = nom == null ? "—" : `${nom >= 0 ? "+" : ""}$${Math.abs(nom) >= 1e9 ? (nom / 1e9).toFixed(2) + "B" : (nom / 1e6).toFixed(0) + "M"}`;
  const p = pct == null ? "—" : `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`;
  return `<b>${n}</b> <span class="muted">(${p})</span>`;
}

function rwaStats(pts) {
  // pts: [[dateStr, v], ...] sorted
  if (!pts.length) return null;
  const cur = pts[pts.length - 1][1];
  const ago = (n) => {
    const cut = pts.length - 1 - n;
    return cut >= 0 ? pts[cut][1] : null;
  };
  const d30 = ago(30);
  return {
    cur,
    nom30: d30 == null ? null : cur - d30,
    pct30: d30 ? (cur - d30) / d30 : null,
    n: pts.length,
  };
}

async function renderRwa(panel) {
  const body = document.querySelector("#panel-defi .panel-body");
  const rwa = panel.rwa;
  if (!rwa || !rwa.classes?.length) {
    body.innerHTML = `<div class="empty-state">NO RWA DATA YET — first daily pull pending.</div>`;
    return;
  }
  const scope = rwa.scope || "";
  const classes = rwa.classes;
  const protos = rwa.protocols ?? [];
  const totalMcap = classes.reduce((s, c) => s + (c.total_mcap || 0), 0);

  // KPI tiles need series history — fetch async, render skeleton first
  body.innerHTML = `
    <div class="rwa-scope muted" style="margin-bottom:8px">${esc(scope)}</div>
    <div class="kpi-row" id="rwa-kpis"><div class="kpi"><div class="kpi-v">…</div></div></div>
    <div class="chart-controls" style="margin:10px 0 6px">
      <span class="muted">Market size</span>
      <span class="seg" id="rwa-range">${RWA_RANGES.map(([id, l]) => `<button type="button" data-range="${id}" class="${id === rwaState.range ? "on" : ""}">${l}</button>`).join("")}</span>
      <button type="button" id="rwa-flows" class="${rwaState.flows ? "on" : ""}" style="margin-left:8px">${rwaState.flows ? "Flows" : "Size"}</button>
      <span class="muted" id="rwa-chart-status" style="margin-left:8px"></span>
    </div>
    <div id="rwa-chart" style="min-height:300px"></div>
    <h4 style="margin:14px 0 6px">BY ASSET CLASS <span class="muted">— token market caps (CoinGecko, top-3 tokens/class)</span></h4>
    <div>${HEAT_LEGEND}</div>
    <div style="overflow-x:auto"><table class="rwa-classes" data-sortable>
      <tr><th>Class</th><th>MCap</th><th>Share</th><th>30D Δ</th>${RANGE_TH}</tr>
      <tbody id="rwa-class-rows"><tr><td colspan="8" class="muted">Loading class history…</td></tr></tbody>
    </table></div>
    <h4 style="margin:14px 0 6px">TOP PROTOCOLS <span class="muted">— on-chain RWA TVL (DefiLlama)</span></h4>
    <div style="overflow-x:auto"><table class="rwa-protos" data-sortable>
      <tr><th>Protocol</th><th>Class</th><th>TVL</th><th>Chains</th></tr>
      ${protos.slice(0, 20).map((p) => `<tr>
        <td>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a>` : esc(p.name)}</td>
        <td><span class="tag">${esc(rwa.class_labels?.[p.class] ?? p.class)}</span></td>
        <td class="num"><b>${fmtUsd(p.tvl)}</b></td>
        <td class="muted">${(p.chains ?? []).slice(0, 3).map(esc).join(", ")}</td>
      </tr>`).join("")}
    </table></div>
    <div class="muted" style="margin-top:10px">Full institutional dataset: <a href="https://app.rwa.xyz/" target="_blank" rel="noopener">rwa.xyz</a> (API key required).</div>`;

  document.querySelectorAll("#rwa-range button").forEach((b) =>
    b.addEventListener("click", () => { rwaState.range = b.dataset.range; syncRwa(); renderRwaChart(); }));
  document.getElementById("rwa-flows").addEventListener("click", (e) => {
    rwaState.flows = !rwaState.flows; e.target.textContent = rwaState.flows ? "Flows" : "Size";
    e.target.classList.toggle("on", rwaState.flows); renderRwaChart();
  });
  renderRwaChart();
  fillRwaKpis(totalMcap, protos.length);
  fillRwaClasses(classes, totalMcap);
}

function syncRwa() {
  document.querySelectorAll("#rwa-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === rwaState.range));
}

let rwaChartCache = null;
async function renderRwaChart() {
  const el = document.getElementById("rwa-chart");
  const st = document.getElementById("rwa-chart-status");
  if (!el) return;
  if (st) st.textContent = "Loading…";
  try {
    if (!rwaChartCache) {
      const s = await getSeries("rwa-mcap-total", "max");
      rwaChartCache = (s.points ?? []).map(([d, v]) => [d, v]).sort((a, b) => a[0] < b[0] ? -1 : 1);
    }
    let pts = rwaChartCache;
    const cut = { "1m": 30, "3m": 90, "1y": 365 }[rwaState.range];
    if (cut) pts = pts.slice(-cut);
    if (rwaPlot) { rwaPlot.destroy(); rwaPlot = null; }
    el.innerHTML = "";
    const xs = pts.map(([d]) => Date.parse(d) / 1000);
    let ys, label, fmt;
    if (rwaState.flows) {
      ys = pts.map(([, v], i) => (i < 7 ? null : v - pts[i - 7][1]));
      label = "7D net flow ($)";
      fmt = (v) => v == null ? "—" : `${v >= 0 ? "+" : "−"}$${(Math.abs(v) / 1e6).toFixed(0)}M`;
    } else {
      ys = pts.map(([, v]) => v);
      label = "Tokenized mcap ($)";
      fmt = (v) => v == null ? "—" : `$${(v / 1e9).toFixed(1)}B`;
    }
    const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
    if (typeof uPlot !== "undefined" && xs.length > 1) {
      rwaPlot = new uPlot({
        width: Math.max(300, el.clientWidth || 900), height: 300,
        scales: { x: { time: true } },
        series: [{}, {
          label, stroke: rwaState.flows ? "#7c3aed" : "#2563eb", width: 1.5,
          fill: rwaState.flows ? undefined : "rgba(37,99,235,.12)",
          spanGaps: true, value: (u, v) => fmt(v),
        }],
        axes: [axisStyle, { ...axisStyle, values: (u, vs) => vs.map(fmt) }],
      }, [xs, ys], el);
    }
    if (st) st.textContent = `${pts.length} daily points · DefiLlama + CoinGecko`;
  } catch (e) {
    if (st) st.textContent = "Chart pending — history accumulating.";
  }
}

async function fillRwaKpis(totalMcap, nProtos) {
  const el = document.getElementById("rwa-kpis");
  if (!el) return;
  try {
    const s = await getSeries("rwa-mcap-total", "max");
    const pts = (s.points ?? []).map(([d, v]) => [d, v]).sort((a, b) => a[0] < b[0] ? -1 : 1);
    const st = rwaStats(pts);
    const t = await getSeries("rwa-tvl-total", "max").catch(() => null);
    const tpts = (t?.points ?? []).map(([d, v]) => [d, v]).sort((a, b) => a[0] < b[0] ? -1 : 1);
    const tst = rwaStats(tpts);
    const kpi = (label, val, sub) => `<div class="kpi"><div class="kpi-l">${label}</div><div class="kpi-v">${val}</div><div class="kpi-s">${sub}</div></div>`;
    el.innerHTML =
      kpi("Tokenized mcap", `$${(totalMcap / 1e9).toFixed(1)}B`,
          st ? `30D ${st.nom30 >= 0 ? "+" : "−"}$${(Math.abs(st.nom30 || 0) / 1e9).toFixed(2)}B (${st.pct30 == null ? "—" : (st.pct30 >= 0 ? "+" : "") + (st.pct30 * 100).toFixed(1) + "%"})` : "—") +
      kpi("RWA protocol TVL", tst ? `$${(tst.cur / 1e9).toFixed(2)}B` : "—",
          tst ? `30D ${(tst.pct30 == null ? "—" : (tst.pct30 >= 0 ? "+" : "") + (tst.pct30 * 100).toFixed(1) + "%")}` : "—") +
      kpi("Protocols", nProtos, "DefiLlama-tracked") +
      kpi("Asset classes", "5", "treasuries · gold · stocks · credit · real estate");
  } catch { /* leave skeleton */ }
}

async function fillRwaClasses(classes, totalMcap) {
  const tb = document.getElementById("rwa-class-rows");
  if (!tb) return;
  const rows = await Promise.all(classes.map(async (c) => {
    let st = null, spark = "";
    try {
      const s = await getSeries(`rwa-mcap-class-${c.class}`, "max");
      const pts = (s.points ?? []).map(([d, v]) => [d, v]).sort((a, b) => a[0] < b[0] ? -1 : 1);
      st = rwaStats(pts);
      if (pts.length > 30) spark = rangeCells(statsFromValues(pts.map(([, v]) => v)), "1Y history");
    } catch { /* no history yet */ }
    const share = totalMcap ? (c.total_mcap / totalMcap) : null;
    return `<tr>
      <td><b>${esc(c.label)}</b></td>
      <td class="num"><b>$${(c.total_mcap / 1e9).toFixed(2)}B</b></td>
      <td class="num">${share == null ? "—" : (share * 100).toFixed(1) + "%"}</td>
      <td class="num"${heatStyle({ pct: st?.pct30 ?? null })}>${st ? rwaDeltaCell(st.nom30, st.pct30) : '<span class="muted">accumulating</span>'}</td>
      ${spark || `<td colspan="4" class="muted">—</td>`}
    </tr>`;
  }));
  tb.innerHTML = rows.join("");
}
