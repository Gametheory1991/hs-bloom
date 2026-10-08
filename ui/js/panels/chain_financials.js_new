// Blockchain Financials sub-tab: per-company three-statement quarterly trends +
// cross-universe comparison table. Latest snapshot comes from the
// chain_financials panel doc; quarterly history is fetched on demand via
// /api/series crypto:{TICKER}:{metric} (quarterly XBRL series).
import { getSeries } from "../api.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const MONEY_METRICS = ["revenue", "gross_profit", "op_income", "net_income",
  "rd", "assets", "cash", "total_debt", "equity", "ocf", "capex", "fcf"];
const RATIO_METRICS = ["gross_margin", "op_margin", "net_margin",
  "rd_intensity", "fcf_margin", "capex_intensity"];

const BLUE = "#2563eb", CYAN = "#0891b2", GREEN = "#059669",
  ORANGE = "#ea580c", PURPLE = "#7c3aed";

const fmtMoney = (v) => {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e12) return `${s}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(0)}M`;
  return `${s}$${a.toFixed(0)}`;
};
const fmtPct = (v) => (v == null || !isFinite(v) ? "—" : `${(v * 100).toFixed(1)}%`);
const fmtYoy = (v) => {
  if (v == null || !isFinite(v)) return "—";
  const s = v > 0 ? "+" : v < 0 ? "-" : "";
  return `${s}${(Math.abs(v) * 100).toFixed(1)}% YoY`;
};
const qLabel = (iso) => {
  const p = String(iso).split("-");
  if (p.length < 2) return iso;
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(p[1]) - 1] || "";
  return `${mon} '${String(p[0]).slice(2)}`;
};
const fullQLabel = (iso) => {
  const p = String(iso).split("-");
  if (p.length < 3) return iso;
  const mon = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November",
    "December"][Number(p[1]) - 1] || "";
  return `${mon} ${Number(p[2])}, ${p[0]}`;
};

function lineChart(el, dates, defs, yFmt) {
  el.innerHTML = "";
  if (!dates || dates.length < 2) {
    el.innerHTML = `<span class="muted">not enough history</span>`;
    return;
  }
  const xs = dates.map((d) => new Date(d + "T00:00:00Z").getTime() / 1000);
  const axis = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
  const yAxis = yFmt ? { ...axis, values: (u, vals) => vals.map(yFmt) } : axis;
  new uPlot({
    width: Math.max(280, el.clientWidth || 640),
    height: 220,
    legend: { show: true },
    series: [{},
      ...defs.map((s) => ({
        label: s.label, stroke: s.color, width: 1.6, spanGaps: true,
      }))],
    axes: [{ ...axis,
      values: (u, vals) => vals.map((v) => { const d = new Date(v * 1000); return qLabel(d.toISOString().slice(0, 10)); }),
    }, yAxis],
  }, [xs, ...defs.map((s) => dates.map((d) => s.byDate[d] ?? null))], el);
}

async function fetchHist(ticker, metrics) {
  const out = {};
  await Promise.all(metrics.map(async (m) => {
    try {
      const r = await getSeries(`crypto:${ticker}:${m}`, "max");
      const byDate = {};
      for (const [d, v] of (r.points || [])) byDate[d] = v;
      out[m] = byDate;
    } catch (e) { out[m] = {}; } // eslint-disable-line no-unused-vars
  }));
  return out;
}

function kpiTile(label, val, sub) {
  return `<div class="kpi"><div class="kpi-label">${esc(label)}</div>` +
    `<div class="kpi-val">${val}</div><div class="kpi-sub muted">${sub ?? ""}</div></div>`;
}

const STMT_GROUPS = [
  ["INCOME STATEMENT", [["revenue", "Revenue"], ["gross_profit", "Gross profit"],
    ["op_income", "Operating income"], ["net_income", "Net income"], ["rd", "R&D expense"]]],
  ["BALANCE SHEET", [["assets", "Total assets"], ["cash", "Cash & equivalents"],
    ["total_debt", "Total debt"], ["equity", "Stockholders' equity"]]],
  ["CASH FLOW", [["ocf", "Operating cash flow"], ["capex", "Capex"], ["fcf", "Free cash flow"]]],
];

