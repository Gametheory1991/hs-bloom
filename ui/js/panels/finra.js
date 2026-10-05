// FINRA tab: every FINRA-sourced dataset in one place.
// Reads dash.panels.finra (backend _finra_panel).
// Sections: Reg SHO short volume, threshold list, short interest,
// market breadth, most-active corporate bonds, capped volume,
// margin statistics, TRACE treasury/monthly, TRACE volume charts.
import { renderTraceCharts } from "./trace_charts.js";
const big = (x) =>
  x == null ? "—" : x.toLocaleString("en-US", { maximumFractionDigits: 0 });
const pct1 = (x) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const usdM = (x) =>
  x == null ? "—" : "$" + (x / 1e6).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "M";

function spark(hist, w = 220, h = 44) {
  if (!hist || hist.length < 2) return `<span class="muted">no history</span>`;
  const vs = hist.map((p) => p.v).filter((v) => v != null);
  if (vs.length < 2) return `<span class="muted">no history</span>`;
  const lo = Math.min(...vs), hi = Math.max(...vs), rng = hi - lo || 1;
  const pts = hist
    .map((p, i) => {
      if (p.v == null) return null;
      const x = (i / (hist.length - 1)) * w;
      const y = h - 3 - ((p.v - lo) / rng) * (h - 6);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  const last = vs[vs.length - 1];
  const cls = last >= vs[0] ? "up" : "down";
  return `<svg width="${w}" height="${h}" class="spark"><polyline points="${pts}" fill="none" stroke="currentColor" class="${cls}" stroke-width="1.5"/></svg>`;
}

function regshoSection(r) {
  if (!r) return `<h3>SHORT VOLUME — REG SHO DAILY</h3><p class="muted">No Reg SHO data yet.</p>`;
  const mkts = r.markets ?? {};
  const rows = Object.entries(mkts).map(([k, m]) =>
    `<tr><td>${m.label ?? k}</td><td>${big(m.short)}</td><td>${big(m.total)}</td>` +
    `<td>${pct1(m.ratio)}</td></tr>`).join("");
  const top = (r.top50 ?? []).map((t) =>
    `<tr><td><b>${t.symbol}</b></td><td>${big(t.short_volume)}</td>` +
    `<td>${pct1(t.short_ratio)}</td></tr>`).join("");
  return `<h3>SHORT VOLUME — REG SHO DAILY <span class="muted">as of ${r.as_of ?? "—"}</span></h3>
    <table><tr><th>Market</th><th>Short vol</th><th>Total vol</th><th>Short ratio</th></tr>${rows}</table>
    <h3>TOP SHORTED TICKERS</h3>
    <table><tr><th>Symbol</th><th>Short vol</th><th>Short ratio</th></tr>${top}</table>`;
}

function thresholdSection(t) {
  if (!t) return `<h3>THRESHOLD LIST — REG SHO</h3><p class="muted">No threshold data yet.</p>`;
  const secs = (t.securities ?? []).map((s) =>
    `<tr><td><b>${s.symbol}</b></td><td>${(s.name ?? "").slice(0, 50)}</td>` +
    `<td>${s.category ?? "—"}</td></tr>`).join("");
  return `<h3>THRESHOLD LIST — REG SHO <span class="muted">${t.count} securities as of ${t.as_of ?? "—"}</span></h3>
    ${secs ? `<table><tr><th>Symbol</th><th>Name</th><th>Category</th></tr>${secs}</table>` : ""}`;
}

function shortInterestSection(s) {
  if (!s) return `<h3>SHORT INTEREST — FINRA</h3><p class="muted">No short-interest data yet.</p>`;
  return `<h3>SHORT INTEREST — FINRA <span class="muted">settlement ${s.as_of ?? "—"}</span></h3>
    <table><tr><th>Total short shares</th></tr>
    <tr><td>${big(s.total_short_shares)}</td></tr></table>`;
}

function breadthSection(b) {
  if (!b) return `<h3>MARKET BREADTH — FINRA</h3><p class="muted">No breadth data yet.</p>`;
  return `<h3>MARKET BREADTH <span class="muted">as of ${b.as_of ?? "—"} · ${b.series_count ?? 0} series</span></h3>
    <table><tr><th>Corp A/D spread (60d)</th></tr>
    <tr><td>${spark(b.adspread_hist)}</td></tr></table>`;
}

function corpSection(c) {
  if (!c) return `<h3>MOST-ACTIVE CORPORATE BONDS</h3><p class="muted">No corporate activity data yet.</p>`;
  const lists = c.lists ?? {};
  const html = Object.entries(lists).map(([slug, l]) => {
    const bonds = (l.bonds ?? []).map((bd) => {
      const cpn = bd.coupon != null ? `${Number(bd.coupon).toFixed(3)}%` : "—";
      const mat = (bd.maturity ?? "").slice(0, 10) || "—";
      const yld = bd.yield != null ? `${Number(bd.yield).toFixed(2)}%` : "—";
      const px = bd.last != null ? Number(bd.last).toFixed(2) : "—";
      return `<tr><td><b>${bd.symbol ?? "—"}</b></td><td>${(bd.issuer ?? "").slice(0, 40)}</td>` +
        `<td>${cpn}</td><td>${mat}</td><td>${yld}</td><td>${px}</td></tr>`;
    }).join("");
    return `<h3>MOST ACTIVE — ${slug.toUpperCase()} <span class="muted">${l.as_of ?? ""} · ${l.count} bonds</span></h3>` +
      (bonds ? `<table><tr><th>Symbol</th><th>Issuer</th><th>Coupon</th><th>Maturity</th><th>Yield</th><th>Price</th></tr>${bonds}</table>`
             : `<p class="muted">No bond rows.</p>`);
  }).join("");
  return `<h3>CORPORATE ACTIVITY — FINRA <span class="muted">as of ${c.as_of ?? "—"}</span></h3>` + html;
}

const GRADE_LABELS = { ig: "Investment Grade", hy: "High Yield", agcy: "Agency", "144a-ig": "144A IG", "144a-hy": "144A HY" };

function cappedSection(c) {
  if (!c) return `<h3>CAPPED VOLUME REPORT</h3><p class="muted">No capped-volume data yet — September pull pending.</p>`;
  const grades = c.grades ?? {};
  const rows = Object.entries(grades).map(([slug, g]) =>
    `<tr><td>${GRADE_LABELS[slug] ?? slug}</td>` +
    `<td>${g.avgsize == null ? "—" : "$" + Number(g.avgsize).toLocaleString("en-US", { maximumFractionDigits: 0 }) + "k"}</td>` +
    `<td>${usdM(g.total)}</td></tr>`).join("");
  return `<h3>CAPPED VOLUME — MONTHLY <span class="muted">${c.as_of ?? "—"} · ${c.months ?? 0} months</span></h3>
    ${rows ? `<table><tr><th>Grade</th><th>Avg capped size</th><th>Total par</th></tr>${rows}</table>`
           : `<p class="muted">No grade rows — September pull pending.</p>`}`;
}

function marginSection(m) {
  if (!m) return `<h3>MARGIN STATISTICS</h3><p class="muted">No margin data yet.</p>`;
  const debit = m.latest_debit_m;
  return `<h3>MARGIN STATISTICS <span class="muted">as of ${m.as_of ?? "—"}</span></h3>
    <table><tr><th>Debit balances</th><th>Trend (26 pts)</th></tr>
    <tr><td>${debit == null ? "—" : "$" + (debit / 1000).toFixed(1) + "B"}</td>
    <td>${spark(m.debit_hist)}</td></tr></table>
    <p class="muted">Debit balances in $M; chart shows recent history.</p>`;
}

function traceSection(t, mo) {
  const tHtml = t
    ? `<tr><td>Treasury TRACE daily</td><td>${t.as_of ?? "—"}</td><td>${t.series_count ?? 0} series</td><td class="up">live</td></tr>`
    : `<tr><td>Treasury TRACE daily</td><td>—</td><td>—</td><td class="flat">no data</td></tr>`;
  const mHtml = mo && !mo.blocked
    ? `<tr><td>TRACE monthly (all products)</td><td>${mo.as_of ?? "—"}</td><td>—</td><td class="up">live</td></tr>`
    : `<tr><td>TRACE monthly (all products)</td><td>${(mo && mo.as_of) || "—"}</td><td>—</td><td class="down">blocked — FINRA CDN 403</td></tr>`;
  return `<h3>TRACE VOLUMES</h3>
    <table><tr><th>Feed</th><th>As of</th><th>Series</th><th>Status</th></tr>${tHtml}${mHtml}</table>`;
}

export function renderFinra(p) {
  const body = document.querySelector("#panel-finra .panel-body");
  if (!body) return;
  const f = p ?? {};
  body.innerHTML =
    `<div id="trace-charts-root"></div>` +
    regshoSection(f.regsho) +
    thresholdSection(f.threshold) +
    shortInterestSection(f.short_interest) +
    breadthSection(f.breadth) +
    corpSection(f.corp) +
    cappedSection(f.capped) +
    marginSection(f.margin) +
    traceSection(f.trace_treasury, f.trace_monthly);
  renderTraceCharts();
}
