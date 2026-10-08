// X-CORR tab: cross-asset correlation heatmap (CBOE-style triangular matrix),
// regime pairs + time series, realized vol, and the GSE/MBS plumbing read-out.
// Data: /api/dashboard "xcorr" + "gse" panels. Vanilla JS + inline SVG,
// no new libraries. Mobile-first: the matrix scrolls horizontally.
import { fmtAge, fmtUsd, ordinal } from "../fmt.js";
import { symName } from "../names.js";

const STALE_MINUTES = 2880; // 2x the daily xcorr cadence

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

// Diverging correlation color: red (-1) -> dark (0) -> green (+1).
function corrColor(v) {
  if (v == null) return "#232a33";
  const t = Math.max(-1, Math.min(1, v));
  const pos = t >= 0;
  const k = Math.abs(t);
  // base #1a2027 -> full #2fb56b (pos) / #d9736a (neg)
  const r = Math.round(26 + k * ((pos ? 0x2f : 0xe0) - 26));
  const g = Math.round(32 + k * ((pos ? 0xb5 : 0x52) - 32));
  const b = Math.round(39 + k * ((pos ? 0x6b : 0x52) - 39));
  return `rgb(${r},${g},${b})`;
}

const fmtCorr = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(2));

// Triangular CBOE-style matrix: lower triangle only, group headers, cells
// with |corr| >= 80% highlighted (CBOE's red tint).
function triHeatmap(labels, matrix, groups) {
  const n = labels.length;
  if (!n) return `<div class="muted">No correlation data yet — the xcorr job runs daily after the data jobs.</div>`;
  const cell = 34, padL = 78, padT = 20;
  const W = padL + n * cell, H = padT + n * cell;
  const grpColor = (g) => ({
    "Equities": "#4f9cf0", "Corporate Credit": "#e3b008", "Rates": "#b07fe8",
    "Commodities": "#e07b39", "Foreign Exchange": "#2fb56b",
  }[g] || "#9aa4b2");
  let s = `<div style="overflow-x:auto"><svg viewBox="0 0 ${W} ${H}" width="${W}" style="max-width:none;display:block" role="img" aria-label="correlation matrix">`;
  // group headers across the top
  (groups || []).forEach(([g, a, b]) => {
    const x0 = padL + a * cell, x1 = padL + b * cell;
    s += `<text x="${(x0 + x1) / 2}" y="10" text-anchor="middle" font-size="9" fill="${grpColor(g)}" letter-spacing="1">${esc(g).toUpperCase()}</text>`;
  });
  labels.forEach((lab, i) => {
    const y = padT + i * cell + cell / 2;
    s += `<text x="${padL - 6}" y="${y + 4}" text-anchor="end" font-size="10" fill="#9aa4b2"><title>${esc(symName(lab))}</title>${esc(lab)}</text>`;
  });
  for (let i = 0; i < n; i++) {
    for (let j = 0; j <= i; j++) {
      if (i === j) continue; // skip the 1.0 diagonal, like CBOE
      const v = matrix[i] ? matrix[i][j] : null;
      const x = padL + j * cell, y = padT + i * cell;
      const hot = v != null && Math.abs(v) >= 0.8;
      const fill = hot ? (v > 0 ? "#7a2e35" : "#7a2e35") : corrColor(v);
      const pct = v == null ? "" : `${Math.round(v * 100)}%`;
      s += `<rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" rx="2" fill="${fill}">` +
        `<title>${esc(labels[i])} vs ${esc(labels[j])}: ${fmtCorr(v)}</title></rect>`;
      if (v != null)
        s += `<text x="${x + cell / 2}" y="${y + cell / 2 + 4}" text-anchor="middle" font-size="10" fill="${hot ? "#ffb3b8" : "#c7cfd8"}">${pct}</text>`;
    }
  }
  s += `</svg></div>
    <div class="muted" style="margin-top:4px">1M Pearson on daily changes, pairwise-complete. ≥80% highlighted. Tap/hold a cell for the value.</div>`;
  return s;
}

