// MOVERS tab: single-stock sigma movers — top-10 positive/negative 5-day and
// 20-day standard-deviation price moves per index. Replicates the tasty*live*
// "largest std-dev moves" visual.
// Data: /api/dashboard "movers" panel. Vanilla JS + inline SVG, no new libs.
import { fmtAge } from "../fmt.js";
import { symName } from "../names.js";

const STALE_MINUTES = 10080; // 7d: weekly job

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

function barList(rows, maxAbs) {
  // rows: [{symbol, z, ret_pct}] — green right / red left, std-dev axis
  if (!rows || !rows.length) return `<div class="muted">no data</div>`;
  const W = 300, mid = 118, maxW = 150;
  const RH = 30, H = rows.length * RH + 4;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" style="max-width:100%;display:block" role="img">`;
  rows.forEach((r, i) => {
    const y = i * RH + 2;
    const w = maxAbs > 0 ? Math.min(maxW, (Math.abs(r.z) / maxAbs) * maxW) : 0;
    const x = r.z >= 0 ? mid : mid - w;
    const color = r.z >= 0 ? "#2fb56b" : "#e05252";
    const full = symName(r.symbol);
    const nm = full.includes(" — ") ? full.split(" — ").slice(1).join(" — ") : "";
    const nmShort = nm.length > 18 ? nm.slice(0, 17) + "…" : nm;
    const zTxt = `${r.z >= 0 ? "+" : ""}${r.z.toFixed(2)}`;
    s += `<text x="${mid - 6}" y="${y + 11}" text-anchor="end" font-size="10" font-weight="700" fill="#1e293b">${esc(r.symbol)}</text>`;
    if (nmShort) s += `<text x="${mid - 6}" y="${y + 23}" text-anchor="end" font-size="8" fill="#94a3b8">${esc(nmShort)}</text>`;
    s += `<rect x="${x}" y="${y + 8}" width="${Math.max(1, w)}" height="12" rx="2" fill="${color}">` +
      `<title>${esc(full)}: ${zTxt}σ (${r.ret_pct >= 0 ? "+" : ""}${r.ret_pct.toFixed(1)}%)</title></rect>`;
    s += `<text x="${r.z >= 0 ? x + w + 4 : x - 4}" y="${y + 19}" text-anchor="${r.z >= 0 ? "start" : "end"}" font-size="9" fill="#9aa4b2">${zTxt}</text>`;
  });
  // zero axis
  s += `<line x1="${mid}" y1="0" x2="${mid}" y2="${H}" stroke="#3a4450" stroke-width="1"/>`;
  s += `</svg><div class="muted">standard deviations</div>`;
  return s;
}

function windowSection(idx, winKey, winLabel) {
  const w = idx[winKey] || {};
  const all = [...(w.up || []), ...(w.down || [])];
  const maxAbs = Math.max(0.1, ...all.map((r) => Math.abs(r.z)));
  return `
    <div class="panel-subhead"><span>${winLabel} STD-DEV MOVE</span></div>
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      <div><div class="muted" style="margin-bottom:4px">largest positive</div>${barList(w.up, maxAbs)}</div>
      <div><div class="muted" style="margin-bottom:4px">largest negative</div>${barList(w.down, maxAbs)}</div>
    </div>`;
}

export function renderMovers(movers) {
  const m = movers || {};
  const body = document.querySelector("#panel-movers .panel-body");
  if (!body) return;
  const idxs = m.indexes || {};
  const keys = Object.keys(idxs);
  if (!keys.length) {
    body.innerHTML = `<div class="muted">No movers data yet — the movers job runs weekly on Saturdays (~600 symbols, polite spacing).</div>`;
  } else {
    body.innerHTML = keys.map((k) => {
      const idx = idxs[k];
      return `<div class="panel-subhead"><span>${esc(idx.label || k).toUpperCase()} <span class="muted">${idx.n_scored || 0}/${idx.n_universe || 0} scored · as of ${esc(m.asof || "—")}</span></span></div>` +
        windowSection(idx, "win5d", "5-DAY") + windowSection(idx, "win20d", "20-DAY");
    }).join("");
  }
  const foot = document.querySelector("#panel-movers .panel-foot");
  if (foot) {
    const src = (m.source ?? "—").toUpperCase();
    foot.textContent = `DATA: ${src} · ${fmtAge(m.updated_at)}`;
    const mins = m.updated_at
      ? (Date.now() - Date.parse(m.updated_at)) / 60000 : 1e9;
    foot.classList.toggle("stale", mins > STALE_MINUTES);
  }
}
