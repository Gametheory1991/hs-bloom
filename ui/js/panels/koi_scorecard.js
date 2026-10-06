// KOI SCORECARD — FINRA/TRACE Key Operating Indicators on the STRUCTURE tab.
// Primary view: Sep Y/Y (ADV + ADT) per product, sorted by ADV Y/Y desc.
// MoM shown secondary. Click a card for the full chart.
//
// TOTAL (Treasury + TRACE) KPI is computed LIVE from /api/series via the
// grid's normalization (toMetric/totalVals/rowStats) — Harry's standing
// rule: always lead with the combined overall.
// Product cards remain a verified static snapshot (Sep 2026 vs Sep 2025 for
// Y/Y, vs Aug 2026 for M/M). Refresh each month when the new TRACE report
// lands (see the monthly-trace-treasury-update cron).
import { openChart } from "../chart.js";
import { getSeries } from "../api.js";
import { toMetric, totalVals, rowStats, rangeById, TOTAL_PARTS } from "./trace_grid.js";

const ASOF_LABEL = "Sep 2026";
const YOY_LABEL = "Sep 2025";
const MOM_LABEL = "Aug 2026";

const ROWS = [
  { id: "ust",  label: "Treasury",     par: "cycle:trace-ust-par",  advYoy: 24.4,  adtYoy: 32.1,  advMom: 15.0,   adtMom: 29.6  },
  { id: "cmo",  label: "CMO",          par: "cycle:trace-cmo-par",  advYoy: 16.5,  adtYoy: 2.3,   advMom: 31.6,   adtMom: 7.0   },
  { id: "absx", label: "ABSX",         par: "cycle:trace-absx-par", advYoy: 5.4,   adtYoy: 13.6,  advMom: 11.3,   adtMom: 23.8  },
  { id: "abs",  label: "ABS",          par: "cycle:trace-abs-par",  advYoy: 0.9,   adtYoy: -42.1, advMom: -1.9,   adtMom: 11.1  },
  { id: "corp", label: "Corporate",    par: "cycle:trace-corp-par", advYoy: 0.7,   adtYoy: 4.1,   advMom: 11.9,   adtMom: 8.9   },
  { id: "eln",  label: "ELN",          par: "cycle:trace-eln-par",  advYoy: -5.3,  adtYoy: -59.4, advMom: 116.9,  adtMom: -15.4 },
  { id: "tba",  label: "TBA",          par: "cycle:trace-tba-par",  advYoy: -7.6,  adtYoy: -15.0, advMom: 26.2,   adtMom: 13.2  },
  { id: "mbs",  label: "MBS",          par: "cycle:trace-mbs-par",  advYoy: -12.8, adtYoy: 14.0,  advMom: 13.2,   adtMom: 23.6  },
  { id: "conv", label: "Convertibles", par: "cycle:trace-conv-par", advYoy: -13.0, adtYoy: -2.0,  advMom: -11.7,  adtMom: 2.5   },
  { id: "agcy", label: "Agency",       par: "cycle:trace-agcy-par", advYoy: -18.6, adtYoy: -38.9, advMom: 59.5,   adtMom: 39.6  },
  { id: "chrc", label: "Church",       par: "cycle:trace-chrc-par", advYoy: -38.9, adtYoy: -5.6,  advMom: -31.2,  adtMom: -28.0 },
];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtP = (x) => (x == null ? "—" : `${x > 0 ? "+" : ""}${x.toFixed(1)}%`);
const cls = (x) => (x == null || x === 0 ? "flat" : x > 0 ? "up" : "down");
const fmtB = (v) => v == null || !isFinite(v) ? "—" : `$${v >= 1000 ? (v / 1000).toFixed(2) + "T" : v.toFixed(0) + "B"}/d`;
const fmtT = (v) => v == null || !isFinite(v) ? "—" : `${Math.round(v).toLocaleString("en-US")}/d`;

// Component series ids for the TOTAL KPI (mirrors trace_grid.js PRODUCTS).
const TOTAL_SERIES = [
  { par: "trace-ust-par", trades: "trace-ust-trades", monthly: false },
  { par: "trace-tba-par", trades: null, monthly: true },
  { par: "trace-corp-par", trades: "trace-corp-trades", monthly: true },
  { par: "trace-eln-par", trades: "trace-eln-trades", monthly: true },
  { par: "trace-conv-par", trades: "trace-conv-trades", monthly: true },
  { par: "trace-agcy-par", trades: null, monthly: true },
  { par: "trace-abs-par", trades: null, monthly: true },
  { par: "trace-absx-par", trades: null, monthly: true },
  { par: "trace-cmo-par", trades: null, monthly: true },
  { par: "trace-mbs-par", trades: null, monthly: true },
  { par: "trace-chrc-par", trades: "trace-chrc-trades", monthly: true },
];

