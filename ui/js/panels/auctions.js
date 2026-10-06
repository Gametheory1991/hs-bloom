import { getSeries } from "../api.js";
import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtB = (v) => v == null ? "—" : `$${(v / 1e9).toFixed(1)}B`;
const fmtY = (v) => v == null ? "—" : `${v.toFixed(3)}%`;
const fmtR = (v) => v == null ? "—" : v.toFixed(2);
const fmtP = (v) => v == null ? "—" : `${v.toFixed(1)}%`;

function aucDate(iso) {
  const d = new Date(iso + "T12:00:00");
  return Number.isFinite(d) ? d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—";
}

// Upcoming auction schedule + latest completed results (Fiscal Data Treasury API).
export function renderAuctions(panel) {
  const body = document.querySelector("#panel-auctions .panel-body");
  const upc = panel.upcoming ?? [];
  const rec = panel.recent ?? [];
  const upRows = upc.map((a) =>
    `<tr><td>${esc(aucDate(a.auction_date))}</td>` +
    `<td style="text-align:left">${esc(a.security_term ?? a.bucket ?? "—")}</td>` +
    `<td>${esc(fmtB(a.offering_amt))}</td></tr>`).join("");
  const recRows = rec.map((a) =>
    `<tr><td>${esc(aucDate(a.auction_date))}</td>` +
    `<td style="text-align:left">${esc(a.bucket ?? "—")}</td>` +
    `<td>${esc(fmtY(a.high_yield))}</td><td>${esc(fmtR(a.bid_to_cover))}</td>` +
    `<td>${esc(fmtP(a.indirect_pct))}</td><td>${esc(fmtP(a.direct_pct))}</td>` +
    `<td>${esc(fmtP(a.dealer_pct))}</td><td>${esc(fmtB(a.offering_amt))}</td></tr>`).join("");
  body.innerHTML =
    `<div class="panel-subhead">UPCOMING</div>` +
    (upc.length
      ? `<table data-sortable><tr><th>Date</th><th>Tenor</th><th>Offering</th></tr>${upRows}</table>`
      : `<div class="muted">No announced auctions on the schedule.</div>`) +
    `<div class="panel-subhead">RECENT RESULTS</div>` +
    (rec.length
      ? `<table data-sortable><tr><th>Date</th><th>Tenor</th><th>High yield</th>` +
        `<th>Bid/cover</th><th>Indirect %</th><th>Direct %</th>` +
        `<th>Dealer %</th><th>Offering</th></tr>${recRows}</table>`
      : `<div class="muted">No results yet.</div>`) +
    `<div class="panel-subhead"><span>BUCKET COMPARISON — VS 1Y HISTORY</span></div>` +
    `<div class="muted" style="margin-bottom:6px">Each bucket's latest auction vs its own trailing-1Y average. ${HEAT_LEGEND}</div>` +
    `<div id="auc-metric-row" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px"></div>` +
    `<div id="auc-compare"><div class="muted">Loading bucket history…</div></div>`;
  renderAucCompare(rec);
}

// ---- Bucket comparison: latest auction per bucket vs trailing-1Y history ----
const AUC_METRICS = [
  ["high_yield", "High yield", (v) => v == null ? "—" : `${v.toFixed(3)}%`, (d) => `${d >= 0 ? "+" : "−"}${Math.abs(d * 100).toFixed(0)}bp`],
  ["bid_to_cover", "Bid/cover", (v) => v == null ? "—" : v.toFixed(2), (d) => `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(2)}`],
  ["indirect_pct", "Indirect %", (v) => v == null ? "—" : `${v.toFixed(1)}%`, (d) => `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(1)}pp`],
  ["direct_pct", "Direct %", (v) => v == null ? "—" : `${v.toFixed(1)}%`, (d) => `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(1)}pp`],
  ["dealer_pct", "Dealer %", (v) => v == null ? "—" : `${v.toFixed(1)}%`, (d) => `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(1)}pp`],
  ["offering", "Offering", (v) => v == null ? "—" : `$${(v / 1e9).toFixed(1)}B`, (d) => `${d >= 0 ? "+" : "−"}$${Math.abs(d / 1e9).toFixed(1)}B`],
];
const AUC_TENOR_ORDER = ["Bill-4W", "Bill-8W", "Bill-13W", "Bill-17W", "Bill-26W", "Bill-52W",
  "Note-2Y", "Note-3Y", "Note-5Y", "Note-7Y", "Note-10Y",
  "Bond-20Y", "Bond-30Y", "TIPS-5Y", "TIPS-10Y", "TIPS-30Y", "FRN-2Y"];
