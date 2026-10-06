// POSITIONING → Short Interest focused view.
// Reads dash.panels.shortinterest (backend _shortinterest_panel).
// Sections: Reg SHO daily short volume (markets + top-50 + trend chart),
// OTC threshold list, FINRA biweekly short-interest settlement (total +
// watchlist detail). Light charts per house style; every comparison names
// its period explicitly. Degrades to "no data yet" per section.
import { getSeries } from "../api.js";

const big = (x) =>
  x == null ? "—" : Number(x).toLocaleString("en-US", { maximumFractionDigits: 0 });
const pct1 = (x) => (x == null ? "—" : `${(Number(x) * 100).toFixed(1)}%`);
const chg1 = (x) =>
  x == null ? "—" : `${Number(x) > 0 ? "+" : ""}${Number(x).toFixed(1)}%`;
const esc = (s) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const state = { regshoRange: "1y", volPlot: null, totalPlot: null, regshoReq: 0, totalReq: 0 };

// Nominal+% delta: "+1.2M (+3.4%)".
const dCellSh = (nom, pct) => {
  if (nom == null && (pct == null || !isFinite(pct))) return "—";
  const n = nom == null ? "—" : `${nom >= 0 ? "+" : "−"}${Math.abs(Math.round(nom)).toLocaleString("en-US")}`;
  const p = pct == null || !isFinite(pct) ? "—" : `${pct > 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`;
  if (n === "—") return p;
  if (p === "—") return n;
  return `${n} <span class="muted">(${p})</span>`;
};
// 1D delta vs previous point from daily {d,v} history.
function d1Delta(pts) {
  if (!pts || pts.length < 2) return { nom: null, pct: null };
  const cur = pts[pts.length - 1], prev = pts[pts.length - 2];
  if (!prev.v) return { nom: null, pct: null };
  return { nom: cur.v - prev.v, pct: (cur.v - prev.v) / prev.v };
}

function regshoSection(r) {
  if (!r)
    return `<h3>SHORT VOLUME — REG SHO DAILY</h3><p class="muted">No Reg SHO data yet.</p>`;
  const rows = Object.entries(r.markets ?? {})
    .map(
      ([k, m]) =>
        `<tr data-mkt="${k}"><td>${esc(m.label ?? k)}</td><td>${big(m.short)}</td>` +
        `<td>${big(m.total)}</td><td>${pct1(m.ratio)}</td>` +
        `<td class="num" data-d1short>…</td><td class="num" data-d1ratio>…</td></tr>`
    )
    .join("");
  const top = (r.top50 ?? [])
    .map(
      (t) =>
        `<tr data-sym="${esc(t.symbol)}"><td><b>${esc(t.symbol)}</b></td><td>${big(t.short_volume)}</td>` +
        `<td>${big(t.total_volume)}</td><td>${pct1(t.short_ratio)}</td>` +
        `<td class="num" data-d1svol>…</td><td class="num" data-d1sratio>…</td></tr>`
    )
    .join("");
  return `<h3>SHORT VOLUME — REG SHO DAILY <span class="muted">prior trading day · as of ${esc(r.as_of ?? "—")}</span></h3>
    <table data-sortable><tr><th>Market</th><th>Short vol (sh)</th><th>Total vol (sh)</th><th>Short ratio</th><th>1D Δ short vol</th><th>1D Δ ratio</th></tr>${rows}</table>
    <div id="si-regsho-chart-wrap">
      <div class="seg" id="si-regsho-range">
        <button data-range="1y" class="on">1Y</button><button data-range="max">Max</button>
      </div>
      <div id="si-regsho-chart" class="trace-chart"></div>
      <div id="si-regsho-stats" class="muted"></div>
    </div>
    <h3>TOP SHORTED TICKERS <span class="muted">by daily short volume · ${esc(r.as_of ?? "—")}</span></h3>
    <table data-sortable><tr><th>Symbol</th><th>Short vol (sh)</th><th>Total vol (sh)</th><th>Short ratio</th><th>1D Δ vol</th><th>1D Δ ratio</th></tr>${top}</table>`;
}
// Fill 1D deltas for Reg SHO markets + top tickers (async, after render).
async function fillRegshoDeltas() {
  const mktMap = { cnms: "cnms", fnyx: "fnyx", fnsq: "fnsq" };
  document.querySelectorAll("#panel-shortinterest tr[data-mkt]").forEach(async (tr) => {
    const k = (mktMap[tr.dataset.mkt] || tr.dataset.mkt).toLowerCase();
    try {
      const [sv, sr] = await Promise.all([
        getSeries(`regsho-${k}-shortvol`, "1m").catch(() => null),
        getSeries(`regsho-${k}-shortratio`, "1m").catch(() => null),
      ]);
      const dS = d1Delta((sv?.points ?? []).map((p) => ({ d: p[0], v: p[1] })));
      const dR = d1Delta((sr?.points ?? []).map((p) => ({ d: p[0], v: p[1] })));
      const c1 = tr.querySelector("[data-d1short]"), c2 = tr.querySelector("[data-d1ratio]");
      if (c1) c1.innerHTML = dCellSh(dS.nom, dS.pct);
      if (c2) c2.innerHTML = dCellSh(dR.nom == null ? null : dR.nom * 100, dR.pct);
    } catch { /* leave placeholder */ }
  });
  document.querySelectorAll("#panel-shortinterest tr[data-sym]").forEach(async (tr) => {
    const sym = tr.dataset.sym;
    try {
      const [sv, sr] = await Promise.all([
        getSeries(`regsho-top-${sym}-shortvol`, "1m").catch(() => null),
        getSeries(`regsho-top-${sym}-totalvol`, "1m").catch(() => null),
      ]);
      const sp = (sv?.points ?? []).map((p) => ({ d: p[0], v: p[1] }));
      const tp = (sr?.points ?? []).map((p) => ({ d: p[0], v: p[1] }));
      const dS = d1Delta(sp);
      // ratio delta: short/total at cur vs prev
      let dR = { nom: null, pct: null };
      if (sp.length >= 2 && tp.length >= 2) {
        const rc = sp[sp.length - 1].v / (tp[tp.length - 1].v || 1);
        const rp = sp[sp.length - 2].v / (tp[tp.length - 2].v || 1);
        dR = { nom: (rc - rp) * 100, pct: rp ? (rc - rp) / rp : null };
      }
      const c1 = tr.querySelector("[data-d1svol]"), c2 = tr.querySelector("[data-d1sratio]");
      if (c1) c1.innerHTML = dCellSh(dS.nom, dS.pct);
      if (c2) c2.innerHTML = dCellSh(dR.nom, dR.pct == null ? null : dR.pct);
    } catch { /* leave placeholder */ }
  });
}

