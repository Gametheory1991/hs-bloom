// PREDICT tab: prediction-market data + edge engine output.
// Reads dash.panels.predict (backend pred_edge doc + venue snapshots).
// All edge/mispricing figures are model estimates, labeled as such —
// the backend disclaimer is rendered verbatim in the panel foot.
// Volume series come from /api/series (cycle:predvol-*, cycle:predvolct-*,
// cycle:predact-*, cycle:predlong-*); FOMC pricing from the fed_meetings doc.
import { getSeries } from "../api.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";
const pct = (x) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const usd = (x) =>
  x == null ? "—" : "$" + x.toLocaleString("en-US", { maximumFractionDigits: 0 });
const usdShort = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
};
const link = (url, text) =>
  url ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>` : text;

function edgeTable(edges) {
  if (!edges.length)
    return `<p class="muted">No cross-venue divergences above threshold right now.</p>`;
  return `<table data-sortable>
      <tr><th>Event</th><th>Poly Yes</th><th>Kalshi Yes</th><th>Spread</th>
      <th>Edge est.</th><th>Score</th><th>Tradable?</th></tr>
      ${edges.map((e) => {
        const sCls = e.spread > 0 ? "up" : e.spread < 0 ? "down" : "flat";
        const tCls = e.tradable_estimate ? "up" : "flat";
        return `<tr><td>${e.label}<br><span class="muted">${link(e.poly_url, "poly")} · ${link(e.kalshi_url, "kalshi")}</span></td>` +
          `<td>${pct(e.poly_yes)}</td><td>${pct(e.kalshi_yes)}</td>` +
          `<td class="${sCls}">${e.spread > 0 ? "+" : ""}${(e.spread * 100).toFixed(1)}¢</td>` +
          `<td>${(e.edge_estimate * 100).toFixed(1)}¢</td>` +
          `<td>${e.mispricing_score}</td>` +
          `<td class="${tCls}">${e.tradable_estimate ? "yes*" : "no"}</td></tr>`;
      }).join("")}
    </table>
    <p class="muted">* "tradable" = spread clears a ~4¢ round-trip cost estimate and both legs pass liquidity gates. Model estimate, not a guarantee.</p>`;
}

function marketsTable(title, rows, priceKey) {
  if (!rows.length) return `<p class="muted">No ${title} data yet.</p>`;
  return `<h3>${title}</h3><table data-sortable>
      <tr><th>Market</th><th>Yes</th><th>24h vol</th></tr>
      ${rows.map((m) => {
        const label = m.question ?? m.title ?? "—";
        const px = m[priceKey] ?? m.yes_price ?? m.last_price;
        return `<tr><td>${link(m.url, label.slice(0, 90))}</td>` +
          `<td>${pct(px)}</td><td>${usd(m.volume24h)}</td></tr>`;
      }).join("")}
    </table>`;
}

function calibTable(rows) {
  if (!rows.length)
    return `<p class="muted">No resolved markets scored yet — calibration builds as tracked markets settle.</p>`;
  return `<table data-sortable>
      <tr><th>Venue</th><th>Category</th><th>n</th><th>Brier</th><th>Win rate</th><th>Avg implied</th></tr>
      ${rows.map((r) => `<tr><td>${r.venue}</td><td>${r.category}</td><td>${r.n}</td>` +
        `<td>${r.brier.toFixed(3)}</td><td>${(r.win_rate * 100).toFixed(1)}%</td>` +
        `<td>${(r.avg_implied * 100).toFixed(1)}%</td></tr>`).join("")}
    </table>
    <p class="muted">Brier = mean squared error of implied probability vs outcome (lower is better; 0.25 = coin-flip).</p>`;
}

function moversTable(movers) {
  if (!movers.length) return "";
  return `<h3>BIGGEST 1D MOVERS — POLYMARKET</h3><div>${HEAT_LEGEND}</div><table data-sortable>
      <tr><th>Market</th><th>Yes</th><th>Δ 1D</th><th>24h vol</th></tr>
      ${movers.map((m) => {
        const cls = m.chg_1d > 0 ? "up" : "down";
        return `<tr><td>${link(m.url, (m.label ?? "").slice(0, 90))}</td>` +
          `<td>${pct(m.yes)}</td><td class="${cls}"${heatStyle({ pct: m.chg_1d })}>${m.chg_1d > 0 ? "+" : ""}${(m.chg_1d * 100).toFixed(1)}pp</td>` +
          `<td>${usd(m.volume24h)}</td></tr>`;
      }).join("")}
    </table>`;
}

export function renderPredict(p) {
  const body = document.querySelector("#panel-predict .panel-body");
  if (!body) return;
  body.innerHTML =
    `<h3>VOLUME — TRACKED TOP-30 PER VENUE</h3>` +
    `<div><span class="seg" id="pred-vol-range">${VOL_RANGES.map(([id, l]) =>
      `<button type="button" data-range="${id}" class="${id === predVolRange ? "on" : ""}">${l}</button>`).join("")}</span></div>` +
    `<div id="pred-vol-chart"></div><p class="muted" id="pred-vol-status"></p>` +
    `<h3>LONGSHOT VOLUME SHARE — 24H</h3>` + longshotTable(p.longshot_volume ?? {}) +
    `<div id="pred-long-chart"></div><p class="muted" id="pred-long-status"></p>` +
    `<h3>LONGSHOT REALIZED P&amp;L — RESOLVED MARKETS</h3>` + longshotPnlTable(p.longshot_pnl ?? []) +
    `<h3>MONTHLY TRADING VOLUME — COMBINED</h3>` +
    `<div id="pred-month-chart"></div><p class="muted" id="pred-month-status"></p>` +
    `<h3>ACTIVITY — CONTRACTS &amp; MARKETS (MONTHLY)</h3><div id="pred-activity"></div>` +
    `<h3>NEXT FOMC MEETING — MARKET PRICING</h3><div id="pred-fed"></div>` +
    `<h3>CROSS-VENUE EDGE ESTIMATES</h3>` + edgeTable(p.edges ?? []) +
    marketsTable("TOP POLYMARKET (24H VOLUME)", p.polymarket ?? [], "yes_price") +
    marketsTable("TOP KALSHI (24H VOLUME)", p.kalshi ?? [], "last_price") +
    moversTable(p.movers ?? []) +
    `<h3>CALIBRATION LEADERBOARD</h3>` + calibTable(p.calibration ?? []) +
    `<p class="muted">Tracking ${p.tracked_count ?? 0} markets for resolution · ` +
    `resolved this run: ${p.resolved_this_run ?? 0}` +
    `${(p.skipped ?? []).length ? ` · skipped: ${p.skipped.join("; ")}` : ""}</p>` +
    (p.disclaimer ? `<p class="muted">${p.disclaimer}</p>` : "");
  body.querySelectorAll("#pred-vol-range button").forEach((b) =>
    b.addEventListener("click", () => {
      predVolRange = b.dataset.range;
      body.querySelectorAll("#pred-vol-range button").forEach((x) =>
        x.classList.toggle("on", x === b));
      drawVolumeChart();
    }));
  drawVolumeChart();
  drawLongshotChart();
  drawMonthlyChart();
  drawActivity();
  drawFedChart(p.fed_meeting ?? null);
}

// ---- Volume + longshot charts (series via /api/series) ----

let predVolPlot = null;
let predLongPlot = null;
let predVolRange = "max"; // client-side filter on the full history
const VOL_RANGES = [["1m", "1M"], ["3m", "3M"], ["1y", "1Y"], ["max", "MAX"]];

function filterRange(pts, range) {
  if (range === "max" || !pts.length) return pts;
  const days = { "1m": 31, "3m": 93, "1y": 366 }[range] ?? 36500;
  const cut = new Date(pts[pts.length - 1][0] + "T00:00:00Z");
  cut.setUTCDate(cut.getUTCDate() - days);
  const cutStr = cut.toISOString().slice(0, 10);
  return pts.filter(([d]) => d >= cutStr);
}

const sortedPts = (s) =>
  ((s && s.points) ?? []).slice().sort((a, b) => (a[0] < b[0] ? -1 : 1));

function aligned(ptsA, ptsB) {
  const dates = [...new Set([...ptsA.map(([d]) => d), ...ptsB.map(([d]) => d)])].sort();
  const ma = new Map(ptsA), mb = new Map(ptsB);
  return {
    xs: dates.map((d) => new Date(d + "T00:00:00Z").getTime() / 1000),
    ys: [dates.map((d) => (ma.has(d) ? ma.get(d) : null)),
          dates.map((d) => (mb.has(d) ? mb.get(d) : null))],
  };
}

const AX = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };

async function drawVolumeChart() {
  const el = document.getElementById("pred-vol-chart");
  if (!el) return;
  const st = document.getElementById("pred-vol-status");
  try {
    const [poly, kal] = await Promise.all([
      getSeries("cycle:predvol-polymarket", "max"),
      getSeries("cycle:predvol-kalshi", "max"),
    ]);
    const pp = filterRange(sortedPts(poly), predVolRange);
    const kp = filterRange(sortedPts(kal), predVolRange);
    const { xs, ys } = aligned(pp, kp);
    if (predVolPlot) { predVolPlot.destroy(); predVolPlot = null; }
    if (typeof uPlot !== "undefined" && xs.length > 1) {
      predVolPlot = new uPlot({
        width: Math.max(300, el.clientWidth || 900), height: 300,
        scales: { x: { time: true } },
        series: [{},
          { label: "Polymarket", stroke: "#2563eb", width: 2, spanGaps: true,
            value: (u, v) => (v == null ? "—" : usdShort(v)) },
          { label: "Kalshi (est. $)", stroke: "#06b6d4", width: 2, spanGaps: true,
            value: (u, v) => (v == null ? "—" : usdShort(v)) }],
        axes: [AX, { ...AX, values: (u, vs) => vs.map(usdShort) }],
      }, [xs, ...ys], el);
    }
    const lp = pp.length ? pp[pp.length - 1][1] : null;
    const lk = kp.length ? kp[kp.length - 1][1] : null;
    if (st) st.textContent =
      `Latest: Polymarket ${usdShort(lp)} · Kalshi ${usdShort(lk)} (est. $) · ` +
      `tracked top-30 markets per venue, not full-venue volume · ` +
      `Kalshi history backfilled to market inception; Polymarket volume from Oct 2026.`;
  } catch (e) {
    if (st) st.textContent = "Volume chart pending — history accumulating.";
  }
}

const LONGSHOT_SERIES = [
  ["cycle:predlong-polymarket-le2", "Poly ≤2¢", "#2563eb"],
  ["cycle:predlong-polymarket-b2_10", "Poly 2–10¢", "#93c5fd"],
  ["cycle:predlong-kalshi-le2", "Kalshi ≤2¢", "#0e7490"],
  ["cycle:predlong-kalshi-b2_10", "Kalshi 2–10¢", "#67e8f9"],
];

async function drawLongshotChart() {
  const el = document.getElementById("pred-long-chart");
  if (!el) return;
  const st = document.getElementById("pred-long-status");
  try {
    const all = await Promise.all(
      LONGSHOT_SERIES.map(([id]) => getSeries(id, "max").catch(() => null)));
    // align on union of dates across the four series
    const dates = [...new Set(all.flatMap((s) => sortedPts(s).map(([d]) => d)))].sort();
    const maps = all.map((s) => new Map(sortedPts(s)));
    const xs = dates.map((d) => new Date(d + "T00:00:00Z").getTime() / 1000);
    const cols = maps.map((m) => dates.map((d) => (m.has(d) ? m.get(d) : null)));
    if (predLongPlot) { predLongPlot.destroy(); predLongPlot = null; }
    if (typeof uPlot !== "undefined" && xs.length > 1) {
      predLongPlot = new uPlot({
        width: Math.max(300, el.clientWidth || 900), height: 260,
        scales: { x: { time: true } },
        series: [{}, ...LONGSHOT_SERIES.map(([, label, stroke]) => ({
          label, stroke, width: 1.5, spanGaps: true,
          value: (u, v) => (v == null ? "—" : `${v.toFixed(1)}%`),
        }))],
        axes: [AX, { ...AX, values: (u, vs) => vs.map((v) => (v == null ? "" : `${v.toFixed(0)}%`)) }],
      }, [xs, ...cols], el);
    }
    if (st) st.textContent =
      "Share of 24h volume in longshot buckets (Yes ≤10¢). " +
      "Polymarket longshot share starts Oct 2026 (no free volume history); Kalshi backfilled.";
  } catch (e) {
    if (st) st.textContent = "Longshot chart pending — history accumulating.";
  }
}

function longshotTable(lv) {
  const venues = ["polymarket", "kalshi"].filter((v) => lv[v]);
  if (!venues.length)
    return `<p class="muted">Longshot breakdown pending — fills in on the next engine run.</p>`;
  const keys = ["le2", "b2_10", "b10_30", "b30_70", "gt70"];
  const lbl = (k) => (((lv[venues[0]] || {}).buckets || {})[k] || {}).label || k;
  return `<table data-sortable><tr><th>Yes-price bucket</th>` +
    venues.map((v) => `<th>${v} share</th><th>${v} 24h vol</th>`).join("") + `</tr>` +
    keys.map((k) => `<tr><td>${lbl(k)}</td>` + venues.map((v) => {
      const b = ((lv[v] || {}).buckets || {})[k] || {};
      return `<td>${b.share_pct == null ? "—" : b.share_pct.toFixed(1) + "%"}</td>` +
        `<td>${usdShort(b.volume_usd)} <span class="muted">(${b.n_markets ?? 0})</span></td>`;
    }).join("") + `</tr>`).join("") + `</table>` +
    `<p class="muted">Share of 24h volume by Yes-price bucket, tracked top-30 per venue (est. $; market count in brackets). ` +
    venues.map((v) => `${v}: ${usdShort(lv[v].total_volume_usd)}`).join(" · ") + `.</p>`;
}

function longshotPnlTable(rows) {
  if (!rows.length)
    return `<p class="muted">No resolved markets scored yet — realized P&amp;L builds as tracked markets settle. ` +
      `History starts Oct 2026, so treat early readings as small-sample.</p>`;
  const smallN = rows.every((r) => r.n < 30);
  return `<table data-sortable><tr><th>Venue</th><th>Bucket</th><th>n</th>` +
    `<th>Return</th><th>P&amp;L</th></tr>` +
    rows.map((r) => {
      const cls = r.return > 0 ? "up" : r.return < 0 ? "down" : "flat";
      const rp = `${r.return_pct >= 0 ? "+" : ""}${r.return_pct.toFixed(1)}%`;
      return `<tr><td>${r.venue}</td><td>${r.label}</td><td>${r.n}</td>` +
        `<td class="${cls}">${rp}</td>` +
        `<td class="${cls}">${r.pnl >= 0 ? "+" : "−"}$${Math.abs(r.pnl).toFixed(2)} ` +
        `<span class="muted">on $${r.stake.toFixed(0)}</span></td></tr>`;
    }).join("") + `</table>` +
    `<p class="muted">Realized return of a $1 Yes buy at the tracked price, equal-weighted per market. ` +
    `Bloomberg's published −15%/−27% figures are volume-weighted, so magnitudes will differ; direction is the comparison.` +
    (smallN ? ` Small sample (n&lt;30 in every bucket) — directional only.` : "") + `</p>`;
}

