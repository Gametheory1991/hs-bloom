// OPTIONS tab: CBOE delayed options chains — GEX, put/call ratios, ATM IV,
// max pain, GEX-by-strike chart, unusual activity. Yahoo v7 fallback flagged.
// Data: /api/dashboard "options" panel + /api/series opt:{SYM}:{metric}.
import { getSeries } from "../api.js";
import { fmtAge } from "../fmt.js";
import { heatStyle } from "../heatmap.js";
import { rangeCells, statsFromValues, RANGE_LEGEND } from "../rangeviz.js";
import { symNameHtml } from "../names.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let optSym = "SPY";
const histCache = {};

const HIST_METRICS = [
  ["gex", "GEX", (v) => `$${v.toFixed(1)}B`, (v) => v.toFixed(2)],
  ["pc_oi", "P/C OI", (v) => v.toFixed(2), (v) => v.toFixed(3)],
  ["pc_vol", "P/C Vol", (v) => v.toFixed(2), (v) => v.toFixed(3)],
  ["atm_iv", "ATM IV", (v) => `${v.toFixed(1)}%`, (v) => v.toFixed(2)],
  ["maxpain", "Max Pain", (v) => `$${Number(v).toFixed(0)}`, (v) => v.toFixed(0)],
];

function kpiTile(label, val, sub) {
  return `<div class="kpi"><div class="kpi-label">${label}</div>` +
    `<div class="kpi-value">${val ?? "—"}</div>` +
    `<div class="kpi-sub muted">${sub ?? ""}</div></div>`;
}

function gexChart(chart, spot) {
  if (!chart || !chart.length) return `<div class="muted">No strike data.</div>`;
  const W = 720, H = 260, padL = 54, padR = 12, padT = 14, padB = 34;
  const mx = Math.max(...chart.map((r) => Math.max(Math.abs(r.call_gex), Math.abs(r.put_gex))), 0.001);
  const lo = Math.min(...chart.map((r) => r.strike));
  const hi = Math.max(...chart.map((r) => r.strike));
  const X = (s) => padL + ((s - lo) / Math.max(hi - lo, 1e-9)) * (W - padL - padR);
  const Y = (v) => padT + (1 - v / mx) * (H - padT - padB);
  const bw = Math.max(2, (W - padL - padR) / chart.length - 2);
  let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:760px;display:block" role="img">`;
  s += `<line x1="${padL}" y1="${Y(0)}" x2="${W - padR}" y2="${Y(0)}" stroke="#9aa4b2" stroke-width="1"/>`;
  for (const r of chart) {
    const x = X(r.strike) - bw / 2;
    if (r.call_gex > 0.0005)
      s += `<rect x="${x.toFixed(1)}" y="${Y(r.call_gex).toFixed(1)}" width="${bw}" height="${(Y(0) - Y(r.call_gex)).toFixed(1)}" fill="#4f9cf0"/>`;
    if (r.put_gex < -0.0005)
      s += `<rect x="${x.toFixed(1)}" y="${Y(0).toFixed(1)}" width="${bw}" height="${(Y(r.put_gex) - Y(0)).toFixed(1)}" fill="#e05c5c"/>`;
  }
  if (spot) {
    const sx = X(Math.min(Math.max(spot, lo), hi));
    s += `<line x1="${sx}" y1="${padT}" x2="${sx}" y2="${H - padB}" stroke="#e3b008" stroke-width="1.5" stroke-dasharray="4 3"/>`;
    s += `<text x="${sx + 4}" y="${padT + 10}" font-size="10" fill="#8a6d1a">spot ${spot.toFixed(0)}</text>`;
  }
  s += `<text x="${padL}" y="${H - 12}" font-size="10" fill="#9aa4b2">${lo.toFixed(0)}</text>`;
  s += `<text x="${W - padR - 30}" y="${H - 12}" font-size="10" fill="#9aa4b2">${hi.toFixed(0)}</text>`;
  s += `<text x="${padL}" y="${padT}" font-size="10" fill="#4f9cf0">■ calls</text>`;
  s += `<text x="${padL + 52}" y="${padT}" font-size="10" fill="#e05c5c">■ puts</text>`;
  s += `</svg>`;
  return s;
}

async function historyRow(sym, metric, label, fmt, fmtD) {
  const sid = `opt:${sym}:${metric}`;
  try {
    let pts = histCache[sid];
    if (!pts) {
      const j = await getSeries(sid, "max");
      pts = (j.points ?? []).map(([d, v]) => [d, Number(v)]).filter(([, v]) => isFinite(v));
      histCache[sid] = pts;
    }
    if (pts.length < 3) return `<tr><td class="sym">${symNameHtml(sym)}</td><td>${label}</td><td colspan="3" class="muted">—</td></tr>`;
    const vals = pts.map(([, v]) => v);
    const s = statsFromValues(vals);
    const cur = vals[vals.length - 1];
    const prev = vals[vals.length - 2];
    const nom = cur - prev;
    const pct = prev !== 0 ? nom / Math.abs(prev) : null;
    const dTxt = `<b>${nom >= 0 ? "+" : "−"}${fmtD(Math.abs(nom))}</b> <span class="muted">(${pct == null ? "—" : (pct >= 0 ? "+" : "−") + Math.abs(pct * 100).toFixed(1) + "%"})</span>`;
    return `<tr><td class="sym">${symNameHtml(sym)}</td><td>${label}</td><td class="num"><b>${fmt(cur)}</b></td>` +
      `<td class="num"${heatStyle({ z: s.z })}>${dTxt}</td>${rangeCells({ z: s.z, pct: s.pct }, "history")}</tr>`;
  } catch {
    return `<tr><td class="sym">${esc(sym)}</td><td>${label}</td><td colspan="3" class="muted">—</td></tr>`;
  }
}

