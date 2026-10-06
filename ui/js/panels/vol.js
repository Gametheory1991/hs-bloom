// VOL tab: CBOE Macro Volatility Digest replication with free data.
// Implied-vs-realized table, implied-vs-realized scatter, VIX-vs-VVIX,
// spot-vol beta. Data: /api/dashboard "voldash" panel.
// Vanilla JS + inline SVG, no new libraries.
import { fmtAge } from "../fmt.js";
import { rangePlotDotted } from "../rangeviz.js";
import { symNameHtml } from "../names.js";

const STALE_MINUTES = 2880; // 2x the daily voldash cadence

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

const n1 = (v) => (v == null ? "—" : v.toFixed(1));

// Percentile cell: green = cheap (<=10), red = rich (>=90), like CBOE.
function pctCell(v) {
  if (v == null) return `<td class="muted">—</td>`;
  const cls = v >= 90 ? "rich" : v <= 10 ? "cheap" : "";
  return `<td class="${cls}">${v.toFixed(0)}</td>`;
}

function tableSection(v) {
  const rows = v.rows || [];
  if (!rows.length)
    return `<div class="muted">No vol data yet — the voldash job runs daily after the data jobs.</div>`;
  const trs = rows.map((r) => {
    const chg = r.wkly_chg == null ? "—"
      : `<span class="${r.wkly_chg > 0 ? "up" : r.wkly_chg < 0 ? "down" : ""}">${r.wkly_chg > 0 ? "▲" : r.wkly_chg < 0 ? "▼" : "·"} ${Math.abs(r.wkly_chg).toFixed(1)}</span>`;
    const spr = r.spread == null ? "—"
      : `<span class="${r.spread > 0 ? "up" : r.spread < 0 ? "down" : ""}">${r.spread > 0 ? "+" : ""}${r.spread.toFixed(1)}</span>`;
    return `<tr><td>${symNameHtml(r.ticker)}</td><td>${n1(r.implied)}</td><td>${chg}</td>` +
      `${pctCell(r.pctile_1y)}<td data-sort="off">${rangePlotDotted({ pct: r.pctile_1y }, "pct")}</td>` +
      `<td>${n1(r.realized)}</td><td>${spr}</td>${pctCell(r.spread_pctile_1y)}` +
      `<td data-sort="off">${rangePlotDotted({ pct: r.spread_pctile_1y }, "pct")}</td></tr>`;
  }).join("");
  return `
    <div class="panel-subhead"><span>MACRO EQUITY VOLATILITY <span class="muted">1M implied vs realized</span></span></div>
    <div style="overflow-x:auto"><table class="voltable" data-sortable>
      <thead><tr><th>Ticker</th><th>1M Impl.</th><th>Wkly</th><th>%ile (1Y)</th><th data-sort="off">Range</th><th>1M Real.</th><th>Impl−Real</th><th>%ile (1Y)</th><th data-sort="off">Range</th></tr></thead>
      <tbody>${trs}</tbody>
    </table></div>
    <div class="muted">Implied = vol index (VIX/VXN/GVZ/OVX/VXSLV); realized = 21d ann. from prices. RTY/TLT/LQD/HYG have no free implied feed — realized only.</div>`;
}

function scatterSection(v) {
  const pts = (v.rows || []).filter((r) => r.implied != null && r.realized != null);
  if (pts.length < 2) return "";
  const W = 320, H = 240, pad = 34;
  const mx = Math.max(50, ...pts.map((r) => Math.max(r.implied, r.realized))) * 1.05;
  const X = (x) => pad + (x / mx) * (W - pad - 8);
  const Y = (y) => H - pad - (y / mx) * (H - pad - 8);
  let s = `<div class="panel-subhead"><span>IMPLIED VS REALIZED VOLATILITY</span></div>
    <svg viewBox="0 0 ${W} ${H}" width="${W}" style="max-width:100%;display:block" role="img">`;
  // diagonal
  s += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(mx)}" y2="${Y(mx)}" stroke="#3a4450" stroke-width="1"/>`;
  s += `<text x="${X(mx * 0.08)}" y="${Y(mx * 0.92)}" font-size="10" fill="#9aa4b2">Cheap</text>`;
  s += `<text x="${X(mx * 0.78)}" y="${Y(mx * 0.18)}" font-size="10" fill="#9aa4b2">Rich</text>`;
  pts.forEach((r) => {
    const cx = X(r.implied), cy = Y(r.realized);
    const rich = r.spread != null && r.spread > 3;
    s += `<circle cx="${cx}" cy="${cy}" r="5" fill="${rich ? "#e05252" : "#4f9cf0"}" opacity="0.85">` +
      `<title>${esc(r.ticker)}: implied ${n1(r.implied)}, realized ${n1(r.realized)}</title></circle>`;
    s += `<text x="${cx + 7}" y="${cy + 3}" font-size="9" fill="#9aa4b2">${esc(r.ticker)}</text>`;
  });
  s += `<text x="${W / 2}" y="${H - 6}" font-size="9" fill="#5a6572" text-anchor="middle">1M implied vol (%)</text>`;
  s += `<text x="10" y="${H / 2}" font-size="9" fill="#5a6572" text-anchor="middle" transform="rotate(-90 10 ${H / 2})">1M realized vol (%)</text>`;
  s += `</svg>`;
  return s;
}

