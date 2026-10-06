import { openChart } from "../chart.js";
import { fmtBp } from "../fmt.js";

// Labels come from collector config, not a third-party API, but escape
// before innerHTML anyway — cheap insurance against a bad config value.
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function renderRefs(panel) {
  const body = document.querySelector("#panel-refs .panel-body");
  if (!panel.rows.length) {
    body.innerHTML = `<div class="empty-state">NO DATA</div>`;
    return;
  }
  const cell = (bp) => { const { text, cls } = fmtBp(bp); return `<td class="${cls}">${text}</td>`; };
  body.innerHTML = `<table data-sortable>
    <tr><th>Ref</th><th>Now</th><th>1D</th><th>1W</th></tr>
    ${panel.rows.map((r, i) =>
      `<tr class="release clickable" data-i="${i}">` +
      `<td class="sym">${esc(r.label)}</td>` +
      `<td>${r.value_pct == null ? "—" : r.value_pct.toFixed(2)}</td>` +
      `${cell(r.chg_1d_bp)}${cell(r.chg_1w_bp)}</tr>`
    ).join("")}
  </table>`;
  body.querySelectorAll("tr.clickable").forEach((tr) => {
    tr.addEventListener("click", () => {
      const r = panel.rows[Number(tr.dataset.i)];
      openChart(r.id, r.label);
    });
  });
}
