import { openChart } from "../chart.js";
import { fmtAge, isStale } from "../fmt.js";

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
          <tr><th>Series</th><th>Now</th><th>Δ1M</th><th>Δ1Y</th></tr>
          ${panel.rows.map((r, ri) => {
            const m = fmtChg(r.chg_1m, r.unit);
            const y = fmtChg(r.chg_1y, r.unit);
            return `<tr class="release clickable" data-p="${pi}" data-r="${ri}">` +
              `<td class="sym">${esc(r.name)}${r.overlay ? ` <span class="muted">⇄</span>` : ""}</td>` +
              `<td>${fmtVal(r.value, r.unit)}</td>` +
              `<td class="${m.cls}">${m.text}</td>` +
              `<td class="${y.cls}">${y.text}</td></tr>`;
          }).join("")}
        </table></div>
        <div class="panel-foot muted${isStale(cycle.updated_at, STALE_MINUTES) ? " stale" : ""}">DATA: CYCLE · ${fmtAge(cycle.updated_at)}</div>
      </section>`).join("");
    root.querySelectorAll("tr.clickable").forEach((tr) => {
      tr.addEventListener("click", () => {
        const row = tab.panels[Number(tr.dataset.p)].rows[Number(tr.dataset.r)];
        openChart(row.id, row.name, row.overlay);
      });
    });
    restore();
  }
}