export async function renderOptions(doc) {
  const body = document.querySelector("#panel-options .panel-body");
  if (!body) return;
  const d = doc || {};
  const syms = d.symbols || {};
  const names = Object.keys(syms);
  if (!names.length) {
    body.innerHTML = `<div class="muted">No options data yet — the cboe_options job runs daily after the US close.</div>`;
    return;
  }
  if (!syms[optSym]) optSym = names[0];
  const s = syms[optSym];
  const src = s.source === "yahoo" ? "Yahoo (fallback — no greeks)" : "CBOE 15-min delayed";

  const picker = `<div class="btnrow" id="opt-sym-row">` + names.map((n) =>
    `<button type="button" data-opt-sym="${n}" class="${n === optSym ? "active" : ""}">${n}</button>`).join("") + `</div>`;

  const kpis =
    kpiTile("GEX", s.gex == null ? "—" : `$${s.gex.toFixed(1)}B`, "all expiries · calls + / puts −") +
    kpiTile("Put/Call OI", s.pc_oi == null ? "—" : s.pc_oi.toFixed(2), "open interest") +
    kpiTile("Put/Call Vol", s.pc_vol == null ? "—" : s.pc_vol.toFixed(2), "today's volume") +
    kpiTile("ATM IV", s.atm_iv == null ? "—" : `${s.atm_iv.toFixed(1)}%`, `nearest expiry ${esc(s.near_expiry ?? "")}`) +
    kpiTile("Max Pain", s.maxpain == null ? "—" : `$${s.maxpain.toFixed(0)}`, `expiry ${esc(s.near_expiry ?? "")}`) +
    kpiTile("Spot", s.spot == null ? "—" : `$${s.spot.toFixed(2)}`, `${(s.n_contracts ?? 0).toLocaleString()} contracts`);

  const expNote = `<div class="muted">Nearest expiry: <b>${esc(s.near_expiry ?? "—")}</b>${(s.expiries || []).length > 1 ? ` · also: ${(s.expiries || []).slice(1, 5).map((e) => e.slice(5)).join(", ")}` : ""}</div>`;

  const un = (s.unusual || []).map((r) =>
    `<tr><td class="sym" title="${esc(r.contract ?? "")}">${r.type?.[0]} $${Number(r.strike).toFixed(0)}</td>` +
    `<td class="num">${esc(r.expiry ?? "—")}</td>` +
    `<td class="num">${Number(r.volume).toLocaleString()}</td>` +
    `<td class="num">${Number(r.oi).toLocaleString()}</td>` +
    `<td class="num"${heatStyle({ z: Math.min(3, (r.vol_oi - 1) / 2) })}><b>${r.vol_oi.toFixed(1)}×</b></td>` +
    `<td class="num">${r.iv == null ? "—" : r.iv.toFixed(1) + "%"}</td></tr>`).join("");

  body.innerHTML =
    `<div class="panel-subhead"><span>OPTIONS — GAMMA & FLOW <span class="muted">${esc(src)} · as of ${esc(d.asof ?? "—")}</span></span></div>` +
    picker +
    `<div class="kpirow">${kpis}</div>` +
    `<div class="panel-subhead"><span>GEX BY STRIKE — ${esc(optSym)} <span class="muted">${esc(s.near_expiry || "")}</span></span></div>` +
    expNote + gexChart(s.strike_chart, s.spot) +
    `<div class="muted">Blue = call gamma (dealers long gamma below, resistance above) · red = put gamma. Dashed gold = spot.</div>` +
    `<div class="panel-subhead"><span>UNUSUAL ACTIVITY <span class="muted">top ${s.unusual?.length ?? 0} by volume/OI · vol ≥ 500</span></span></div>` +
    (un ? `<div style="overflow-x:auto"><table class="opttable" data-sortable><thead><tr>` +
      `<th>Contract</th><th>Expiry</th><th>Volume</th><th>OI</th><th>Vol/OI</th><th>IV</th></tr></thead>` +
      `<tbody>${un}</tbody></table></div>`
      : `<div class="muted">No unusual contracts today.</div>`) +
    `<div class="panel-subhead"><span>HISTORY — ALL SYMBOLS <span class="muted">1D change vs prior snapshot · range vs history</span></span></div>` +
    `<div style="overflow-x:auto"><table class="opttable" data-sortable><thead><tr>` +
    `<th>Symbol</th><th>Metric</th><th>Latest</th><th>Δ 1D (nom + %)</th><th data-sort="off">Range<br>${RANGE_LEGEND}</th></tr></thead>` +
    `<tbody id="opt-hist"><tr><td colspan="5" class="muted">Loading…</td></tr></tbody></table></div>` +
    `<div class="muted">${esc(d.note ?? "")}</div>`;

  body.querySelectorAll("[data-opt-sym]").forEach((b) =>
    b.addEventListener("click", () => { optSym = b.dataset.optSym; renderOptions(doc); }));

  // history table (async): all symbols x all metrics
  const tb = document.getElementById("opt-hist");
  if (tb) {
    const jobs = [];
    for (const n of names)
      for (const [m, label, fmt, fmtD] of HIST_METRICS)
        jobs.push(historyRow(n, m, label, fmt, fmtD));
    const rows = await Promise.all(jobs);
    tb.innerHTML = rows.join("");
  }

  const foot = document.querySelector("#panel-options .panel-foot");
  if (foot) foot.textContent = `DATA: CBOE DELAYED / YAHOO · ${fmtAge(d.updated_at)}`;
}