function heatmap(labels, matrix) {
  // legacy full-square fallback (kept for the 252d window)
  const n = labels.length;
  if (!n) return `<div class="muted">No correlation data yet — the xcorr job runs daily after the data jobs.</div>`;
  const cell = 30, pad = 92;
  const W = pad + n * cell, H = pad + n * cell;
  let s = `<div style="overflow-x:auto"><svg viewBox="0 0 ${W} ${H}" width="${W}" style="max-width:none;display:block" role="img" aria-label="correlation matrix">`;
  labels.forEach((lab, i) => {
    const y = pad + i * cell + cell / 2;
    s += `<text x="${pad - 6}" y="${y + 4}" text-anchor="end" font-size="10" fill="#9aa4b2">${esc(lab)}</text>`;
  });
  labels.forEach((lab, j) => {
    const x = pad + j * cell + cell / 2;
    s += `<text x="${x}" y="${pad - 8}" text-anchor="end" font-size="10" fill="#9aa4b2" transform="rotate(-45 ${x} ${pad - 8})">${esc(lab)}</text>`;
  });
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const v = matrix[i] ? matrix[i][j] : null;
      const x = pad + j * cell, y = pad + i * cell;
      s += `<rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" rx="2" fill="${corrColor(v)}">` +
        `<title>${esc(labels[i])} vs ${esc(labels[j])}: ${fmtCorr(v)}</title></rect>`;
    }
  }
  s += `</svg></div>
    <div class="muted" style="margin-top:4px">Pearson on daily changes, pairwise-complete. Tap/hold a cell for the value.</div>`;
  return s;
}

let win = "60d";
function matrixSection(x) {
  const m = win === "60d" ? x.matrix_60d : x.matrix_252d;
  const nobs = win === "60d" ? x.n_obs_60d : x.n_obs_252d;
  const body = win === "60d"
    ? triHeatmap(x.labels || [], m || [], x.groups || [])
    : heatmap(x.labels || [], m || []);
  return `
    <div class="panel-subhead">
      <span>CROSS-ASSET CORRELATION MATRIX <span class="muted">1M</span></span>
      <span class="seg">
        <button data-win="60d" class="${win === "60d" ? "on" : ""}">60D</button><button data-win="252d" class="${win === "252d" ? "on" : ""}">1Y</button>
      </span>
    </div>
    ${body}
    <div class="muted">window ${win === "60d" ? "60" : "252"} trading days · ${nobs || 0} obs/pair max · as of ${esc(x.asof || "—")}</div>`;
}

// CBOE-style pair time series: trailing 1y of the 60d rolling correlation
// for the first six (digest) pairs.
function pairCharts(x) {
  const hist = x.pair_hist || {};
  const pairs = (x.pairs || []).slice(0, 6);
  const charts = pairs.map((p) => {
    const h = hist[p.id] || [];
    if (h.length < 20) return "";
    const W = 340, H = 120, padL = 34, padB = 16, padT = 6;
    const t0 = Date.parse(h[0][0]), t1 = Date.parse(h[h.length - 1][0]);
    const X = (iso) => padL + ((Date.parse(iso) - t0) / Math.max(1, t1 - t0)) * (W - padL - 8);
    const Y = (v) => padT + (1 - (v + 1) / 2) * (H - padT - padB);
    const d = h.map((pt, i) => `${i ? "L" : "M"}${X(pt[0]).toFixed(1)},${Y(pt[1]).toFixed(1)}`).join(" ");
    const last = h[h.length - 1][1];
    return `<div style="margin-bottom:8px">
      <div class="muted" style="margin-bottom:2px">${esc(p.label)} <span class="muted">${esc(p.a)} vs ${esc(p.b)}</span> <b style="color:${Math.abs(last) >= 0.7 ? "#d9736a" : "#c7cfd8"}">${(last > 0 ? "+" : "") + Math.round(last * 100)}%</b></div>
      <svg viewBox="0 0 ${W} ${H}" width="${W}" style="max-width:100%;display:block" role="img">
        <line x1="${padL}" y1="${Y(0)}" x2="${W - 8}" y2="${Y(0)}" stroke="#3a4450" stroke-width="1"/>
        <text x="${padL - 4}" y="${Y(1) + 3}" text-anchor="end" font-size="8" fill="#5a6572">100%</text>
        <text x="${padL - 4}" y="${Y(0) + 3}" text-anchor="end" font-size="8" fill="#5a6572">0</text>
        <text x="${padL - 4}" y="${Y(-1) + 3}" text-anchor="end" font-size="8" fill="#5a6572">-100%</text>
        <path d="${d}" fill="none" stroke="#4f9cf0" stroke-width="1.5"/>
      </svg></div>`;
  }).join("");
  if (!charts) return "";
  return `<div class="panel-subhead"><span>CROSS-ASSET CORRELATION ANALYSIS <span class="muted">60d rolling, 1Y</span></span></div>${charts}`;
}