function lineChart(seriesA, labelA, colorA, seriesB, labelB, colorB, title) {
  // series: [[iso, v], ...] — dual-scale line chart
  const all = [...seriesA, ...seriesB];
  if (all.length < 10) return "";
  const W = 340, H = 170, padL = 34, padR = 34, padT = 8, padB = 20;
  const t0 = Date.parse(all[0][0]), t1 = Date.parse(all[all.length - 1][0]);
  const X = (iso) => padL + ((Date.parse(iso) - t0) / Math.max(1, t1 - t0)) * (W - padL - padR);
  const rng = (s) => {
    const vs = s.map((p) => p[1]).filter((v) => v != null);
    return [Math.min(...vs), Math.max(...vs)];
  };
  const [a0, a1] = rng(seriesA), [b0, b1] = rng(seriesB);
  const YA = (v) => padT + (1 - (v - a0) / Math.max(1e-9, a1 - a0)) * (H - padT - padB);
  const YB = (v) => padT + (1 - (v - b0) / Math.max(1e-9, b1 - b0)) * (H - padT - padB);
  const path = (s, Y) => s.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  return `
    <div class="panel-subhead"><span>${title}</span></div>
    <svg viewBox="0 0 ${W} ${H}" width="${W}" style="max-width:100%;display:block" role="img">
      <path d="${path(seriesA, YA)}" fill="none" stroke="${colorA}" stroke-width="1.5"/>
      <path d="${path(seriesB, YB)}" fill="none" stroke="${colorB}" stroke-width="1.5"/>
      <text x="${padL}" y="${H - 6}" font-size="9" fill="${colorA}">${esc(labelA)} ${n1(seriesA.length ? seriesA[seriesA.length - 1][1] : null)}</text>
      <text x="${W - padR}" y="${H - 6}" font-size="9" fill="${colorB}" text-anchor="end">${esc(labelB)} ${n1(seriesB.length ? seriesB[seriesB.length - 1][1] : null)}</text>
    </svg>`;
}

function betaSection(v) {
  const b = v.beta || {};
  const bh = v.beta_hist || {};
  const bv = b.vix_spx, bvv = b.vvix_vix;
  if (bv == null && bvv == null) return "";
  const spark = (hist) => {
    if (!hist || hist.length < 10) return "";
    const W = 200, H = 44;
    const vs = hist.map((p) => p[1]);
    const lo = Math.min(...vs), hi = Math.max(...vs);
    const X = (i) => (i / (hist.length - 1)) * W;
    const Y = (val) => 4 + (1 - (val - lo) / Math.max(1e-9, hi - lo)) * (H - 8);
    const d = hist.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${W} ${H}" width="${W}" style="max-width:100%;display:block"><path d="${d}" fill="none" stroke="#4f9cf0" stroke-width="1.5"/></svg>`;
  };
  return `
    <div class="panel-subhead"><span>SPOT-VOL BETA <span class="muted">60d rolling</span></span></div>
    <div class="kv"><span>VIX vs SPX <span class="muted">(VIX log-chg on SPX return)</span></span>
      <span><b>${bv == null ? "—" : bv.toFixed(2)}</b></span></div>${spark(bh.vix_spx)}
    <div class="kv"><span>VVIX vs VIX <span class="muted">(vol-of-vol)</span></span>
      <span><b>${bvv == null ? "—" : bvv.toFixed(2)}</b></span></div>${spark(bh.vvix_vix)}
    <div class="muted">Negative VIX/SPX beta = equities fall, vol spikes. Rising VVIX/VIX beta = vol-of-vol stress.</div>`;
}

export function renderVol(voldash) {
  const v = voldash || {};
  const body = document.querySelector("#panel-vol .panel-body");
  if (!body) return;
  const regime = v.regime ? `<div class="kv"><span><b>REGIME</b></span><span>${esc(v.regime)}</span></div>` : "";
  body.innerHTML =
    regime +
    tableSection(v) +
    scatterSection(v) +
    lineChart(v.vix_hist || [], "VIX", "#4f9cf0", v.vvix_hist || [], "VVIX", "#e3b008", "VIX VS VVIX INDEX") +
    betaSection(v);
  const foot = document.querySelector("#panel-vol .panel-foot");
  if (foot) {
    const src = (v.source ?? "—").toUpperCase();
    foot.textContent = `DATA: ${src} · ${fmtAge(v.updated_at)}`;
    const mins = v.updated_at
      ? (Date.now() - Date.parse(v.updated_at)) / 60000 : 1e9;
    foot.classList.toggle("stale", mins > STALE_MINUTES);
  }
}
