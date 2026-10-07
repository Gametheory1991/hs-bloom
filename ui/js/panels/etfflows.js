// ETF FLOWS tab: AUM / NAV / shares-outstanding snapshots from the iShares
// product screener (T+1, keyless) + Yahoo quote fallback for non-iShares
// funds. Flows are derived: shares = AUM / NAV; net_flow(day) =
// (shares_t − shares_{t-1}) × nav_t. Data: /api/dashboard "etfflows" panel
// + /api/series etf:{TICKER}:{aum|nav|shares}.
import { getSeries } from "../api.js";
import { fmtAge } from "../fmt.js";
import { heatStyle } from "../heatmap.js";
import { rangeCells, statsFromValues, RANGE_LEGEND } from "../rangeviz.js";
import { symNameHtml } from "../names.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const seriesCache = {};

async function seriesPts(sid) {
  let pts = seriesCache[sid];
  if (!pts) {
    const j = await getSeries(sid, "max");
    pts = (j.points ?? []).map(([d, v]) => [d, Number(v)]).filter(([, v]) => isFinite(v));
    seriesCache[sid] = pts;
  }
  return pts;
}

function money(v) {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toFixed(0)}`;
}

function signedMoney(v) {
  if (v == null) return "—";
  return (v >= 0 ? "+" : "−") + money(Math.abs(v)).slice(0);
}

/** Net flows from consecutive share snapshots: (shares_t − shares_{t-1}) × nav_t.
 *  Quarterly SEC-XBRL checkpoints (>10d gaps) are skipped — daily flows only. */
async function fundFlows(t) {
  const shares = await seriesPts(`etf:${t}:shares`);
  const nav = await seriesPts(`etf:${t}:nav`);
  if (shares.length < 2) return null;
  const navByD = Object.fromEntries(nav);
  const flows = [];
  for (let i = 1; i < shares.length; i++) {
    const [d, s1] = shares[i];
    const [d0, s0] = shares[i - 1];
    if ((new Date(d) - new Date(d0)) / 86400000 > 10) continue;
    const n = navByD[d] ?? navByD[d0];
    if (n == null) continue;
    flows.push([d, (s1 - s0) * n]);
  }
  return flows;
}

function kpiTile(label, val, sub) {
  return `<div class="kpi"><div class="kpi-label">${label}</div>` +
    `<div class="kpi-value">${val ?? "—"}</div>` +
    `<div class="kpi-sub muted">${sub ?? ""}</div></div>`;
}

async function leagueRow(t, f) {
  const aumPts = await seriesPts(`etf:${t}:aum`);
  const flows = await fundFlows(t);
  const aum = f.aum;
  const vals = aumPts.map(([, v]) => v);
  const s = vals.length >= 3 ? statsFromValues(vals) : { z: null, pct: null };
  const sumLast = (n) => {
    if (!flows || flows.length < n) return null;
    return flows.slice(-n).reduce((a, [, v]) => a + v, 0);
  };
  let f1 = sumLast(1), f5 = sumLast(5), f21 = sumLast(21);
  let wNote = "";
  if (f5 == null && f.source === "coinlaw") {
    // CoinLaw reports weekly flows directly — no shares derivation needed
    const wpts = await seriesPts(`etf:${t}:flow7d`);
    if (wpts.length) { f5 = wpts[wpts.length - 1][1]; wNote = " (reported wk)"; }
  }
  const cell = (v, note = "") => v == null ? `<td class="num muted">—</td>`
    : `<td class="num"${heatStyle({ z: v === 0 ? 0 : (v > 0 ? 1.5 : -1.5) })} title="${esc(note)}"><b>${signedMoney(v)}</b>${note ? `<span class="muted">${esc(note)}</span>` : ""}</td>`;
  return `<tr><td class="sym">${symNameHtml(t)}</td>` +
    `<td class="muted">${esc(CLASS_LABELS_UI[f.class] ?? f.class ?? "")}</td>` +
    `<td class="num"><b>${money(aum)}</b></td>` +
    `${cell(f1)}${cell(f5, wNote)}${cell(f21)}` +
    `<td class="num">${f.shares == null ? "—" : (f.shares / 1e6).toFixed(1) + "M"}</td>` +
    `${rangeCells({ z: s.z, pct: s.pct }, "AUM history")}` +
    `<td class="muted">${esc(f.source ?? "")}</td></tr>`;
}

function flowsChart(flows, title) {
  // flows: [[date, $], ...] aggregate daily
  if (!flows || flows.length < 2) return `<div class="muted">Not enough history yet — flows accumulate daily from the first run.</div>`;
  const W = 720, H = 240, padL = 60, padR = 12, padT = 14, padB = 30;
  const mx = Math.max(...flows.map(([, v]) => Math.abs(v)), 1);
  const X = (i) => padL + (i / (flows.length - 1)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v + mx) / (2 * mx)) * (H - padT - padB);
  const bw = Math.max(1.5, (W - padL - padR) / flows.length - 1);
  let s = `<div class="panel-subhead"><span>${esc(title)}</span></div>`;
  s += `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:760px;display:block" role="img">`;
  s += `<line x1="${padL}" y1="${Y(0)}" x2="${W - padR}" y2="${Y(0)}" stroke="#9aa4b2" stroke-width="1"/>`;
  flows.forEach(([d, v], i) => {
    const x = X(i) - bw / 2;
    const y0 = Y(0), y1 = Y(v);
    s += `<rect x="${x.toFixed(1)}" y="${Math.min(y0, y1).toFixed(1)}" width="${bw}" height="${Math.abs(y1 - y0).toFixed(1)}" fill="${v >= 0 ? "#4f9cf0" : "#e05c5c"}"><title>${d}: ${signedMoney(v)}</title></rect>`;
  });
  s += `<text x="${padL}" y="${H - 10}" font-size="10" fill="#9aa4b2">${flows[0][0]}</text>`;
  s += `<text x="${W - padR - 62}" y="${H - 10}" font-size="10" fill="#9aa4b2">${flows[flows.length - 1][0]}</text>`;
  s += `</svg>`;
  return s;
}

