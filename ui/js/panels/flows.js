// FLOWS tab: 13F change detection per watchlist filer — latest quarterly
// net flow plus new/closed/increased/decreased counts and the top position
// changes. Fed by /api/thirteenf (docs written by the thirteenf job).
import { getThirteenF } from "../api.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtUsd(x) {
  if (x == null) return "—";
  const sign = x < 0 ? "−" : "+";
  const a = Math.abs(x);
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  return `${sign}$${Math.round(a).toLocaleString("en-US")}`;
}

const KIND_LABEL = { new: "NEW", closed: "EXIT", increased: "ADD", decreased: "TRIM" };

export async function renderFlows() {
  const body = document.querySelector("#panel-flows .panel-body");
  if (!body) return;
  let filers = [];
  try {
    filers = (await getThirteenF()).filers ?? [];
  } catch {
    body.innerHTML = `<div class="empty-state">13F FLOW DATA UNAVAILABLE</div>`;
    return;
  }
  if (!filers.length) {
    body.innerHTML = `<div class="empty-state">NO 13F DIFFS YET — FIRST FILING SEEN ONLY</div>`;
    return;
  }
  filers.sort((a, b) => Math.abs(b.net_flow_usd ?? 0) - Math.abs(a.net_flow_usd ?? 0));
  body.innerHTML = filers.map((f) => {
    const nf = f.net_flow_usd ?? 0;
    const cls = nf === 0 ? "flat" : nf > 0 ? "up" : "down";
    const top = (f.changes ?? []).slice(0, 5).map((c) => {
      const d = c.delta_usd ?? 0;
      const dc = d === 0 ? "flat" : d > 0 ? "up" : "down";
      return `<tr><td class="sym">${esc(c.issuer || c.cusip)}</td>` +
        `<td class="muted">${KIND_LABEL[c.kind] ?? esc(c.kind)}</td>` +
        `<td class="${dc}">${fmtUsd(d)}</td></tr>`;
    }).join("");
    return `<section class="flow-card">
      <div class="flow-head">
        <span class="flow-name">${esc(f.name)}</span>
        <span class="flow-net ${cls}">${fmtUsd(nf)}</span>
      </div>
      <div class="flow-meta muted">filed ${esc(f.filing_date)} vs ${esc(f.prev_filing_date)} ·
        ${f.n_new ?? 0} new · ${f.n_closed ?? 0} closed · ${f.n_increased ?? 0} adds · ${f.n_decreased ?? 0} trims</div>
      <table data-sortable><tr><th>Position</th><th>Kind</th><th>Δ $</th></tr>${top}</table>
    </section>`;
  }).join("");
  const foot = document.querySelector("#panel-flows .panel-foot");
  if (foot) foot.textContent = "DATA: SEC EDGAR 13F-HR · top-15 holdings diff";
}