let aucMetric = "bid_to_cover";
const aucCache = {};

async function renderAucCompare(rec) {
  const row = document.getElementById("auc-metric-row");
  const wrap = document.getElementById("auc-compare");
  if (!row || !wrap) return;
  row.innerHTML = AUC_METRICS.map(([id, label]) =>
    `<button type="button" data-auc-m="${id}" class="${id === aucMetric ? "active" : ""}">${label}</button>`).join("");
  row.querySelectorAll("[data-auc-m]").forEach((b) =>
    b.addEventListener("click", () => { aucMetric = b.dataset.aucM; renderAucCompare(rec); }));

  const buckets = [...new Set((rec ?? []).map((r) => r.bucket).filter(Boolean))]
    .sort((a, b) => (AUC_TENOR_ORDER.indexOf(a) === -1 ? 99 : AUC_TENOR_ORDER.indexOf(a)) -
                    (AUC_TENOR_ORDER.indexOf(b) === -1 ? 99 : AUC_TENOR_ORDER.indexOf(b)));
  const [, mLabel, fmtV, fmtD] = AUC_METRICS.find(([id]) => id === aucMetric);
  wrap.innerHTML = `<div class="muted">Loading ${esc(mLabel)} history…</div>`;

  const rows = await Promise.all(buckets.map(async (bucket) => {
    const sid = `auction:${bucket}:${aucMetric}`;
    try {
      let pts = aucCache[sid];
      if (!pts) {
        const j = await getSeries(sid, "max");
        pts = (j.points ?? []).map(([d, v]) => [d, Number(v)]).filter(([, v]) => isFinite(v));
        aucCache[sid] = pts;
      }
      if (pts.length < 3) return null;
      // trailing-1Y window for the historical baseline (explicit period)
      const lastD = new Date(pts[pts.length - 1][0] + "T12:00:00");
      lastD.setFullYear(lastD.getFullYear() - 1);
      const cut = lastD.toISOString().slice(0, 10);
      const y1 = pts.filter(([d]) => d >= cut).map(([, v]) => v);
      const vals = y1.length >= 3 ? y1 : pts.map(([, v]) => v);
      const s = statsFromValues(vals);
      if (s.avg == null) return null;
      const cur = vals[vals.length - 1];
      const nom = cur - s.avg;
      const pct = s.avg !== 0 ? nom / Math.abs(s.avg) : null;
      const asof = pts[pts.length - 1][0];
      const deltaTxt = `<b>${fmtD(nom)}</b> <span class="muted">(${pct == null ? "—" : (pct >= 0 ? "+" : "−") + Math.abs(pct * 100).toFixed(1) + "%"})</span>`;
      return `<tr><td class="sym">${esc(bucket)}</td>` +
        `<td class="num"><b>${fmtV(cur)}</b> <span class="muted">${esc(asof)}</span></td>` +
        `<td class="num"${heatStyle({ z: s.z })} title="Latest vs trailing-1Y avg (${vals.length} auctions)">${deltaTxt}</td>` +
        `${rangeCells({ z: s.z, pct: s.pct }, "trailing 1Y")}</tr>`;
    } catch { return null; }
  }));
  const good = rows.filter(Boolean);
  wrap.innerHTML = good.length
    ? `<div style="overflow-x:auto"><table data-sortable><tr><th>Bucket</th><th>Latest ${esc(mLabel)}</th>` +
      `<th title="Latest minus trailing-1Y average">Δ vs 1Y avg</th>${RANGE_TH}</tr>${good.join("")}</table></div>`
    : `<div class="muted">No bucket history available yet.</div>`;
}