// ---- Monthly volume chart + activity panel (Pew-style) ----

let predMonthPlot = null;

// daily [[date, val]] -> { "YYYY-MM": sum }
function toMonths(pts) {
  const m = {};
  for (const [d, v] of pts) {
    const k = d.slice(0, 7);
    m[k] = (m[k] || 0) + (v || 0);
  }
  return m;
}
const monthLabel = (ym) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleString("en-US",
    { month: "short", year: "2-digit", timeZone: "UTC" });
};

async function drawMonthlyChart() {
  const el = document.getElementById("pred-month-chart");
  if (!el) return;
  const st = document.getElementById("pred-month-status");
  try {
    const [kal, poly] = await Promise.all([
      getSeries("cycle:predvol-kalshi", "max").catch(() => null),
      getSeries("cycle:predvol-polymarket", "max").catch(() => null),
    ]);
    const km = toMonths(sortedPts(kal)), pm = toMonths(sortedPts(poly));
    const months = [...new Set([...Object.keys(km), ...Object.keys(pm)])].sort();
    if (months.length < 2) {
      if (st) st.textContent = "Monthly chart pending — history accumulating.";
      return;
    }
    const xs = months.map((m) => {
      const [y, mo] = m.split("-").map(Number);
      return Date.UTC(y, mo - 1, 1) / 1000;
    });
    const kVals = months.map((m) => km[m] ?? 0);
    const pVals = months.map((m) => pm[m] ?? 0);
    const cVals = months.map((m, i) => kVals[i] + pVals[i]);
    if (predMonthPlot) { predMonthPlot.destroy(); predMonthPlot = null; }
    if (typeof uPlot !== "undefined") {
      predMonthPlot = new uPlot({
        width: Math.max(300, el.clientWidth || 900), height: 300,
        scales: { x: { time: true } },
        series: [{},
          { label: "Combined", stroke: "#d97706", width: 2.5, spanGaps: true,
            value: (u, v) => (v == null ? "—" : usdShort(v)) },
          { label: "Kalshi (est. $)", stroke: "#06b6d4", width: 1.5, spanGaps: true,
            value: (u, v) => (v == null ? "—" : usdShort(v)) },
          { label: "Polymarket", stroke: "#2563eb", width: 1.5, spanGaps: true,
            value: (u, v) => (v == null ? "—" : usdShort(v)) }],
        axes: [AX, { ...AX, values: (u, vs) => vs.map(usdShort) }],
      }, [xs, cVals, kVals, pVals], el);
    }
    // latest complete month (skip the partial current month)
    const nowYM = new Date().toISOString().slice(0, 7);
    const full = months.filter((m) => m < nowYM);
    const lm = full[full.length - 1];
    if (st) st.textContent =
      (lm ? `Latest full month (${monthLabel(lm)}): combined ${usdShort(km[lm] + pm[lm])} ` +
        `(Kalshi ${usdShort(km[lm])} est. $ + Polymarket ${usdShort(pm[lm])}). ` : "") +
      `Combined monthly $ volume, tracked top-30 markets per venue — not full-venue volume. ` +
      `Kalshi backfilled to market inception; Polymarket tracked from Oct 2026, ` +
      `so combined history before Oct 2026 is Kalshi only.`;
  } catch (e) {
    if (st) st.textContent = "Monthly chart pending — history accumulating.";
  }
}