function pairsSection(x) {
  const pairs = x.pairs || [];
  if (!pairs.length) return "";
  const rows = pairs.map((p) => {
    const pct = p.pctile_60d == null ? "—" : `${ordinal(p.pctile_60d)} %ile`;
    const badge = p.extreme ? ` <span class="badge warn">EXTREME</span>` : "";
    const bar = p.pctile_60d == null ? "" :
      `<div class="pctbar"><div style="width:${Math.min(100, Math.max(0, p.pctile_60d))}%"></div></div>`;
    return `<div class="kv"><div><b>${esc(p.label)}</b>${badge}<br>
      <span class="muted">${esc(p.a)} vs ${esc(p.b)} · 60d ${fmtCorr(p.corr_60d)} · 1y ${fmtCorr(p.corr_252d)} · ${pct}</span></div>${bar}</div>`;
  }).join("");
  return `<div class="panel-subhead"><span>REGIME PAIRS</span></div>${rows}`;
}

function rvolSection(x) {
  const rows = (x.rvol || []).map((r) => {
    const unit = r.unit === "pp" ? "bp" : "%";
    const m = (v) => (v == null ? "—" : v.toFixed(1));
    return `<div class="kv"><span>${esc(r.label)}</span>
      <span><b>${m(r.rv_21d)}</b><span class="muted"> / ${m(r.rv_63d)} ${unit}</span></span></div>`;
  }).join("");
  if (!rows) return "";
  return `<div class="panel-subhead"><span>REALIZED VOL <span class="muted">21d / 63d ann.</span></span></div>${rows}`;
}

function gseSection(g) {
  const rows = (g.series || []).map((s) => {
    const v = s.value_m == null ? "—" : `$${(s.value_m / 1000).toFixed(1)}B`;
    const c3 = s.chg_3m_m == null ? "—" : `${s.chg_3m_m > 0 ? "+" : ""}$${(s.chg_3m_m / 1000).toFixed(1)}B 3m`;
    const c12 = s.chg_12m_m == null ? "—" : `${s.chg_12m_m > 0 ? "+" : ""}$${(s.chg_12m_m / 1000).toFixed(1)}B 12m`;
    const cls = s.chg_3m_m == null ? "" : s.chg_3m_m >= 0 ? "up" : "down";
    return `<div class="kv"><span>${esc(s.label)}<br><span class="muted">${esc(s.asof || "")}</span></span>
      <span style="text-align:right"><b>${v}</b><br><span class="${cls}">${c3}</span><span class="muted"> · ${c12}</span></span></div>`;
  }).join("");
  if (!rows) return "";
  return `<div class="panel-subhead"><span>GSE RETAINED PORTFOLIOS <span class="muted">monthly</span></span></div>
    ${rows}
    <div class="muted">Fannie Mae + Freddie Mac month-end retained mortgage balances. The marginal GSE bid for agency MBS.</div>`;
}

export function renderXcorr(xcorr, gse) {
  const x = xcorr || {};
  const body = document.querySelector("#panel-xcorr .panel-body");
  if (!body) return;
  body.innerHTML =
    matrixSection(x) + pairCharts(x) + pairsSection(x) + rvolSection(x) + gseSection(gse || {});
  body.querySelectorAll("[data-win]").forEach((b) =>
    b.addEventListener("click", () => {
      win = b.dataset.win;
      renderXcorr(xcorr, gse);
    })
  );
  const foot = document.querySelector("#panel-xcorr .panel-foot");
  if (foot) {
    const src = ((xcorr || {}).source ?? "—").toUpperCase();
    foot.textContent = `DATA: ${src} · ${fmtAge((xcorr || {}).updated_at)}`;
    const mins = xcorr && xcorr.updated_at
      ? (Date.now() - Date.parse(xcorr.updated_at)) / 60000 : 1e9;
    foot.classList.toggle("stale", mins > STALE_MINUTES);
  }
}
