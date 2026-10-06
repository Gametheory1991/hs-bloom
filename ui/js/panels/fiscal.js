import { fmtAge, isStale } from "../fmt.js";

// Third-party names come from USAspending/Finnhub — escape before innerHTML.
const esc = (s) => String(s).replace(/[&<>\"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtB = (x) => (x == null ? "—" : `$${Number(x).toLocaleString("en-US", { maximumFractionDigits: 1 })}B`);

function sectionShell(title, inner, footText, stale) {
  return `<section class="panel"><div class="panel-title">${esc(title)}</div>` +
    `<div class="panel-body">${inner}</div>` +
    `<div class="panel-foot muted${stale ? " stale" : ""}">${esc(footText)}</div></section>`;
}

// Appended to #cycle-econ after the config-driven rows: fiscal detail the
// cycle engine can't express (top recipients / agencies are doc-shaped).
export function renderFiscalEcon(panel) {
  const root = document.getElementById("cycle-econ");
  if (!root) return;
  root.querySelectorAll("[data-batch11]").forEach((el) => el.remove());
  const monthly = panel.monthly ?? [];
  const recips = panel.top_recipients ?? [];
  const agencies = panel.top_agencies ?? [];
  if (!monthly.length && !recips.length && !agencies.length) return;
  const stale = isStale(panel.updated_at, 20160);
  const footText = `DATA: USASPENDING · ${fmtAge(panel.updated_at)}`;
  let html = "";
  if (recips.length || agencies.length) {
    const list = (rows) => rows.slice(0, 8).map((r) =>
      `<tr><td class="sym">${esc(r.name)}</td><td>${fmtB(r.amount_b)}</td></tr>`).join("");
    html += sectionShell("FISCAL — TOP RECIPIENTS & AGENCIES (12M)",
      `<div class="two-col"><div><table data-sortable><tr><th>Recipient</th><th>Oblig</th></tr>${list(recips)}</table></div>` +
      `<div><table data-sortable><tr><th>Agency</th><th>Oblig</th></tr>${list(agencies)}</table></div></div>`,
      footText, stale);
  }
  if (monthly.length) {
    const last6 = monthly.slice(-6);
    html += sectionShell("FISCAL — MONTHLY OBLIGATIONS ($B)",
      `<table data-sortable><tr><th>Month</th><th>Total</th><th>Contracts</th><th>Grants</th></tr>` +
      last6.map((m) => `<tr><td class="sym">${esc(m.date.slice(0, 7))}</td>` +
        `<td>${fmtB(m.total_b)}</td><td>${fmtB(m.contract_b)}</td><td>${fmtB(m.grants_b)}</td></tr>`).join("") +
      `</table>`, footText, stale);
  }
  const wrap = document.createElement("div");
  wrap.setAttribute("data-batch11", "fiscal");
  wrap.innerHTML = html;
  root.appendChild(wrap);
}

// Appended to #cycle-profit after the config-driven rows: earnings calendar
// + insider sentiment for watchlist names (Finnhub; empty until a key is set).
export function renderEarningsProfit(panel) {
  const root = document.getElementById("cycle-profit");
  if (!root) return;
  root.querySelectorAll("[data-batch11]").forEach((el) => el.remove());
  const earnings = panel.earnings ?? [];
  const insider = panel.insider ?? [];
  if (!panel.key_configured || (!earnings.length && !insider.length)) return;
  const stale = isStale(panel.updated_at, 2880);
  const footText = `DATA: FINNHUB · ${fmtAge(panel.updated_at)}`;
  let html = "";
  if (earnings.length) {
    const fmtE = (x) => (x == null ? "—" : Number(x).toFixed(2));
    html += sectionShell("EARNINGS — WATCHLIST (NEXT 3W)",
      `<table data-sortable><tr><th>Date</th><th>Sym</th><th>EPS est</th><th>Rev est $B</th><th>Q</th></tr>` +
      earnings.slice(0, 25).map((e) =>
        `<tr><td class="sym">${esc(e.date)}</td><td class="sym">${esc(e.symbol)}</td>` +
        `<td>${fmtE(e.eps_estimate)}</td>` +
        `<td>${e.revenue_estimate == null ? "—" : (e.revenue_estimate / 1e9).toFixed(1)}</td>` +
        `<td>${e.quarter == null ? "—" : "Q" + e.quarter}</td></tr>`).join("") +
      `</table>`, footText, stale);
  }
  if (insider.length) {
    // latest MSPR read per symbol
    const latest = new Map();
    for (const m of insider) latest.set(m.symbol, m);
    const rows = [...latest.values()].slice(0, 12).map((m) => {
      const v = m.mspr;
      const cls = v == null ? "flat" : v > 0 ? "up" : v < 0 ? "down" : "flat";
      return `<tr><td class="sym">${esc(m.symbol)}</td>` +
        `<td class="${cls}">${v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(1)}</td></tr>`;
    }).join("");
    html += sectionShell("INSIDER SENTIMENT — MSPR (−100..+100)",
      `<table data-sortable><tr><th>Sym</th><th>MSPR</th></tr>${rows}</table>`, footText, stale);
  }
  const wrap = document.createElement("div");
  wrap.setAttribute("data-batch11", "earnings");
  wrap.innerHTML = html;
  root.appendChild(wrap);
}