async function drawActivity() {
  const el = document.getElementById("pred-activity");
  if (!el) return;
  try {
    const [kct, kact, pact, kvol, pvol] = await Promise.all([
      getSeries("cycle:predvolct-kalshi", "max").catch(() => null),
      getSeries("cycle:predact-kalshi", "max").catch(() => null),
      getSeries("cycle:predact-polymarket", "max").catch(() => null),
      getSeries("cycle:predvol-kalshi", "max").catch(() => null),
      getSeries("cycle:predvol-polymarket", "max").catch(() => null),
    ]);
    const ctM = toMonths(sortedPts(kct));
    const kvM = toMonths(sortedPts(kvol)), pvM = toMonths(sortedPts(pvol));
    // active markets: monthly max of the daily tracked count
    const actMax = (s) => {
      const m = {};
      for (const [d, v] of sortedPts(s)) {
        const k = d.slice(0, 7);
        m[k] = Math.max(m[k] || 0, v || 0);
      }
      return m;
    };
    const kaM = actMax(kact), paM = actMax(pact);
    const months = [...new Set([...Object.keys(kvM), ...Object.keys(pvM)])]
      .sort().slice(-6).reverse();
    if (!months.length) {
      el.innerHTML = `<p class="muted">Activity panel pending — history accumulating.</p>`;
      return;
    }
    el.innerHTML = `<table data-sortable><tr><th>Month</th><th>Combined $ vol</th>` +
      `<th>Kalshi $ vol</th><th>Poly $ vol</th><th>Kalshi contracts</th>` +
      `<th>Active mkts (Kalshi)</th><th>Active mkts (Poly)</th></tr>` +
      months.map((m) => `<tr><td>${monthLabel(m)}</td>` +
        `<td>${usdShort((kvM[m] || 0) + (pvM[m] || 0))}</td>` +
        `<td>${usdShort(kvM[m])}</td><td>${usdShort(pvM[m])}</td>` +
        `<td>${ctM[m] == null ? "—" : Math.round(ctM[m]).toLocaleString("en-US")}</td>` +
        `<td>${kaM[m] == null ? "—" : Math.round(kaM[m])}</td>` +
        `<td>${paM[m] == null ? "—" : Math.round(paM[m])}</td></tr>`).join("") +
      `</table><p class="muted">Monthly sums of daily tracked-universe figures (top-30 markets per venue). ` +
      `Kalshi contracts are the venue's native unit; $ volumes are estimated (contracts × price). ` +
      `Individual wager/trade counts are not published by either venue's free API — not shown, not estimated.</p>`;
  } catch (e) {
    el.innerHTML = `<p class="muted">Activity panel pending — history accumulating.</p>`;
  }
}