async function totalKpi() {
  // Fetch par + trades for every component, normalize, sum by month.
  const jobs = TOTAL_SERIES.map(async (c) => {
    const [par, tr] = await Promise.all([
      getSeries(c.par, "max").catch(() => null),
      c.trades ? getSeries(c.trades, "max").catch(() => null) : Promise.resolve(null),
    ]);
    if (!par) return null;
    // Match to the grid's TOTAL_PARTS entry by series id (for toMetric's `monthly` flag).
    const gp = TOTAL_PARTS.find((g) => g.par === c.par);
    if (!gp) return null;
    return { p: gp, par: par.points, tr: tr ? tr.points : null };
  });
  const data = (await Promise.all(jobs)).filter(Boolean);
  const advVals = totalVals(data, "adv");
  const adtVals = totalVals(data, "adt");
  const adv = rowStats(advVals, rangeById("3y"));
  const adt = rowStats(adtVals, rangeById("3y"));
  return { adv, adt };
}

function totalCard(t) {
  if (!t || !t.adv) return `<div class="koi-total muted">TOTAL KPI loading…</div>`;
  const a = t.adv, d = t.adt;
  const asof = a.asof ? a.asof.slice(0, 7) : "";
  const yoyLbl = a.asof ? `${+a.asof.slice(0, 4) - 1}${a.asof.slice(4, 7)}` : "";
  return `<div class="koi-total">
    <div class="koi-total-head">TOTAL (TREASURY + TRACE) <span class="muted">· ${esc(asof)} vs ${esc(yoyLbl)} · live</span></div>
    <div class="koi-total-grid">
      <div class="koi-metric"><span class="koi-k">ADV</span><span class="koi-v">${fmtB(a.cur.v)}</span></div>
      <div class="koi-metric"><span class="koi-k">ADV Y/Y</span><span class="koi-v ${cls(a.y1)}">${fmtP(a.y1 == null ? null : a.y1 * 100)}</span></div>
      <div class="koi-metric"><span class="koi-k">ADT</span><span class="koi-v">${d ? fmtT(d.cur.v) : "—"}</span></div>
      <div class="koi-metric"><span class="koi-k">ADT Y/Y</span><span class="koi-v ${cls(d?.y1)}">${fmtP(d?.y1 == null ? null : d.y1 * 100)}</span></div>
    </div>
  </div>`;
}

export function renderKoi() {
  const body = document.querySelector("#panel-koi .panel-body");
  if (!body) return;
  const rows = ROWS.slice().sort((a, b) => b.advYoy - a.advYoy);
  const cards = rows.map((r) => `
    <div class="koi-card clickable" data-series="${esc(r.par)}" data-name="${esc(r.label)}">
      <div class="koi-name">${esc(r.label).toUpperCase()}</div>
      <div class="koi-yoy">
        <div class="koi-metric"><span class="koi-k">ADV Y/Y</span><span class="koi-v ${cls(r.advYoy)}">${fmtP(r.advYoy)}</span></div>
        <div class="koi-metric"><span class="koi-k">ADT Y/Y</span><span class="koi-v ${cls(r.adtYoy)}">${fmtP(r.adtYoy)}</span></div>
      </div>
      <div class="koi-mom muted">M/M&nbsp; ADV ${fmtP(r.advMom)} · ADT ${fmtP(r.adtMom)}</div>
    </div>`).join("");
  body.innerHTML = `<div class="koi-sub muted">${ASOF_LABEL} vs ${YOY_LABEL} · ADV + ADT · sorted by ADV Y/Y · M/M vs ${MOM_LABEL}</div>` +
    `<div id="koi-total-slot">${totalCard(null)}</div>` +
    `<div class="koi-grid">${cards}</div>`;
  body.querySelectorAll(".koi-card").forEach((el) => {
    el.addEventListener("click", () => openChart(el.dataset.series, `TRACE — ${el.dataset.name}`));
  });
  const footEl = document.querySelector("#panel-koi .panel-foot");
  if (footEl) footEl.textContent = `DATA: FINRA · ${ASOF_LABEL} (product cards) · TOTAL live from /api/series`;
  // Fill the TOTAL KPI asynchronously (doesn't block the static cards).
  totalKpi().then((t) => {
    const slot = body.querySelector("#koi-total-slot");
    if (slot) slot.innerHTML = totalCard(t);
  }).catch(() => {
    const slot = body.querySelector("#koi-total-slot");
    if (slot) slot.innerHTML = `<div class="koi-total muted">TOTAL KPI unavailable</div>`;
  });
}