function thresholdSection(t) {
  if (!t)
    return `<h3>THRESHOLD LIST — REG SHO</h3><p class="muted">No threshold data yet.</p>`;
  const secs = t.securities ?? [];
  const rows = secs
    .map(
      (s) =>
        `<tr><td><b>${esc(s.symbol)}</b></td><td>${esc((s.name ?? "").slice(0, 48))}</td>` +
        `<td>${esc(s.category ?? "—")}</td><td>${s.reg_sho ? "Y" : "—"}</td>` +
        `<td>${s.rule4320 ? "Y" : "—"}</td></tr>`
    )
    .join("");
  const note =
    t.count > secs.length ? ` <span class="muted">showing ${secs.length} of ${t.count}</span>` : "";
  return `<h3>THRESHOLD LIST — REG SHO <span class="muted">${t.count} securities as of ${esc(t.as_of ?? "—")}</span>${note}</h3>
    ${rows ? `<table data-sortable><tr><th>Symbol</th><th>Name</th><th>Category</th><th>Reg SHO</th><th>Rule 4320</th></tr>${rows}</table>` : `<p class="muted">List is empty.</p>`}`;
}

function shortInterestSection(s) {
  if (!s)
    return `<h3>SHORT INTEREST — FINRA SETTLEMENT</h3><p class="muted">No short-interest data yet.</p>`;
  const rows = Object.entries(s.tickers ?? {})
    .sort((a, b) => (b[1].short ?? 0) - (a[1].short ?? 0))
    .map(([sym, v]) => {
      const nom = v.short != null && v.prev != null ? v.short - v.prev : null;
      const n = nom == null ? "—" : `${nom > 0 ? "+" : ""}${big(Math.abs(nom))}`;
      const p = v.chg_pct == null ? "—" : `${v.chg_pct > 0 ? "+" : ""}${(v.chg_pct * 100).toFixed(1)}%`;
      return `<tr><td><b>${esc(sym)}</b></td><td>${big(v.short)}</td>` +
        `<td>${n} <span class="muted">(${p})</span></td><td>${big(v.adv)}</td><td>${v.dtc ?? "—"}</td></tr>`;
    })
    .join("");
  return `<h3>SHORT INTEREST — FINRA SETTLEMENT <span class="muted">as of ${esc(s.as_of ?? "—")}</span></h3>
    <p class="muted">Biweekly settlement (15th and last business day of month), published ~8 business days later. Levels, not flow — compare with Reg SHO daily flow above.</p>
    <table><tr><th>Total short shares (all listed)</th></tr><tr><td>${big(s.total_short_shares)}</td></tr></table>
    <div id="si-total-chart" class="trace-chart"></div>
    <h3>WATCHLIST <span class="muted">change vs prior settlement</span></h3>
    <table data-sortable><tr><th>Ticker</th><th>Short (sh)</th><th>Δ vs prior settl.</th><th>Avg daily vol (sh)</th><th>Days to cover</th></tr>${rows}</table>`;
}

function mergeDates(a, b) {
  const byDate = new Map();
  for (const [d, v] of a) byDate.set(d, [v, null]);
  for (const [d, v] of b) {
    const cur = byDate.get(d) ?? [null, null];
    cur[1] = v;
    byDate.set(d, cur);
  }
  const dates = [...byDate.keys()].sort();
  return [dates.map((d) => Date.parse(d) / 1000), dates.map((d) => byDate.get(d)[0]), dates.map((d) => byDate.get(d)[1]), dates];
}

