// SCORECARD tab: briefing-style 1D/1M/3M/1Y moves + 1Y z-score per series.
// Fed by /api/scorecard (rows configured in config.yaml `scorecard:`).
// Click a row for the full chart via the shared overlay.
import { getScorecard } from "../api.js";
import { openChart } from "../chart.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtMove = (x, kind) => {
  if (x == null) return "—";
  const sign = x < 0 ? "−" : x > 0 ? "+" : "";
  const v = Math.abs(x).toFixed(kind === "bp" ? 0 : 2);
  return `${sign}${v}${kind === "bp" ? "bp" : "%"}`;
};

const fmtLast = (r) => {
  if (r.last == null) return "—";
  const u = r.unit ?? "";
  if (u === "pp") return `${r.last.toFixed(2)}pp`;
  if (u === "%") return `${r.last.toFixed(2)}%`;
  if (u === "ratio") return r.last.toFixed(3);
  if (u === "MMBbls") return Math.round(r.last).toLocaleString("en-US");
  if (u === "$m") return `$${Math.round(r.last).toLocaleString("en-US")}m`;
  return r.last.toLocaleString("en-US", { maximumFractionDigits: 2 });
};

const zClass = (z) => {
  if (z == null) return "";
  const a = Math.abs(z);
  return a >= 2 ? (z > 0 ? "up strong" : "down strong") : a >= 1 ? (z > 0 ? "up" : "down") : "flat";
};

const moveClass = (x) => (x == null || x === 0 ? "flat" : x > 0 ? "up" : "down");

export async function renderScorecard() {
  const body = document.querySelector("#panel-scorecard .panel-body");
  if (!body) return;
  let rows = [];
  try {
    rows = (await getScorecard()).rows ?? [];
  } catch {
    body.innerHTML = `<div class="empty-state">SCORECARD UNAVAILABLE</div>`;
    return;
  }
  if (!rows.length) {
    body.innerHTML = `<div class="empty-state">NO SCORECARD ROWS CONFIGURED</div>`;
    return;
  }
  const trs = rows.map((r) => {
    const cells = ["d1", "m1", "m3", "y1"]
      .map((k) => `<td class="${moveClass(r[k])} num">${fmtMove(r[k], r.kind)}</td>`)
      .join("");
    return `<tr class="clickable" data-series="${esc(r.id)}" data-name="${esc(r.name)}">` +
      `<td class="sym">${esc(r.name)}</td>` +
      `<td class="num">${fmtLast(r)}</td>${cells}` +
      `<td class="${zClass(r.z_1y)} num">${r.z_1y == null ? "—" : (r.z_1y > 0 ? "+" : "") + r.z_1y.toFixed(2)}</td></tr>`;
  }).join("");
  body.innerHTML = `<table class="scorecard"><tr><th>Series</th><th>Last</th><th>1D</th>` +
    `<th>1M</th><th>3M</th><th>1Y</th><th>1Y z</th></tr>${trs}</table>`;
  body.querySelectorAll("tr.clickable").forEach((tr) =>
    tr.addEventListener("click", () => openChart(tr.dataset.series, tr.dataset.name)));
  const foot = document.querySelector("#panel-scorecard .panel-foot");
  const asof = rows.map((r) => r.asof).filter(Boolean).sort().pop();
  if (foot) foot.textContent = `DATA: cycle job · moves ${asof ? "as of " + asof : ""} · z vs trailing 1Y`;
}
