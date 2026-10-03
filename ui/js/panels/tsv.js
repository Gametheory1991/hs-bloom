// TSV panel: Tokenized Securities Venue watch.
// Renders the generic universe schema (verticals -> companies with status)
// plus the SEC order explainer and the weekly regulatory scan. No AI-flow
// internals are reused — this view is a regulatory watchlist, not a
// capex/money-flow graph, so it gets a purpose-built renderer on the same
// generic data contract.
import { fmtAge, isStale } from "../fmt.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATUS_COLOR = {
  operating: "#2fb56b", notice_filed: "#2fb56b", announced: "#7fd4a8",
  preparing: "#4f8ff7", positioned: "#e8c96a", rumored: "#9a9a9a",
  active: "#9a9a9a",
};

function statusChip(st) {
  const c = STATUS_COLOR[st] || "#9a9a9a";
  const label = String(st || "unknown").replace(/_/g, " ").toUpperCase();
  return `<span class="tsv-status" style="border-color:${c};color:${c}">${esc(label)}</span>`;
}

function explainer(order) {
  if (!order || !order.release) return "";
  const o = order;
  const rows = [
    ["Order", `Release ${esc(o.release)} · File ${esc(o.file_no)}`],
    ["Window", `${esc(o.issued)} → ${esc(o.expires)}`],
    ["Tier 1", `${o.tier1_symbols} symbols · ${esc(o.tier1_volume_cap)}`],
    ["Tier 2", `${o.tier2_symbols} symbols · ${esc(o.tier2_volume_cap)}`],
    ["Notice", "30-day public website notice; SEC email within 1 business day"],
    ["Issuer veto", "30-day objection window on third-party tokens"],
    ["Ledger", "Auditable public smart contracts, permissionless chain"],
    ["Rights", "Same dividends / votes / liquidation as underlying"],
    ["Bans", "No leverage · no primary issuance · no synthetics"],
    ["Reporting", "Trades within 10 min · halts with listing exchange"],
  ];
  return `<div class="tsv-explainer"><div class="tsv-sec-title">EXEMPTION EXPLAINER — SEC INNOVATION EXEMPTION</div>
    <div class="tsv-grid">${rows.map(([k, v]) =>
      `<div class="tsv-fact"><div class="tsv-fact-k">${esc(k)}</div><div class="tsv-fact-v">${v}</div></div>`).join("")}
    </div>
    <div class="muted" style="font-size:10px">Earliest possible trading: ${esc(o.earliest_trading)}. Press release ${esc(o.press_release)}. Comments open under File ${esc(o.file_no)}.</div></div>`;
}

function watchlist(doc) {
  const verts = doc.verticals || [];
  if (!verts.length) return `<div class="empty-state">NO DATA — tsv_graph job has not run yet</div>`;
  return verts.map((v) => `
    <div class="tsv-vert"><div class="tsv-sec-title">${esc(v.label).toUpperCase()}</div>
    ${(v.companies || []).map((c) => `
      <div class="tsv-co">
        <div class="tsv-co-head"><span class="tsv-co-name">${esc(c.name)}</span>${statusChip(c.status)}
          ${c.ticker ? `<span class="tsv-ticker">${esc(c.ticker)}</span>` : ""}</div>
        ${c.note ? `<div class="tsv-note">${esc(c.note)}</div>` : ""}
        ${c.kind === "private" ? `<div class="muted" style="font-size:10px">private — no public financials</div>` : ""}
      </div>`).join("")}
    </div>`).join("");
}

function scanResults(doc) {
  const w = doc.watch;
  if (!w) return `<div class="muted">Weekly regulatory scan has not run yet.</div>`;
  const hits = [...(w.press_hits || []), ...(w.federal_register_hits || [])];
  const items = hits.length
    ? hits.map((h) => `<div class="tsv-hit"><a href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.title || h.url)}</a>
        <span class="muted">${esc(h.published || "")}</span></div>`).join("")
    : `<div class="muted">No new TSV items this scan.</div>`;
  return `<div class="tsv-sec-title">WEEKLY REGULATORY SCAN <span class="muted">· ${esc(w.as_of ? w.as_of.slice(0, 10) : "")}</span></div>
    ${items}
    <div class="muted" style="font-size:10px">${esc(w.caveat || "")}</div>`;
}

function riskNotes(doc) {
  const notes = doc.risk_notes || [];
  if (!notes.length) return "";
  return `<div class="tsv-sec-title">KEY FACTS</div><ul class="tsv-notes">` +
    notes.map((n) => `<li>${esc(n)}</li>`).join("") + `</ul>`;
}

export function renderTsv(doc) {
  const body = document.querySelector("#panel-tsv .panel-body");
  if (!body) return;
  body.innerHTML = explainer(doc.order) +
    `<div class="tsv-sec-title">VENUE WATCHLIST</div>` + watchlist(doc) +
    scanResults(doc) + riskNotes(doc);
}
