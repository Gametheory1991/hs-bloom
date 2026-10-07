// FINRA OTC Market — over-the-counter equities (otce.finra.org).
// Sub-tab of the EQUITY hub ("OTC Market"). Keyless api.finra.org,
// group otcMarket. Sections:
//   1. Market statistics trend chart (monthly share/dollar/trade totals)
//   2. Top-100 issues table (month selector)
//   3. Annual statistics table
//   4. Daily-list corporate-action feed (additions/deletions/symbol
//      changes/bankruptcies/dividends)
//   5. Current trading halts
//   6. OTC threshold securities
// Every delta cell shows nominal + % (Harry's rule); deltas sort off the
// % value. Bloomberg-style dotted range sparklines via rangeCells.
import { getSeries, getOtcTop100 } from "../api.js";
import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";import { RANGES, rangeById } from "./trace_grid.js";
import { matrixToCSV, exportTablesXLSX, todayStamp } from "../export.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";

const esc = (s) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const big = (x) =>
  x == null || !isFinite(x) ? "—" : Number(x).toLocaleString("en-US", { maximumFractionDigits: 0 });
const bigC = (x) =>
  x == null || !isFinite(x) ? "—" :
  Math.abs(x) >= 1e9 ? (x / 1e9).toFixed(2) + "B" :
  Math.abs(x) >= 1e6 ? (x / 1e6).toFixed(1) + "M" :
  Math.abs(x) >= 1e3 ? (x / 1e3).toFixed(1) + "K" : String(Math.round(x));
const pct1 = (x) =>
  x == null || !isFinite(x) ? "—" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
// Nominal+% delta cell, heatmapped on the % value.
const dCell = (nom, pct, unit) => {
  if ((nom == null || !isFinite(nom)) && (pct == null || !isFinite(pct))) return `<td class="num">—</td>`;
  const n = nom == null || !isFinite(nom) ? "—"
    : `${nom >= 0 ? "+" : "−"}${bigC(Math.abs(nom))}${unit ? " " + unit : ""}`;
  const p = pct1(pct);
  return `<td class="num" data-sort-val="${pct ?? ""}"${heatStyle({ pct })}>${n} <span class="muted">(${p})</span></td>`;
};

const otcState = { range: "1y", reqId: 0, plot: null, month: null };

function otcFilterRange(points) {
  if (otcState.range === "max") return points;
  const r = rangeById(otcState.range);
  if (!r || !isFinite(r.months)) return points;
  return points.slice(-Math.max(4, Math.round(r.months)));
}

