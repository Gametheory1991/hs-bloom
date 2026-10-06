// BRIEFCHECK panel (mkt tab): briefing-vs-terminal cross-check report.
// GET /api/briefcheck → {checked_at, checked_count, mismatches:[{table, metric,
// briefing_value, terminal_value, deviation, unit}]} or 404 → quiet empty state.
import { getBriefcheck } from "../api.js";

export async function renderBriefcheck() {
  const body = document.querySelector("#panel-briefcheck .panel-body");
  if (!body) return;
  let rep = null;
  try {
    rep = await getBriefcheck();
  } catch (err) {
    // 404 = no report yet — quiet empty state, not an error banner.
    body.innerHTML = `<div class="muted">no cross-check yet</div>`;
    return;
  }
  const mm = rep.mismatches ?? [];
  const rows = mm.length
    ? `<table data-sortable><tr><th>Metric</th><th>Briefing</th><th>Terminal</th><th>Δ</th></tr>` +
      mm.map((m) => {
        const dev = m.deviation;
        const cls = dev == null ? "flat" : "down";
        return `<tr><td>${m.metric}<div class="muted" style="font-size:10px">${m.table ?? ""}</div></td>` +
          `<td>${m.briefing_value ?? "—"}${m.unit ? " " + m.unit : ""}</td>` +
          `<td>${m.terminal_value ?? "—"}${m.unit ? " " + m.unit : ""}</td>` +
          `<td class="${cls}">${dev ?? "—"}</td></tr>`;
      }).join("") + `</table>`
    : `<div class="up">✓ all ${rep.checked_count ?? 0} metrics match</div>`;
  body.innerHTML =
    `<div class="news-meta">checked ${rep.checked_at ?? "—"} · ${rep.checked_count ?? 0} metrics · ${mm.length} mismatches</div>` +
    rows;
  const foot = document.querySelector("#panel-briefcheck .panel-foot");
  if (foot) foot.textContent = `DATA: BRIEFCHECK · ${rep.checked_at ?? "—"}`;
}