// ---- FOMC pricing: prediction markets vs Fed funds futures (BI-style) ----

function fedBars(groups) {
  // groups: [[venueLabel, {hike,hold,cut}|null, sourceLabel], ...]
  const outs = [["hike", "Hike ≥25bp", "#2563eb"], ["hold", "Hold", "#f59e0b"],
                ["cut", "Cut ≥25bp", "#64748b"]];
  return `<div style="display:flex;gap:28px;flex-wrap:wrap;align-items:flex-end">` +
    groups.map(([venue, probs, src]) => {
      const vals = outs.map(([k]) => (probs && probs[k] != null ? probs[k] : null));
      const bars = outs.map(([k, lbl, col], i) => {
        const v = vals[i];
        return `<div style="flex:1;min-width:52px;text-align:center">` +
          `<div style="font-size:12px;font-weight:600">${v == null ? "—" : v.toFixed(1) + "%"}</div>` +
          `<div style="height:${v == null ? 4 : Math.max(4, v * 2.2)}px;background:${col};` +
          `border-radius:4px 4px 0 0;margin:4px 0"></div>` +
          `<div style="font-size:11px;color:#6b7280">${lbl}</div></div>`;
      }).join("");
      return `<div style="flex:1;min-width:220px"><div style="font-weight:600;margin-bottom:6px">${venue}</div>` +
        `<div style="display:flex;gap:8px">${bars}</div>` +
        `<div class="muted" style="font-size:11px;margin-top:4px">${src}</div></div>`;
    }).join("") + `</div>`;
}

