// TAPE VOLUME tab (EQUITY): NasdaqTrader Full Volume Summary.
// Trend chart (Tape A/B/C + Total) on top; venue grid heatmap below.
// Data: /api/dashboard "tape" panel (one fetch — rolling daily history
// per venue per metric, accumulated daily from Nasdaq's 30-day window).
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";
import { rangeCells, RANGE_TH, RANGE_LEGEND } from "../rangeviz.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const METRICS = [["shares", "Shares"], ["trades", "Trades"], ["dollar", "Dollar vol"]];
const RANGES = [["1m", "1M"], ["3m", "3M"], ["1y", "1Y"], ["max", "MAX"]];
const RANGE_PTS = { "1m": 21, "3m": 63, "1y": 252, "max": 1e9 };
const HORIZONS = [["1d", "1D", 1], ["1m", "1M", 21], ["1q", "1Q", 63], ["1y", "1Y", 252]];
const TAPES = ["a", "b", "c", "total"];
const TAPE_SHORT = { a: "Tape A", b: "Tape B", c: "Tape C", total: "Total" };
const TAPE_COLORS = { a: "#2563eb", b: "#0891b2", c: "#7c3aed", total: "#64748b" };

const state = { metric: "shares", range: "3m", horizon: "1d" };
let tapeData = null;

function fmtBig(v, metric) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (metric === "dollar") {
    if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
    if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
    return `$${(v / 1e6).toFixed(1)}M`;
  }
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return `${v.toFixed(0)}`;
}

function fmtNom(v, metric) {
  if (v == null || !isFinite(v)) return "—";
  const s = v >= 0 ? "+" : "−";
  return s + fmtBig(Math.abs(v), metric).replace(/^\$/, "$");
}

function pctTxt(p) {
  if (p == null || !isFinite(p)) return "—";
  return `${p >= 0 ? "+" : ""}${(p * 100).toFixed(1)}%`;
}

/** value `h` trading-days back (or null when history is too short) */
function refBack(arr, h) {
  // arr: [[date, a, b, c, total], ...] ascending
  if (arr.length <= h) return null;
  return arr[arr.length - 1 - h];
}

function tapeCell(vals, refVals, ti, metric) {
  // vals/refVals: [date, a, b, c, total]
  const now = vals?.[ti];
  const ref = refVals?.[ti];
  if (now == null) return `<td class="num muted">—</td>`;
  const main = `<b>${fmtBig(now, metric)}</b>`;
  if (ref == null || ref === 0)
    return `<td class="num" title="History too short — accumulating">${main}<br><span class="muted" style="font-size:11px">accumulating</span></td>`;
  const nom = now - ref, pct = nom / Math.abs(ref);
  return `<td class="num"${heatStyle({ pct })} title="${esc(fmtNom(nom, metric))} vs ${HORIZONS.find((x) => x[0] === state.horizon)[1]}">${main}<br>` +
    `<span style="font-size:11px" class="${nom > 0 ? "up" : nom < 0 ? "down" : "flat"}">${pctTxt(pct)}</span> ` +
    `<span class="muted" style="font-size:11px">(${esc(fmtNom(nom, metric))})</span></td>`;
}

function renderTapeChart() {
  const host = document.getElementById("tape-chart");
  if (!host || !tapeData) return;
  const mkt = tapeData.market?.[state.metric] ?? [];
  const n = Math.min(RANGE_PTS[state.range], mkt.length);
  const rows = mkt.slice(-n);
  if (rows.length < 2) { host.innerHTML = `<span class="muted">Not enough history yet.</span>`; return; }
  const dates = rows.map((r) => r[0]);
  const data = [dates.map((d) => Date.parse(d + "T12:00:00") / 1000),
    ...TAPES.map((t, i) => rows.map((r) => r[i + 1]))];
  const axis = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
  new uPlot({
    width: Math.max(300, host.clientWidth || 720), height: 260,
    series: [{},
      ...TAPES.map((t) => ({
        label: t === "total" ? "Total" : tapeData.tape_labels?.[t]?.split(" — ")[0] ?? `Tape ${t.toUpperCase()}`,
        stroke: TAPE_COLORS[t], width: t === "total" ? 1.2 : 1.8,
        dash: t === "total" ? [5, 4] : [], spanGaps: true,
      }))],
    axes: [axis, { ...axis }],
  }, data, host);
}

function venueRows() {
  const venues = Object.values(tapeData.venues ?? {});
  const hz = HORIZONS.find((x) => x[0] === state.horizon);
  const h = hz[2];
  const rows = venues.map((v) => {
    const arr = v[state.metric] ?? [];
    const latest = arr[arr.length - 1];
    const ref = refBack(arr, h);
    return { v, latest, ref, total: latest?.[4] ?? 0 };
  }).sort((x, y) => y.total - x.total);
  // market total row (pinned via data-sort-row="off")
  const marr = tapeData.market?.[state.metric] ?? [];
  rows.unshift({ v: { name: "MARKET TOTAL", slug: "all" }, latest: marr[marr.length - 1],
                 ref: refBack(marr, h), total: Infinity, pin: true });
  return rows;
}

function ma10pct(arr) {
  if (arr.length < 10) return null;
  const last10 = arr.slice(-10).map((r) => r[4]);
  const avg = last10.reduce((s, x) => s + x, 0) / 10;
  const now = arr[arr.length - 1][4];
  return avg ? (now - avg) / avg : null;
}

