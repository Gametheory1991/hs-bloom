// ETF HOLDERS: inverse 13F. Who holds each ETF, Bloomberg Holdings-tab style.
// Quarterly Form 13F filings (up to 45-day reporting lag), institutions with
// over $100M discretionary AUM only (no retail, no non-filing holders).
// Data: /api/dashboard "etfholders" panel (per-symbol latest-quarter summary
// for the picker + the 6 Bloomberg columns) + /api/etf-holders/<TICKER> for
// the full holder table. Renders as a section inside the ETF FLOWS page.
import { getEtfHolders } from "../api.js";
import { heatStyle } from "../heatmap.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function shares(v) {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(v);
}

function money(v) {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  return `$${v.toFixed(0)}`;
}

function pct(v, digits = 1) {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}%`;
}

function kpiTile(label, val, sub) {
  return `<div class="kpi"><div class="kpi-label">${label}</div>` +
    `<div class="kpi-value">${val ?? "—"}</div>` +
    `<div class="kpi-sub muted">${sub ?? ""}</div></div>`;
}

const LAG_NOTE = "Quarterly Form 13F filings · up to 45-day reporting lag · " +
  "institutions with over $100M discretionary AUM only (no retail)";

let picked = null; // selected ticker; defaults to the first covered ETF
let tableCache = {}; // ticker -> /api/etf-holders payload

function summaryKpis(s) {
  if (!s || s.inst_shares_held == null) return "";
  const chg = s.pct_chg_inst;
  return `<div class="kpirow">` +
    kpiTile("Inst Shares Held", shares(s.inst_shares_held), "sum of reported holders") +
    kpiTile("% Chg of Inst Holdings", pct(chg), "vs previous quarter reported") +
    kpiTile("Inst Holdings (% of o/s Shrs)", s.pct_of_os == null ? "—" : s.pct_of_os.toFixed(1) + "%", "sum of holder % stakes") +
    kpiTile("Num of Inst Holders", s.n_holders, "top 50 shown") +
    kpiTile("Num of Inst Buyers", s.n_buyers, "raised shares QoQ") +
    kpiTile("Num of Inst Sellers", s.n_sellers, "cut shares QoQ") +
    `</div>`;
}

function holderTable(holders) {
  if (!holders.length) {
    return `<div class="muted">No holder rows stored for this quarter.</div>`;
  }
  const rows = holders.map((h) => {
    const chg = h.changeInSharesNumber;
    const chgPct = (chg != null && h.sharesNumber - chg > 0)
      ? (chg / (h.sharesNumber - chg)) * 100 : null;
    const chgCell = chg == null
      ? `<td class="num muted">—</td>`
      : `<td class="num"${heatStyle({ z: chg === 0 ? 0 : (chg > 0 ? 1 : -1) })}>` +
        `<b>${chg >= 0 ? "+" : "−"}${shares(Math.abs(chg))}</b>` +
        (chgPct == null ? "" : `<br><span class="muted">${pct(chgPct)}</span>`) + `</td>`;
    return `<tr>` +
      `<td>${esc(h.investorName)}</td>` +
      `<td class="num"><b>${shares(h.sharesNumber)}</b></td>` +
      chgCell +
      `<td class="num">${h.ownership == null ? "—" : h.ownership.toFixed(2) + "%"}</td>` +
      `<td class="num">${money(h.marketValue)}</td>` +
      `<td class="muted">${esc(h.filingDate ?? "—")}</td></tr>`;
  }).join("");
  return `<div style="overflow-x:auto"><table class="etftable" data-sortable><thead><tr>` +
    `<th>Institution</th><th>Shares Held</th><th>Chg Shares (nominal + %)</th>` +
    `<th>% of O/S</th><th>Mkt Value</th><th>Filing Date</th>` +
    `</tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function renderBody(el, doc) {
  const symbols = doc.symbols || {};
  const covered = Object.keys(symbols).sort();
  const coverage = doc.coverage || {};
  if (!covered.length) {
    el.innerHTML = `<div class="muted">No holder data yet. The ` +
      `etf_holders_13f job pulls each quarter after the 45-day 13F filing ` +
      `window (first fill lands once filings are in). ${esc(doc.note ?? "")}</div>`;
    return;
  }
  if (!picked || !symbols[picked]) picked = covered[0];
  const pending = (coverage.universe ?? 0) - (coverage.covered ?? 0);
  const opts = covered.map((t) =>
    `<option value="${t}"${t === picked ? " selected" : ""}>${t}</option>`).join("");
  const s = symbols[picked];
  const qlabel = `Q${s.quarter} ${s.year}`;

  el.innerHTML =
    `<div class="btnrow" style="margin:6px 0">` +
    `<label class="muted" for="eh-pick">ETF</label> ` +
    `<select id="eh-pick">${opts}</select>` +
    `<span class="muted">quarter: <b>${qlabel}</b> · ` +
    `${coverage.covered ?? covered.length} of ${coverage.universe ?? "?"} ETFs covered` +
    (pending > 0 ? ` · ${pending} pending` : "") + `</span></div>` +
    `<div id="eh-detail"><div class="muted">Loading ${esc(picked)}…</div></div>`;

  el.querySelector("#eh-pick").addEventListener("change", (ev) => {
    picked = ev.target.value;
    renderBody(el, doc);
  });

  const det = el.querySelector("#eh-detail");
  try {
    let d = tableCache[picked];
    if (!d) { d = await getEtfHolders(picked); tableCache[picked] = d; }
    det.innerHTML =
      summaryKpis(d.summary) +
      `<div class="panel-subhead"><span>HOLDERS — ${esc(picked)} · ${qlabel} ` +
      `<span class="muted">top ${d.holders.length} by shares held · ` +
      `as of ${esc(d.asof ?? "—")} · source: ${esc(d.source ?? "FMP")}</span></span></div>` +
      holderTable(d.holders || []);
  } catch (e) {
    det.innerHTML = `<div class="muted">No holder data for ${esc(picked)} ` +
      `yet — the quarterly pull runs after the 45-day 13F filing window.</div>`;
  }
}

/** Renders the holders section into the given element (hosted by the ETF
 *  page's Holders sub-tab). Data labels kept: quarterly 13F, 45-day lag,
 *  institutions-only. */
export function renderEtfHoldersInto(el, doc) {
  if (!el) return;
  el.innerHTML =
    `<div class="panel-subhead"><span>ETF HOLDERS — INSTITUTIONAL OWNERSHIP (13F) ` +
    `<span class="muted">${LAG_NOTE}</span></span></div>` +
    `<div id="eh-body"><div class="muted">Loading…</div></div>`;
  renderBody(el.querySelector("#eh-body"), doc || {});
}

/** Mounts the ETF Holders section at the end of the ETF FLOWS page body. */
export function renderEtfHolders(doc) {
  const body = document.querySelector("#panel-etfflows .panel-body");
  if (!body) return;
  let sec = document.getElementById("etf-holders-section");
  if (!sec) {
    sec = document.createElement("div");
    sec.id = "etf-holders-section";
    body.appendChild(sec);
  }
  renderEtfHoldersInto(sec, doc);
}