function fedTakeaway(fm) {
  const g = [
    ["Kalshi", fm.kalshi?.probs], ["Polymarket", fm.polymarket?.probs],
    ["Fed funds futures", fm.fed_funds?.probs],
  ].filter(([, p]) => p);
  if (g.length < 2) return "Pricing comparison pending — venues still loading.";
  const lead = ([, p]) => ["hike", "hold", "cut"].reduce((a, b) => (p[a] >= p[b] ? a : b));
  const leaders = g.map(([v, p]) => [v, lead([v, p]), p[lead([v, p])]]);
  const allAgree = leaders.every(([, l]) => l === leaders[0][1]);
  const rng = (k) => {
    const vs = g.map(([, p]) => p[k]);
    return `${Math.min(...vs).toFixed(0)}–${Math.max(...vs).toFixed(0)}%`;
  };
  const lbl = fm.next_meeting?.label ?? "the meeting";
  if (allAgree) {
    const l = leaders[0][1];
    return `All ${g.length} venues agree the burden of proof for ${lbl} sits with ` +
      `${l === "hold" ? "a move" : l === "hike" ? "a hold" : "a hold"}: ` +
      `${l} priced ${rng(l)} across venues (hike ${rng("hike")}, hold ${rng("hold")}, cut ${rng("cut")}).`;
  }
  return `Venues disagree into ${lbl}: ` +
    leaders.map(([v, l, x]) => `${v} leans ${l} (${x.toFixed(0)}%)`).join("; ") + `.`;
}