export async function renderChainFinancials(finDoc, slot) {
  const cos = (finDoc && finDoc.companies) || {};
  let tickers = Object.keys(cos);
  if (!tickers.length) {
    slot.innerHTML = `<div class="empty-state">NO DATA — the crypto_capex XBRL job has not run yet</div>`;
    return;
  }
  tickers.sort((a, b) => (cos[b].revenue || 0) - (cos[a].revenue || 0));
  let sel = tickers.includes("COIN") ? "COIN" : tickers[0];

  slot.innerHTML = `
    <div class="panel-subhead"><span>BLOCKCHAIN FINANCIALS — THREE-STATEMENT TRACKER
      <span class="muted">SEC EDGAR XBRL companyfacts · quarterly · ~45-day filing lag · private/offshore players (Tether, Binance) have no public financials — shown in the Universe Map with honest no-data state</span></span></div>
    <div style="display:flex;gap:8px;align-items:center;margin:8px 0;flex-wrap:wrap">
      <label class="muted" for="chainfin-co">Company</label>
      <select id="chainfin-co" style="max-width:280px">
        ${tickers.map((t) => `<option value="${esc(t)}"${t === sel ? " selected" : ""}>${esc(t)} — ${esc(cos[t].name || "")}</option>`).join("")}
      </select>
      <span class="muted" id="chainfin-lq"></span>
    </div>
    <div class="kpi-grid" id="chainfin-kpis" style="margin-bottom:12px"></div>
    <div class="panel-subhead"><span>QUARTERLY TRENDS <span class="muted" id="chainfin-trange"></span></span></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;margin-bottom:12px">
      <div><div class="muted" style="margin-bottom:4px">Revenue, net income, FCF ($)</div><div id="chainfin-c1"></div></div>
      <div><div class="muted" style="margin-bottom:4px">Capex vs operating cash flow ($) — miner fleet expansion</div><div id="chainfin-c2"></div></div>
      <div><div class="muted" style="margin-bottom:4px">Margins (%)</div><div id="chainfin-c3"></div></div>
    </div>
    <div class="panel-subhead"><span>STATEMENTS — LAST 8 QUARTERS <span class="muted">as reported (fiscal Q4 derived where needed)</span></span></div>
    <div style="overflow-x:auto;margin-bottom:12px"><table class="data-table" id="chainfin-stmt"></table></div>
    <div class="panel-subhead"><span>UNIVERSE COMPARISON — LATEST REPORTED QUARTER <span class="muted">click a row to inspect</span></span></div>
    <div style="overflow-x:auto"><table class="data-table" id="chainfin-cmp"></table></div>
    <div class="muted" style="margin-top:8px;font-size:11px">BTC holdings (treasury cos) and hash rate (miners) are not consistently XBRL-tagged and are not shown — labeled unavailable rather than guessed. Token issuers are mostly private (no SEC filings). Values in USD as filed. Related coverage: crypto ETFs (ETF Flows tab), tokenized securities venues (TSV), tokenized RWA (Digital).</div>`;

  const cmpEl = slot.querySelector("#chainfin-cmp");
  const cmpRows = tickers.map((t) => {
    const c = cos[t];
    return `<tr data-t="${esc(t)}" style="cursor:pointer">
      <td class="sym"><strong>${esc(t)}</strong></td><td>${esc(c.name || "")}</td>
      <td class="muted">${esc(c.vertical || "")}</td>
      <td>${qLabel(c.latest_quarter || "")}</td>
      <td class="num">${fmtMoney(c.revenue)}</td>
      <td class="num">${fmtYoy(c.rev_yoy)}</td>
      <td class="num">${fmtPct(c.net_margin)}</td>
      <td class="num">${fmtPct(c.fcf_margin)}</td>
      <td class="num">${fmtMoney(c.capex)}</td>
      <td class="num">${fmtPct(c.capex_intensity)}</td>
      <td class="num">${c.debt_to_equity == null ? "—" : c.debt_to_equity.toFixed(2) + "x"}</td></tr>`;
  }).join("");
  cmpEl.innerHTML = `<thead><tr><th>Ticker</th><th>Company</th><th>Vertical</th><th>Latest</th>` +
    `<th class="num">Revenue</th><th class="num">Rev YoY</th><th class="num">Net margin</th>` +
    `<th class="num">FCF margin</th><th class="num">Capex</th><th class="num">Capex/rev</th>` +
    `<th class="num">D/E</th></tr></thead><tbody>${cmpRows}</tbody>`;
  cmpEl.querySelectorAll("tr[data-t]").forEach((r) =>
    r.addEventListener("click", () => {
      sel = r.dataset.t;
      slot.querySelector("#chainfin-co").value = sel;
      load();
    }));

  async function load() {
    const c = cos[sel] || {};
    slot.querySelector("#chainfin-lq").textContent =
      `latest quarter ended ${fullQLabel(c.latest_quarter || "")} · ${c.quarters_count || "?"} quarters of history`;
    slot.querySelector("#chainfin-kpis").innerHTML =
      kpiTile("Revenue", fmtMoney(c.revenue), fmtYoy(c.rev_yoy)) +
      kpiTile("Net income", fmtMoney(c.net_income), fmtYoy(c.ni_yoy)) +
      kpiTile("Free cash flow", fmtMoney(c.fcf), `margin ${fmtPct(c.fcf_margin)}`) +
      kpiTile("Capex", fmtMoney(c.capex), `${fmtYoy(c.capex_yoy)} · ${fmtPct(c.capex_intensity)} of rev`) +
      kpiTile("Net debt", fmtMoney(c.net_debt), `D/E ${c.debt_to_equity == null ? "—" : c.debt_to_equity.toFixed(2) + "x"}`) +
      kpiTile("Equity", fmtMoney(c.equity), `assets ${fmtMoney(c.assets)}`);

    const hist = await fetchHist(sel, [...MONEY_METRICS, ...RATIO_METRICS]);
    const dates = [...new Set(Object.values(hist).flatMap((m) => Object.keys(m)))].sort().slice(-12);
    slot.querySelector("#chainfin-trange").textContent =
      dates.length ? `${qLabel(dates[0])} to ${qLabel(dates[dates.length - 1])}` : "";
    lineChart(slot.querySelector("#chainfin-c1"), dates, [
      { label: "Revenue", color: BLUE, byDate: hist.revenue || {} },
      { label: "Net income", color: GREEN, byDate: hist.net_income || {} },
      { label: "FCF", color: CYAN, byDate: hist.fcf || {} },
    ], (u, vals) => vals.map((v) => v == null ? "" : fmtMoney(v)));
    lineChart(slot.querySelector("#chainfin-c2"), dates, [
      { label: "Capex", color: ORANGE, byDate: hist.capex || {} },
      { label: "OCF", color: BLUE, byDate: hist.ocf || {} },
    ]);
    lineChart(slot.querySelector("#chainfin-c3"), dates, [
      { label: "Gross margin", color: BLUE, byDate: hist.gross_margin || {} },
      { label: "Op margin", color: PURPLE, byDate: hist.op_margin || {} },
      { label: "Net margin", color: GREEN, byDate: hist.net_margin || {} },
    ], (u, vals) => vals.map((v) => v == null ? "" : `${(v * 100).toFixed(0)}%`));

    const sdates = dates.slice(-8);
    let stmt = `<thead><tr><th></th>${sdates.map((d) => `<th class="num">${qLabel(d)}</th>`).join("")}</tr></thead><tbody>`;
    for (const [g, rows] of STMT_GROUPS) {
      stmt += `<tr class="grp"><td colspan="${sdates.length + 1}"><strong>${g}</strong></td></tr>`;
      for (const [m, label] of rows) {
        const bd = hist[m] || {};
        stmt += `<tr><td>${esc(label)}</td>${sdates.map((d) =>
          `<td class="num">${fmtMoney(bd[d])}</td>`).join("")}</tr>`;
      }
    }
    slot.querySelector("#chainfin-stmt").innerHTML = stmt + "</tbody>";
  }

  slot.querySelector("#chainfin-co").addEventListener("change", (e) => { sel = e.target.value; load(); });
  await load();
}