let etfClass = "all"; // asset-class filter

const CLASS_ORDER = ["equity", "fixedincome", "privcredit", "commodity", "crypto", "realestate", "other"];
const CLASS_LABELS_UI = {
  equity: "Equities", fixedincome: "Fixed Income", privcredit: "Private Credit",
  commodity: "Commodities", crypto: "Crypto", realestate: "Real Estate", other: "Other",
};

export async function renderEtfFlows(doc) {
  const body = document.querySelector("#panel-etfflows .panel-body");
  if (!body) return;
  const d = doc || {};
  const funds = d.funds || {};
  const tickers = Object.keys(funds);
  if (!tickers.length) {
    body.innerHTML = `<div class="muted">No ETF data yet — the ishares_etf job runs daily.</div>`;
    return;
  }

  const classes = [...new Set(tickers.map((t) => funds[t].class || "other"))]
    .sort((a, b) => CLASS_ORDER.indexOf(a) - CLASS_ORDER.indexOf(b));
  const filterRow = `<div class="btnrow" id="etf-cls-row">` +
    [`<button type="button" data-etf-cls="all" class="${etfClass === "all" ? "active" : ""}">All</button>`,
     ...classes.map((c) =>
       `<button type="button" data-etf-cls="${c}" class="${etfClass === c ? "active" : ""}">${CLASS_LABELS_UI[c] ?? c}</button>`)].join("") +
    `</div>`;

  const pending = d.pending || [];
  const pendingNote = pending.length
    ? `<div class="muted">Pending issuer-direct coverage (${pending.length}): ${pending.map(esc).join(", ")} — shown once their feeds land.</div>`
    : "";

  body.innerHTML =
    `<div class="panel-subhead"><span>ETF FLOWS — AUM & CREATIONS/REDEMPTIONS <span class="muted">as of ${esc(d.asof ?? "—")} · iShares T+1 · CoinLaw weekly (crypto)</span></span></div>` +
    filterRow + pendingNote +
    `<div class="kpirow" id="etf-kpis"><div class="muted">Computing flows…</div></div>` +
    `<div id="etf-chart"><div class="muted">Loading…</div></div>` +
    `<div class="panel-subhead"><span>LEAGUE TABLE <span class="muted">${etfClass === "all" ? "all classes" : CLASS_LABELS_UI[etfClass] ?? etfClass} · flows = Δshares × NAV (derived) or reported weekly (CoinLaw) · heat = inflow blue / outflow red</span></span></div>` +
    `<div style="overflow-x:auto"><table class="etftable" data-sortable><thead><tr>` +
    `<th>ETF</th><th>Class</th><th>AUM</th><th>1D Flow</th><th>1W Flow</th><th>1M Flow</th><th>Shares Out</th>` +
    `<th data-sort="off">AUM Range<br>${RANGE_LEGEND}</th><th>Src</th></tr></thead>` +
    `<tbody id="etf-league"><tr><td colspan="9" class="muted">Loading…</td></tr></tbody></table></div>` +
    `<div class="muted">${esc(d.note ?? "")}</div>`;

  body.querySelectorAll("[data-etf-cls]").forEach((b) =>
    b.addEventListener("click", () => { etfClass = b.dataset.etfCls; renderEtfFlows(doc); }));

  const shown = etfClass === "all" ? tickers
    : tickers.filter((t) => (funds[t].class || "other") === etfClass);

  // per-fund flows (async, cached series)
  const per = await Promise.all(shown.map(async (t) => ({ t, flows: await fundFlows(t) })));

  // aggregate daily flows
  const byD = {};
  for (const { flows } of per) {
    if (!flows) continue;
    for (const [dd, v] of flows) byD[dd] = (byD[dd] || 0) + v;
  }
  const agg = Object.entries(byD).sort(([a], [b]) => (a < b ? -1 : 1));
  const f1 = agg.length ? agg[agg.length - 1][1] : null;
  const f5 = agg.length >= 5 ? agg.slice(-5).reduce((a, [, v]) => a + v, 0) : null;

  const shownAum = shown.reduce((a, t) => a + (funds[t].aum || 0), 0);
  const clsTitle = etfClass === "all" ? "ALL TRACKED ETFs" : (CLASS_LABELS_UI[etfClass] ?? etfClass).toUpperCase();
  const kpis =
    kpiTile("AUM Tracked", money(shownAum), `${shown.length} ETFs`) +
    kpiTile("Net Flow 1D", f1 == null ? "—" : signedMoney(f1), agg.length ? agg[agg.length - 1][0] : "") +
    kpiTile("Net Flow 1W", f5 == null ? "—" : signedMoney(f5), "trailing 5 sessions");

  const kc = document.getElementById("etf-kpis");
  if (kc) kc.innerHTML = kpis;
  const ch = document.getElementById("etf-chart");
  if (ch) ch.innerHTML = flowsChart(agg, `AGGREGATE DAILY NET FLOWS — ${clsTitle}`);

  // class rollup (always all classes; rows click to filter)
  const clsAgg = {};
  const allPer = etfClass === "all" ? per
    : await Promise.all(tickers.map(async (t) => ({ t, flows: await fundFlows(t) })));
  for (const { t, flows } of allPer) {
    if (!flows) continue;
    const c = funds[t].class || "other";
    for (const [dd, v] of flows) {
      clsAgg[c] = clsAgg[c] || {};
      clsAgg[c][dd] = (clsAgg[c][dd] || 0) + v;
    }
  }
  const clsRows = Object.entries(clsAgg)
    .sort(([a], [b]) => CLASS_ORDER.indexOf(a) - CLASS_ORDER.indexOf(b))
    .map(([c, dd]) => {
      const arr = Object.entries(dd).sort(([a], [b]) => (a < b ? -1 : 1));
      const w = arr.length >= 5 ? arr.slice(-5).reduce((a, [, v]) => a + v, 0) : null;
      const clsAum = tickers.filter((t) => (funds[t].class || "other") === c)
        .reduce((a, t) => a + (funds[t].aum || 0), 0);
      const label = CLASS_LABELS_UI[c] ?? c;
      return `<tr data-cls="${c}" style="cursor:pointer" title="Filter to ${esc(label)}">` +
        `<td><b>${esc(label)}</b>${etfClass === c ? ' <span class="muted">●</span>' : ""}</td>` +
        `<td class="num"><b>${money(clsAum)}</b></td>` +
        `<td class="num"${heatStyle({ z: w == null || w === 0 ? 0 : (w > 0 ? 1.5 : -1.5) })}><b>${w == null ? "—" : signedMoney(w)}</b></td></tr>`;
    }).join("");

  const tb = document.getElementById("etf-league");
  if (tb) {
    const rows = await Promise.all(shown.map((t) => leagueRow(t, funds[t])));
    tb.innerHTML = rows.join("");
  }
  if (ch) {
    ch.innerHTML +=
      `<div class="panel-subhead"><span>BY ASSET CLASS <span class="muted">1W net flows · click a row to filter</span></span></div>` +
      `<div style="overflow-x:auto"><table class="etftable"><thead><tr><th>Class</th><th>AUM</th><th>1W Flow</th></tr></thead>` +
      `<tbody>${clsRows}</tbody></table></div>`;
    ch.querySelectorAll("[data-cls]").forEach((r) =>
      r.addEventListener("click", () => {
        etfClass = etfClass === r.dataset.cls ? "all" : r.dataset.cls;
        renderEtfFlows(doc);
      }));
  }

  const foot = document.querySelector("#panel-etfflows .panel-foot");
  if (foot) foot.textContent = `DATA: ISHARES T+1 / YAHOO / COINLAW · ${fmtAge(d.updated_at)}`;
}
