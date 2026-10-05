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
      ? `<table><tr><th>Date</th><th>Tenor</th><th>Offering</th></tr>${upRows}</table>`
      : `<div class="muted">No announced auctions on the schedule.</div>`) +
    `<div class="panel-subhead">RECENT RESULTS</div>` +
    (rec.length
      ? `<table><tr><th>Date</th><th>Tenor</th><th>High yield</th>` +
        `<th>Bid/cover</th><th>Indirect %</th><th>Direct %</th>` +
        `<th>Dealer %</th><th>Offering</th></tr>${recRows}</table>`
      : `<div class="muted">No results yet.</div>`);
}
