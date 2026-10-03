import { fmtUsd } from "../fmt.js";
import { enableTouchCursor } from "../chart.js";

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

let defiView = "vaults"; // "vaults" | "markets" — in-memory, default VAULTS (spec §3)

const VIEW_TITLES = { vaults: "CURATED VAULTS — USDC", markets: "MORPHO MARKETS — USDC" };

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
    <tr class="tier-head"><td colspan="7">${esc(tier).toUpperCase()}
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
  body.innerHTML = `<table>
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
  } else {
    renderVaults(panel);
  }
}

export function defiFootData(defiPanel, morphoPanel) {
  return defiView === "markets" ? morphoPanel : defiPanel;
}

let curvePlot = null;
let curveObserver = null;

export function renderMidnight(panel) {
  const body = document.querySelector("#panel-midnight .panel-body");
  curveObserver?.disconnect();
  curveObserver = null;
  if (curvePlot) { curvePlot.destroy(); curvePlot = null; } // before innerHTML wipes its root
  if (!panel.rows.length) {
    body.innerHTML = `<div class="empty-state">NO LIVE MARKETS</div>`;
    return;
  }
  body.innerHTML = `<table>
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
    width: Math.max(1, root.clientWidth || 300), height: 240,
    scales: { x: { time: false } },
    series: [
      { label: "DAYS", value: (u, v) => (v == null ? "--" : Math.round(v)) },
      { label: "LEND %", stroke: "#f5a623", width: 1.5, points: { show: true, size: 5 },
        value: (u, v) => (v == null ? "--" : v.toFixed(2)) },
    ],
    axes: [
      { stroke: "#6a746a", grid: { stroke: "#1e261e" } },
      { stroke: "#6a746a", grid: { stroke: "#1e261e" } },
    ],
    // default cursor + legend stay on: hovering reads out DAYS / LEND %
  }, [pts.map((r) => r.days), pts.map((r) => r.lend_apy)], root);
  enableTouchCursor(curvePlot);
  curveObserver = new ResizeObserver(() => {
    if (curvePlot && root.clientWidth > 0) {
      curvePlot.setSize({ width: root.clientWidth, height: 240 });
    }
  });
  curveObserver.observe(root);
}