function drawPlot(el, xs, y1, y2, label1, label2, isPct) {
  if (typeof uPlot === "undefined") return null;
  const w = Math.max(el.clientWidth || 640, 280);
  const opts = {
    width: w,
    height: 240,
    scales: { x: { time: true }, y: {}, y2: {} },
    series: [
      {},
      { label: label1, stroke: "#2563eb", width: 1.5, scale: "y", spanGaps: true },
      {
        label: label2, stroke: "#06b6d4", width: 1.5, scale: "y2", spanGaps: true,
        value: (u, v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`),
      },
    ],
    axes: [
      { stroke: "#94a3b8", grid: { stroke: "#e5e9f0" } },
      { scale: "y", stroke: "#94a3b8", grid: { stroke: "#e5e9f0" },
        values: (u, vs) => vs.map((v) => (v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : `${(v / 1e6).toFixed(0)}M`)) },
      ...(isPct
        ? [{ side: 1, scale: "y2", stroke: "#94a3b8",
             values: (u, vs) => vs.map((v) => `${(v * 100).toFixed(0)}%`) }]
        : []),
    ],
  };
  return new uPlot(opts, [xs, y1, y2], el);
}

async function drawRegshoChart() {
  const el = document.getElementById("si-regsho-chart");
  const statsEl = document.getElementById("si-regsho-stats");
  if (!el) return;
  const req = ++state.regshoReq;
  try {
    const [vol, ratio] = await Promise.all([
      getSeries("regsho-cnms-shortvol", state.regshoRange),
      getSeries("regsho-cnms-shortratio", state.regshoRange),
    ]);
    if (req !== state.regshoReq) return;
    const [xs, y1, y2, dates] = mergeDates(vol.points ?? [], ratio.points ?? []);
    if (state.volPlot) { state.volPlot.destroy(); state.volPlot = null; }
    el.innerHTML = "";
    state.volPlot = drawPlot(el, xs, y1, y2, "Short vol (sh)", "Short ratio", true);
    if (statsEl && dates.length) {
      const rs = dates.map((d, i) => y2[i]).filter((v) => v != null);
      if (rs.length) {
        const avg = (n) => rs.slice(-n).reduce((a, b) => a + b, 0) / Math.min(n, rs.length);
        const lastD = dates[dates.length - 1];
        statsEl.textContent =
          `Consolidated short ratio — latest ${pct1(rs[rs.length - 1])} · 1W avg ${pct1(avg(5))} · 1M avg ${pct1(avg(21))} (windows ending ${lastD})`;
      }
    }
  } catch {
    if (statsEl) statsEl.textContent = "Chart unavailable.";
  }
}

async function drawTotalChart() {
  const el = document.getElementById("si-total-chart");
  if (!el) return;
  const req = ++state.totalReq;
  try {
    const s = await getSeries("finra-short-total", "max");
    if (req !== state.totalReq) return;
    const pts = (s.points ?? []).sort((a, b) => (a[0] < b[0] ? -1 : 1));
    const xs = pts.map((p) => Date.parse(p[0]) / 1000);
    const ys = pts.map((p) => p[1]);
    if (state.totalPlot) { state.totalPlot.destroy(); state.totalPlot = null; }
    el.innerHTML = "";
    if (typeof uPlot !== "undefined" && xs.length > 1) {
      const w = Math.max(el.clientWidth || 640, 280);
      state.totalPlot = new uPlot(
        {
          width: w, height: 200,
          scales: { x: { time: true } },
          series: [{}, { label: "Total short (sh)", stroke: "#2563eb", width: 1.5, spanGaps: true }],
          axes: [{ stroke: "#94a3b8", grid: { stroke: "#e5e9f0" } },
                 { stroke: "#94a3b8", grid: { stroke: "#e5e9f0" },
                   values: (u, vs) => vs.map((v) => `${(v / 1e9).toFixed(1)}B`) }],
        },
        [xs, ys], el);
    }
  } catch { /* chart is optional */ }
}

export function renderShortInterest(p) {
  const body = document.querySelector("#panel-shortinterest .panel-body");
  if (!body) return;
  const s = p ?? {};
  body.innerHTML =
    regshoSection(s.regsho) + thresholdSection(s.threshold) + shortInterestSection(s.short_interest);
  const rangeBtns = body.querySelectorAll("#si-regsho-range button");
  rangeBtns.forEach((b) => {
    b.classList.toggle("on", b.dataset.range === state.regshoRange);
    b.onclick = () => {
      if (state.regshoRange === b.dataset.range) return;
      state.regshoRange = b.dataset.range;
      rangeBtns.forEach((x) => x.classList.toggle("on", x === b));
      drawRegshoChart();
    };
  });
  drawRegshoChart();
  drawTotalChart();
  fillRegshoDeltas();
}