function drawFedChart(fm) {
  const el = document.getElementById("pred-fed");
  if (!el) return;
  if (!fm || !fm.next_meeting) {
    el.innerHTML = `<p class="muted">FOMC pricing pending — fills in on the next fetcher run.</p>`;
    return;
  }
  const ffSrc = fm.fed_funds?.manual
    ? `Manual input (${fm.fed_funds.snapshot_date ?? fm.fed_funds.asof ?? "undated"})`
    : `CME FedWatch via third-party archive` +
      (fm.fed_funds?.snapshot_date ? `, snapshot ${fm.fed_funds.snapshot_date}` : "") +
      (fm.fed_funds?.stale ? ` — STALE` : "");
  const groups = [
    ["Kalshi", fm.kalshi?.probs ?? null,
     fm.kalshi?.event ? `Event ${fm.kalshi.event}` : "event not found"],
    ["Polymarket", fm.polymarket?.probs ?? null,
     fm.polymarket?.slug ? `Slug ${fm.polymarket.slug}` : "event not found"],
    ["Fed funds futures", fm.fed_funds?.probs ?? null, ffSrc],
  ];
  el.innerHTML =
    `<p><strong>${fm.next_meeting.label} FOMC decision</strong> ` +
    `<span class="muted">(decision ${fm.next_meeting.date}; auto-rolls to the next meeting after)</span></p>` +
    fedBars(groups) +
    `<p style="margin-top:10px"><strong>Takeaway:</strong> ${fedTakeaway(fm)}</p>` +
    `<p class="muted">Probabilities are market-implied, not forecasts. ` +
    `Kalshi legs (H25+H26 / H0 / C25+C26) are mutually exclusive; ` +
    `Polymarket sums the matching outcome legs. Futures leg is CME FedWatch ` +
    `via a third-party GitHub archive (not official CME data)` +
    (fm.fed_funds?.manual ? `; this meeting uses manual input` : "") + `.</p>`;
}
