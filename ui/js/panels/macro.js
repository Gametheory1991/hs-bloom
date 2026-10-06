import { openChart } from "../chart.js";

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function when(iso) {
  if (!Number.isFinite(Date.parse(iso))) return "TBD";
  const d = new Date(iso);
  return `${d.toLocaleDateString("en-GB", { weekday: "short" })} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

// Timeline: released events from the last 7 days first (actuals filled),
// then the ── UPCOMING ── divider, then the rest of the FF week.
export function renderMacro(panel) {
  const body = document.querySelector("#panel-macro .panel-body");
  const past = panel.past ?? [];   // ?? []: tolerate an old collector during rollout
  const all = [...past, ...panel.releases];
  const row = (r, i) =>
    `<tr class="release ${r.series_id ? "clickable" : ""}" data-i="${i}">` +
    `<td>${esc(when(r.time))}</td><td>${esc(r.country ?? "—")}</td>` +
    `<td style="text-align:left">${esc(r.name ?? "—")}</td>` +
    `<td>${esc(r.previous ?? "—")}</td><td>${esc(r.consensus ?? "—")}</td>` +
    `<td class="actual">${esc(r.actual ?? "—")}</td></tr>`;
  body.innerHTML = `<table data-sortable>
    <tr><th>When (local)</th><th>Ctry</th><th>Release</th><th>Prev</th><th>Cons</th><th>Act</th></tr>
    ${past.map(row).join("")}
    ${past.length && panel.releases.length
      ? `<tr class="macro-divider"><td colspan="6">── UPCOMING ──</td></tr>` : ""}
    ${panel.releases.map((r, i) => row(r, past.length + i)).join("")}
  </table>`;
  body.querySelectorAll("tr.clickable").forEach((tr) => {
    tr.addEventListener("click", () => {
      const release = all[Number(tr.dataset.i)];
      openChart(release.series_id, release.name);
    });
  });
}