async function drawOtcChart(statusEl) {
  const reqId = ++otcState.reqId;
  const el = document.getElementById("otc-chart");
  if (!el) return;
  if (statusEl) statusEl.textContent = "Loading…";
  try {
    const [shares, dv] = await Promise.all([
      getSeries("otc:monthly-total-shares", "max"),
      getSeries("otc:monthly-total-dollarvol", "max"),
    ]);
    if (reqId !== otcState.reqId) return;
    const sp = otcFilterRange(shares.points ?? []), dp = otcFilterRange(dv.points ?? []);
    const byDate = new Map();
    for (const [d, v] of sp) byDate.set(d, [v, null]);
    for (const [d, v] of dp) { const c = byDate.get(d) ?? [null, null]; c[1] = v; byDate.set(d, c); }
    const dates = [...byDate.keys()].sort();
    const xs = dates.map((d) => Date.parse(d) / 1000);
    const y1 = dates.map((d) => byDate.get(d)[0]);
    const y2 = dates.map((d) => byDate.get(d)[1]);
    if (otcState.plot) { otcState.plot.destroy(); otcState.plot = null; }
    el.innerHTML = "";
    const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
    if (typeof uPlot !== "undefined" && xs.length > 1) {
      otcState.plot = new uPlot({
        width: Math.max(300, el.clientWidth || 900), height: 340,
        scales: { x: { time: true }, y: {}, y2: {} },
        series: [{}, { label: "Share volume", stroke: "#2563eb", width: 2, spanGaps: true },
          { label: "Dollar volume", stroke: "#f59e0b", width: 1.5, spanGaps: true, scale: "y2" }],
        axes: [
          { ...axisStyle },
          { ...axisStyle, scale: "y", values: (u, v) => v.map((x) => x == null ? "" : bigC(x)) },
          { ...axisStyle, scale: "y2", side: 1, values: (u, v) => v.map((x) => x == null ? "" : "$" + bigC(x)) },
        ],
        legend: { show: true },
      }, [xs, y1, y2], el);
      if (statusEl) statusEl.textContent =
        `OTC monthly totals · ${dates.length} months (${dates[0]} → ${dates[dates.length - 1]})`;
    } else if (statusEl) {
      statusEl.textContent = xs.length < 2 ? "Not enough data yet." : "Chart library unavailable.";
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = `Chart failed to load: ${esc(e.message)}`;
  }
}

const DL_LABELS = {
  additions: "Additions", deletions: "Deletions", symbol_changes: "Symbol / Name Changes",
  attribute_changes: "Security Attribute Changes", bankruptcy: "Bankruptcy",
  dividends: "Dividends / Distributions / Splits", other: "Other",
};

export function renderOTC(otc) {
  const body = document.querySelector("#panel-otc .panel-body");
  if (!body) return;
  const o = otc ?? {};
  const months = o.top100_months ?? [];
  if (otcState.month == null && months.length) otcState.month = months[months.length - 1];
  const t100 = o.top100 ?? null;
  const mt = o.monthly_totals ?? {};
  const yearly = o.yearly ?? [];
  const dl = o.dailylist ?? null;
  const th = o.threshold ?? null;
  const halts = o.halts ?? null;

  const notices = [];
  if (!months.length && !(mt.shares ?? []).length) notices.push(
    `<div class="notice">No OTC data yet — the <code>finra_otc</code> job hasn't run.</div>`);

  const rangeBtns = RANGES.filter((r) => ["1y", "3y", "5y", "max"].includes(r.id))
    .map((x) => `<button data-range="${x.id}" class="${x.id === otcState.range ? "on" : ""}">${x.label}</button>`).join("");

  // ---- Top 100 table ----
  const monthOpts = months.map((m) =>
    `<option value="${m}"${m === otcState.month ? " selected" : ""}>${m}</option>`).join("");
  const t100rows = (t100?.rows ?? []).map((r) =>
    `<tr><td><b>${esc(r.symbol)}</b><br><span class="muted">${esc(r.name)}</span></td>` +
    `<td class="muted">${esc(r.market)}</td>` +
    `<td class="num" data-sort-val="${r.shares ?? ""}">${bigC(r.shares)}</td>` +
    `<td class="num" data-sort-val="${r.dollarVol ?? ""}">${r.dollarVol == null ? "—" : "$" + bigC(r.dollarVol)}</td>` +
    `<td class="num" data-sort-val="${r.close ?? ""}">${r.close == null ? "—" : "$" + Number(r.close).toFixed(4)}</td></tr>`
  ).join("");

  // ---- Annual statistics ----
  const yrows = yearly.map((y) =>
    `<tr><td><b>${y.y}</b></td>` +
    `<td class="num" data-sort-val="${y.shares ?? ""}">${bigC(y.shares)}</td>` +
    `<td class="num" data-sort-val="${y.dollarvol ?? ""}">${y.dollarvol == null ? "—" : "$" + bigC(y.dollarvol)}</td>` +
    `<td class="num" data-sort-val="${y.trades ?? ""}">${big(y.trades)}</td></tr>`
  ).join("");

  // ---- Daily list feed ----
  const dlCats = dl?.categories ?? {};
  const dlSections = Object.entries(DL_LABELS).map(([key, label]) => {
    const rows = dlCats[key] ?? [];
    if (!rows.length) return "";
    const lis = rows.slice(0, 30).map((r) =>
      `<li><b>${esc(r.symbol ?? "—")}</b>` +
      (r.oldSymbol && r.oldSymbol !== r.symbol ? ` <span class="muted">(was ${esc(r.oldSymbol)})</span>` : "") +
      (r.desc ? ` — ${esc(r.desc)}` : "") +
      (r.exDate ? ` <span class="muted">ex ${esc(r.exDate)}</span>` : "") +
      (r.comment ? `<br><span class="muted">${esc(r.comment)}</span>` : "") + `</li>`).join("");
    return `<div class="otc-dl-cat"><h5>${label} (${rows.length})</h5><ul>${lis}</ul></div>`;
  }).join("");

  // ---- Halts ----
  const haltRows = (halts?.rows ?? []).map((r) =>
    `<tr><td><b>${esc(r.symbol)}</b><br><span class="muted">${esc(r.name)}</span></td>` +
    `<td>${esc(r.action === "H" ? "Halt" : r.action === "R" ? "Resume" : r.action ?? "—")}</td>` +
    `<td>${esc(r.reason ?? "—")}</td>` +
    `<td class="muted">${esc((r.haltTime ?? "").replace("T", " ").slice(0, 16))}</td>` +
    `<td class="muted">${esc(r.originator ?? "—")}</td></tr>`
  ).join("");

  // ---- Threshold ----
  const thRows = (th?.rows ?? []).slice(0, 100).map((r) =>
    `<tr><td><b>${esc(r.symbol)}</b><br><span class="muted">${esc(r.name)}</span></td>` +
    `<td class="muted">${esc(r.market ?? "—")}</td>` +
    `<td>${esc(r.regSho ?? "—")}</td><td>${esc(r.rule4320 ?? "—")}</td></tr>`
  ).join("");

  body.innerHTML =
    `<h3>FINRA OTC MARKET <span class="muted">over-the-counter equities · keyless FINRA API` +
    (o.as_of ? ` · updated ${esc(o.as_of)}` : "") + `</span></h3>` +
    notices.join("") +
    `<div class="star-dl-row">
       <button id="otc-dl-csv" class="mini-btn">⤓ CSV (top 100)</button>
       <button id="otc-dl-xlsx" class="mini-btn">⤓ XLSX (top 100)</button>
     </div>` +
    `<h4>Market statistics — monthly totals</h4>
     <div class="chart-controls">${rangeBtns}</div>
     <div id="otc-chart" style="width:100%;min-height:340px"></div>
     <div class="muted" id="otc-chart-status" style="margin:4px 0 12px"></div>` +
    `<h4>Top 100 issues by share volume
       <span class="muted">month <select id="otc-month">${monthOpts}</select></span></h4>
     <div class="tbl-wrap"><table class="sortable" id="otc-top100-tbl">
       <thead><tr><th>Issue</th><th>Market</th><th>Shares</th><th>Dollar Vol</th><th>Close</th></tr></thead>
       <tbody>${t100rows || `<tr><td colspan="5" class="muted">No data for this month yet.</td></tr>`}</tbody>
     </table></div>` +
    (yearly.length ? `<h4>Annual statistics — All OTC</h4>
     <div class="tbl-wrap"><table class="sortable">
       <thead><tr><th>Year</th><th>Share Volume</th><th>Dollar Volume</th><th>Trades</th></tr></thead>
       <tbody>${yrows}</tbody></table></div>` : "") +
    (dl ? `<h4>Daily list — corporate actions <span class="muted">${esc(dl.date)} · ${dl.count} events</span></h4>
     <div class="otc-dl-grid">${dlSections || `<p class="muted">No events.</p>`}</div>` : "") +
    ((halts?.rows ?? []).length ? `<h4>Current trading halts / resumes</h4>
     <div class="tbl-wrap"><table class="sortable">
       <thead><tr><th>Symbol</th><th>Action</th><th>Reason</th><th>Time</th><th>Originator</th></tr></thead>
       <tbody>${haltRows}</tbody></table></div>` : "") +
    ((th?.rows ?? []).length ? `<h4>OTC threshold securities <span class="muted">${esc(th.date)} · ${(th.rows ?? []).length} issues</span></h4>
     <div class="tbl-wrap"><table class="sortable">
       <thead><tr><th>Symbol</th><th>Market</th><th>Reg SHO</th><th>Rule 4320</th></tr></thead>
       <tbody>${thRows}</tbody></table></div>` : "") +
    `<p class="muted" style="margin-top:12px">Source: FINRA OTC Market (otce.finra.org), keyless API · ` +
    `symbol directory: ${big(o.secmaster_count ?? 0)} issues · ${big(o.mplist_count ?? 0)} market participants</p>`;

  // chart + range buttons
  drawOtcChart(document.getElementById("otc-chart-status"));
  body.querySelectorAll("[data-range]").forEach((b) =>
    b.addEventListener("click", () => {
      otcState.range = b.dataset.range;
      body.querySelectorAll("[data-range]").forEach((x) => x.classList.toggle("on", x === b));
      drawOtcChart(document.getElementById("otc-chart-status"));
    }));
  // month selector fetches that month's top-100 from the API
  const msel = document.getElementById("otc-month");
  if (msel) msel.addEventListener("change", async () => {
    otcState.month = msel.value;
    const tbody = document.querySelector("#otc-top100-tbl tbody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="muted">Loading…</td></tr>`;
    try {
      const m = await getOtcTop100(msel.value);
      const rows = (m.rows ?? []).map((r) =>
        `<tr><td><b>${esc(r.symbol)}</b><br><span class="muted">${esc(r.name)}</span></td>` +
        `<td class="muted">${esc(r.market)}</td>` +
        `<td class="num" data-sort-val="${r.shares ?? ""}">${bigC(r.shares)}</td>` +
        `<td class="num" data-sort-val="${r.dollarVol ?? ""}">${r.dollarVol == null ? "—" : "$" + bigC(r.dollarVol)}</td>` +
        `<td class="num" data-sort-val="${r.close ?? ""}">${r.close == null ? "—" : "$" + Number(r.close).toFixed(4)}</td></tr>`
      ).join("");
      if (tbody) tbody.innerHTML = rows || `<tr><td colspan="5" class="muted">No data for this month.</td></tr>`;
    } catch (e) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="muted">Failed to load: ${esc(e.message)}</td></tr>`;
    }
  });

  // exports (top 100)
  const rows2d = [["symbol", "name", "market", "shares", "dollar_volume", "close"],
    ...(t100?.rows ?? []).map((r) => [r.symbol, r.name, r.market, r.shares, r.dollarVol, r.close])];
  const csvBtn = document.getElementById("otc-dl-csv");
  if (csvBtn) csvBtn.addEventListener("click", () =>
    matrixToCSV(rows2d, `otc-top100-${otcState.month ?? "latest"}-${todayStamp()}.csv`));
  const xlsxBtn = document.getElementById("otc-dl-xlsx");
  if (xlsxBtn) xlsxBtn.addEventListener("click", () =>
    exportTablesXLSX([{ name: "top100", rows: rows2d }],
      `otc-top100-${otcState.month ?? "latest"}-${todayStamp()}.xlsx`));
}