function renderTapeGrid() {
  const host = document.getElementById("tape-grid");
  if (!host || !tapeData) return;
  const hz = HORIZONS.find((x) => x[0] === state.horizon);
  const rows = venueRows();
  const mktArr = tapeData.market?.[state.metric] ?? [];
  const mktLatest = mktArr[mktArr.length - 1];
  const depthNote = tapeData.history_days < 63
    ? `<span class="muted"> · ${tapeData.history_days} trading days banked — 1Q/1Y unlock as history accumulates</span>` : "";
  host.innerHTML = `
    <div style="margin-bottom:6px">${HEAT_LEGEND}${depthNote}</div>
    <div style="overflow-x:auto"><table class="tape-grid" data-sortable>
      <tr>
        <th data-sort="off">Venue</th>
        ${["a", "b", "c"].map((t) => `<th title="${esc(tapeData.tape_labels?.[t] ?? "")}">${TAPE_SHORT[t]}<br><span class="muted" style="font-weight:normal">latest · ${hz[1]} Δ</span></th>`).join("")}
        <th>Total<br><span class="muted" style="font-weight:normal">latest · ${hz[1]} Δ</span></th>
        <th data-sort="off" title="Latest total vs its own 10-trading-day moving average">vs 10D MA</th>
      </tr>
      ${rows.map(({ v, latest, ref, pin }) => {
        const arr = pin ? mktArr : (v[state.metric] ?? []);
        const ma = ma10pct(arr);
        return `<tr${pin ? ' data-sort-row="off" class="total-row"' : ""}>
          <td${pin ? ' data-sort="off"' : ""}><b>${esc(v.name)}</b></td>
          ${[1, 2, 3, 4].map((ti) => {
            const cell = tapeCell(latest, ref, ti, state.metric);
            if (ti <= 3 && mktLatest && latest) {
              const share = mktLatest[ti] ? latest[ti] / mktLatest[ti] : null;
              const shareTxt = share == null ? "" : `<br><span class="muted" style="font-size:11px">${(share * 100).toFixed(1)}% of tape</span>`;
              return cell.replace(/<\/td>$/, `${shareTxt}</td>`);
            }
            return cell;
          }).join("")}
          <td class="num"${ma == null ? "" : heatStyle({ pct: ma })}>${ma == null ? '<span class="muted">—</span>' : `<span class="${ma > 0 ? "up" : ma < 0 ? "down" : "flat"}">${pctTxt(ma)}</span>`}</td>
        </tr>`;
      }).join("")}
    </table></div>
    <div class="muted" style="margin-top:8px; font-size:12px">
      Tape legend: <b>A</b> = NYSE-listed · <b>B</b> = NYSE Arca/American-listed · <b>C</b> = Nasdaq-listed.
      FINRA TRF rows = off-exchange prints. Cells show latest value with ${hz[1]} nominal + % change (heatmapped); share % = venue's slice of that tape.
      Source: NasdaqTrader Full Volume Summary (T+1, daily).
    </div>`;
}

function syncTapeCtrls() {
  document.querySelectorAll("#tape-metric button").forEach((b) =>
    b.classList.toggle("on", b.dataset.metric === state.metric));
  document.querySelectorAll("#tape-range button").forEach((b) =>
    b.classList.toggle("on", b.dataset.range === state.range));
  document.querySelectorAll("#tape-horizon button").forEach((b) =>
    b.classList.toggle("on", b.dataset.horizon === state.horizon));
}

export function renderTape(tape) {
  tapeData = tape;
  const body = document.querySelector("#panel-tape .panel-body");
  if (!body) return;
  if (!tape || !tape.dates?.length) {
    body.innerHTML = `<div class="empty-state">NO TAPE DATA YET — first daily pull pending.</div>`;
    return;
  }
  body.innerHTML = `
    <div class="chart-controls" style="margin:2px 0 8px; display:flex; gap:12px; flex-wrap:wrap; align-items:center">
      <span><span class="muted">Metric</span>
        <span class="seg" id="tape-metric">${METRICS.map(([id, l]) => `<button type="button" data-metric="${id}">${l}</button>`).join("")}</span></span>
      <span><span class="muted">Chart</span>
        <span class="seg" id="tape-range">${RANGES.map(([id, l]) => `<button type="button" data-range="${id}">${l}</button>`).join("")}</span></span>
      <span><span class="muted">Grid Δ horizon</span>
        <span class="seg" id="tape-horizon">${HORIZONS.map(([id, l]) => `<button type="button" data-horizon="${id}">${l}</button>`).join("")}</span></span>
      <span class="muted" style="margin-left:auto">as of ${esc(tape.as_of)} · ${tape.history_days}d history</span>
    </div>
    <div id="tape-chart" style="min-height:280px"></div>
    <h4 style="margin:14px 0 6px">VENUE × TAPE GRID <span class="muted">— shares, trades &amp; dollar volume by exchange</span></h4>
    <div id="tape-grid"></div>`;
  document.querySelectorAll("#tape-metric button").forEach((b) =>
    b.addEventListener("click", () => { state.metric = b.dataset.metric; syncTapeCtrls(); renderTapeChart(); renderTapeGrid(); }));
  document.querySelectorAll("#tape-range button").forEach((b) =>
    b.addEventListener("click", () => { state.range = b.dataset.range; syncTapeCtrls(); renderTapeChart(); }));
  document.querySelectorAll("#tape-horizon button").forEach((b) =>
    b.addEventListener("click", () => { state.horizon = b.dataset.horizon; syncTapeCtrls(); renderTapeGrid(); }));
  syncTapeCtrls();
  renderTapeChart();
  renderTapeGrid();
  // re-draw chart on resize (debounced)
  if (!renderTape._rz) {
    renderTape._rz = true;
    let t = null;
    window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => renderTapeChart(), 250); });
  }
}
