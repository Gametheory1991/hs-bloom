import { openChart } from "../chart.js";
import { fmtBp, fmtNum, fmtPct } from "../fmt.js";
import { symNameHtml } from "../names.js";

const HORIZONS = ["1d", "1w", "ytd", "1y"];

export function renderEquity(panel) {
  const body = document.querySelector("#panel-equity .panel-body");
  const cells = (row) => HORIZONS.map((h) => {
    const { text, cls } = fmtPct(row[`chg_${h}`]);
    return `<td class="${cls}">${text}</td>`;
  }).join("");
  body.innerHTML = `<table data-sortable>
    <tr><th>Index</th><th>Last</th><th>1D</th><th>1W</th><th>YTD</th><th>1Y</th></tr>
    ${panel.rows.map((r, i) =>
      `<tr class="clickable" data-i="${i}"><td class="sym">${symNameHtml(r.symbol, 34)}</td>` +
      `<td>${fmtNum(r.last)}</td>${cells(r)}</tr>`
    ).join("")}
  </table>`;
  body.querySelectorAll("tr.clickable").forEach((tr) => {
    tr.addEventListener("click", () => {
      const r = panel.rows[Number(tr.dataset.i)];
      openChart(r.symbol, r.name);
    });
  });
}

// Matrix: one row per country, CB / 3M / 10Y cells each click through to
// their own history series (USCB / US3M / US10Y — api applies store prefixes).
export function renderBonds(panel) {
  const body = document.querySelector("#panel-bonds .panel-body");
  const chg = (bp) => { const { text, cls } = fmtBp(bp); return `<td class="${cls}">${text}</td>`; };
  const yld = (r, pct, sid, title) => pct == null
    ? `<td>—</td>`
    : `<td class="clickable" data-sid="${r.country}${sid}" data-title="${title}">${pct.toFixed(2)}</td>`;
  // Benchmark dropdown for spread column (persisted in localStorage)
  const countries = panel.rows.map((r) => r.country);
  let bench = localStorage.getItem("bonds_benchmark") || "DE";
  if (!countries.includes(bench)) bench = countries[0] || "DE";
  const benchRow = panel.rows.find((r) => r.country === bench);
  const benchY10 = benchRow?.y10_pct;
  const spread = (r) => {
    if (r.y10_pct == null || benchY10 == null || r.country === bench) return `<td>—</td>`;
    const bp = (r.y10_pct - benchY10) * 100;
    const { text, cls } = fmtBp(bp);
    return `<td class="${cls}">${text}</td>`;
  };
  const opts = countries.map((cc) =>
    `<option value="${cc}"${cc === bench ? " selected" : ""}>${cc}</option>`).join("");
  const EM = new Set(["MX", "KR"]);
  let seg = localStorage.getItem("bonds_segment") || "all";
  const segOpts = [["all", "All"], ["dev", "Developed (G10)"], ["em", "Emerging Markets"]]
    .map(([v, l]) => `<option value="${v}"${v === seg ? " selected" : ""}>${l}</option>`).join("");
  const rows = panel.rows.filter((r) => seg === "all" || (seg === "em" ? EM.has(r.country) : !EM.has(r.country)));
  body.innerHTML = `<div class="bench-row"><label>Spread vs <select id="bonds-bench">${opts}</select></label> <label>Segment <select id="bonds-seg">${segOpts}</select></label></div>
  <div class="table-scroll"><table data-sortable>
    <tr><th>Ctry</th><th>CB</th><th>3M</th><th>10Y</th><th>Spread</th><th>1D</th><th>1W</th></tr>
    ${rows.map((r) =>
      `<tr><td class="sym">${r.country}</td>` +
      yld(r, r.cb_pct, "CB", `${r.cb_label ?? r.country} RATE`) +
      yld(r, r.y3m_pct, "3M", `${r.country} 3M YIELD`) +
      yld(r, r.y10_pct, "10Y", `${r.country} 10Y YIELD`) +
      spread(r) +
      `${chg(r.chg_1d_bp)}${chg(r.chg_1w_bp)}</tr>`
    ).join("")}
  </table></div>`;
  body.querySelector("#bonds-bench").addEventListener("change", (e) => {
    localStorage.setItem("bonds_benchmark", e.target.value);
    renderBonds(panel);
  });
  body.querySelector("#bonds-seg").addEventListener("change", (e) => {
    localStorage.setItem("bonds_segment", e.target.value);
    renderBonds(panel);
  });
  body.querySelectorAll("td.clickable").forEach((td) => {
    td.addEventListener("click", () => openChart(td.dataset.sid, td.dataset.title));
  });
}
