import { openChart } from "../chart.js";
import { fmtAge, isStale } from "../fmt.js";
import { getSeries } from "../api.js";
import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";

// Labels come from collector config, not a third-party API, but escape
// before innerHTML anyway — cheap insurance against a bad config value.
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// One formatter per unit family: %-like values keep 2 decimals, contract
// counts read in thousands, everything else gets locale grouping.
function fmtVal(x, unit) {
  if (x == null) return "—";
  if (unit === "%" || unit === "ratio" || unit === "pts") return x.toFixed(2);
  if (unit === "contracts" || unit === "k") return Math.round(x).toLocaleString("en-US");
  return x.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function fmtChg(x, unit) {
  if (x == null) return { text: "—", cls: "flat" };
  const text = `${x > 0 ? "+" : ""}${fmtVal(x, unit)}`;
  return { text, cls: x === 0 ? "flat" : x > 0 ? "up" : "down" };
}
// Nominal + % delta cell: "+5.20 (+1.2%)". pct = chg / (value - chg).
function fmtChgBoth(chg, value, unit) {
  if (chg == null) return { text: "—", cls: "flat" };
  const m = fmtChg(chg, unit);
  const base = value != null ? value - chg : null;
  const pct = base != null && base !== 0 ? (chg / base) * 100 : null;
  const p = pct == null || !isFinite(pct) ? "—" : `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
  return { text: `${m.text} <span class="muted">(${p})</span>`, cls: m.cls };
}

const STALE_MINUTES = 2880; // 2x the daily cycle cadence

export function renderCycle(cycle) {
  for (const tab of cycle.tabs ?? []) {
    const root = document.getElementById(`cycle-${tab.id}`);
    if (!root) continue;
    // Batch-11 custom sections (fiscal detail, earnings, FIGI lookup) live
    // inside the cycle tab roots; preserve them across re-renders.
    const extras = [...root.querySelectorAll("[data-batch11]")];
    const restore = () => { for (const el of extras) root.appendChild(el); };
    if (!tab.panels.some((p) => p.rows.length)) {
      root.innerHTML = `<section class="panel"><div class="panel-body"><div class="empty-state">NO DATA</div></div></section>`;
      restore();
      continue;
    }
    root.innerHTML = tab.panels.map((panel, pi) => `
      <section class="panel">
        <div class="panel-title">${esc(panel.title)}</div>
        <div class="panel-body"><table data-sortable>
          <tr><th>Series</th><th>Now</th><th>Δ 1M</th><th>Δ 1Y</th>${RANGE_TH}</tr>
          ${panel.rows.map((r, ri) => {
            const m = fmtChgBoth(r.chg_1m, r.value, r.unit);
            const y = fmtChgBoth(r.chg_1y, r.value, r.unit);
            return `<tr class="release clickable" data-p="${pi}" data-r="${ri}" data-sid="${esc(r.id)}">` +
              `<td class="sym">${esc(r.name)}${r.overlay ? ` <span class="muted">⇄</span>` : ""}</td>` +
              `<td>${fmtVal(r.value, r.unit)}</td>` +
              `<td class="${m.cls}">${m.text}</td>` +
              `<td class="${y.cls}">${y.text}</td>` +
              `<td class="range-cell" data-range-for="${esc(r.id)}" colspan="4"><span class="muted">…</span></td></tr>`;
          }).join("")}
        </table></div>
        <div class="panel-foot muted${isStale(cycle.updated_at, STALE_MINUTES) ? " stale" : ""}">DATA: CYCLE · ${fmtAge(cycle.updated_at)}</div>
      </section>`).join("");
    // Fill Bloomberg sparklines asynchronously (one history fetch per row).
    root.querySelectorAll("td[data-range-for]").forEach(async (cell) => {
      const sid = cell.dataset.rangeFor;
      try {
        const s = await getSeries("cycle:" + sid, "1y");
        const vals = (s.points ?? []).map((p) => p[1]).filter((v) => v != null && isFinite(v));
        if (vals.length >= 12) {
          const tmp = document.createElement("tr");
          // rangeCells returns two <td>s; transplant them.
          tmp.innerHTML = rangeCells(statsFromValues(vals), "1Y");
          cell.replaceWith(...tmp.cells);
        } else {
          cell.innerHTML = `<span class="muted">—</span>`;
          cell.colSpan = 4;
        }
      } catch {
        cell.innerHTML = `<span class="muted">—</span>`;
      }
    });
    root.querySelectorAll("tr.clickable").forEach((tr) => {
      tr.addEventListener("click", () => {
        const row = tab.panels[Number(tr.dataset.p)].rows[Number(tr.dataset.r)];
        openChart(row.id, row.name, row.overlay);
      });
    });
    restore();
  }
}
