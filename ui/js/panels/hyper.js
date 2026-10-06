// HYPER tab: the hyperscaler desk. Equity cards for the six AI hyperscalers
// (price + 1m change + 90d sparkline, from the hyper doc's equity cards;
// short interest per card from FINRA's free twice-monthly file, merged in
// by the collector) and a debt-issuance monitor table parsed from SEC
// filings (424B2/424B3/424B5/FWP).
// Honest limits are shown, not faked: no free single-name bond spread/CDS
// feed exists; holder flows are covered by the 13F watchlist.
import { fmtAge, isStale } from "../fmt.js";

const STALE_MINUTES = 10080; // 2x the weekly hyperscaler cadence

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function sparkSvg(spark) {
  if (!spark || spark.length < 2) return `<span class="muted">—</span>`;
  const W = 110, H = 28;
  const vals = spark.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const rng = hi - lo || 1;
  const up = vals[vals.length - 1] >= vals[0];
  const pts = spark.map((p, i) =>
    `${(i / (spark.length - 1) * W).toFixed(1)},${(H - 2 - ((p[1] - lo) / rng) * (H - 4)).toFixed(1)}`);
  const color = up ? "#2fb56b" : "#e05252";
  return `<svg viewBox="0 0 ${W} ${H}" class="hyper-spark" aria-hidden="true">` +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

function fmtChg(v) {
  if (v == null) return `<span class="muted">—</span>`;
  const cls = v >= 0 ? "up" : "down";
  return `<span class="${cls}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</span>`;
}

function fmtUsd(v) {
  if (v == null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  return `$${(v / 1e6).toFixed(0)}M`;
}

function issuanceRows(issuances) {
  if (!issuances.length)
    return `<tr><td colspan="6" class="muted">No debt filings in the last 90 days.</td></tr>`;
  return issuances.slice(0, 12).map((e) => {
    const tranches = (e.tranches || []).slice(0, 4).map((t) =>
      `${t.coupon_pct != null ? t.coupon_pct.toFixed(3) + "%" : "—"}` +
      ` ${t.maturity_year ?? "—"} ${fmtUsd(t.principal_usd)}`).join("<br>");
    const parsed = e.parsed === "yes" ? "" : ` <span class="muted">(filing only)</span>`;
    return `<tr>
      <td>${esc(e.filing_date)}</td>
      <td><strong>${esc(e.issuer)}</strong></td>
      <td>${esc(e.form)}${parsed}</td>
      <td class="num">${tranches || "—"}</td>
      <td><a href="${esc(e.url)}" target="_blank" rel="noopener">SEC&nbsp;↗</a></td>
    </tr>`;
  }).join("");
}

function fmtShort(s) {
  if (!s || s.short == null) return `<span class="muted">short: —</span>`;
  const sh = s.short >= 1e6 ? `${(s.short / 1e6).toFixed(1)}M sh` : `${Math.round(s.short / 1e3)}K sh`;
  const dtc = s.dtc != null ? ` · ${s.dtc.toFixed(1)}d to cover` : "";
  const chg = s.chg_pct != null ? ` <span class="${s.chg_pct >= 0 ? "up" : "down"}">${s.chg_pct >= 0 ? "+" : ""}${s.chg_pct.toFixed(1)}%</span>` : "";
  return `<span title="FINRA short interest, settlement">short: ${sh}${dtc}</span>${chg}`;
}

export function renderHyper(hyper) {
  const body = document.querySelector("#panel-hyper .panel-body");
  const footEl = document.querySelector("#panel-hyper .panel-foot");
  if (!body) return;
  const equities = hyper.equities ?? [];
  const issuances = hyper.issuances ?? [];
  if (!equities.length && !issuances.length) {
    body.innerHTML = `<div class="empty-state">NO DATA — hyperscaler job has not run yet</div>`;
    if (footEl) footEl.textContent = "DATA: —";
    return;
  }
  const cards = equities.map((e) => `
    <div class="hyper-card">
      <div class="hyper-card-head"><strong>${esc(e.ticker)}</strong>
      <span class="muted">${esc(e.label)}</span></div>
      <div class="hyper-card-price">${e.last == null ? "—" : "$" + e.last.toFixed(2)}
      ${fmtChg(e.chg_1m_pct)} <span class="muted">1m</span></div>
      <div class="hyper-card-short">${fmtShort(e.short)}</div>
      ${sparkSvg(e.spark)}
    </div>`).join("");
  body.innerHTML = `
    <div class="hyper-cards">${cards}</div>
    <div class="hyper-section-title">DEBT ISSUANCE — SEC FILINGS (90D)</div>
    <table class="hyper-table" data-sortable>
      <thead><tr><th>DATE</th><th>ISSUER</th><th>FORM</th><th>TRANCHES (coupon · maturity · size)</th><th></th></tr></thead>
      <tbody>${issuanceRows(issuances)}</tbody>
    </table>
    <div class="muted" style="font-size:10px">${esc(hyper.note ?? "")}</div>`;
  if (footEl) {
    const src = (hyper.source ?? "—").toUpperCase();
    footEl.textContent = `DATA: ${src} · ${fmtAge(hyper.updated_at)}`;
    footEl.classList.toggle("stale", isStale(hyper.updated_at, STALE_MINUTES));
  }
}
