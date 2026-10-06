// FUTURES tab: front-month futures quotes — latest price + day change.
// Series come from the cycle job's Yahoo pulls (cycle:fut-*); click a row
// for the full chart via the shared overlay.
import { getSeries } from "../api.js";
import { openChart } from "../chart.js";

export const FUTURES = [
  ["fut-es", "ES", "S&P 500"],
  ["fut-nq", "NQ", "Nasdaq 100"],
  ["fut-ym", "YM", "Dow"],
  ["fut-rty", "RTY", "Russell 2000"],
  ["fut-zb", "ZB", "30Y T-Bond"],
  ["fut-zn", "ZN", "10Y T-Note"],
  ["fut-cl", "CL", "WTI crude"],
  ["fut-gc", "GC", "Gold"],
  ["fut-si", "SI", "Silver"],
  ["fut-hg", "HG", "Copper"],
];

const fmtPx = (x) =>
  x == null ? "—" : x.toLocaleString("en-US", { maximumFractionDigits: 2 });

export async function renderFutures() {
  const body = document.querySelector("#panel-futures .panel-body");
  if (!body) return;
  const rows = await Promise.all(
    FUTURES.map(async ([id, sym, label]) => {
      try {
        const s = await getSeries(id, "1y");
        const pts = s.points ?? [];
        if (pts.length < 2) return { id, sym, label, last: null, chg: null, pct: null, asof: null };
        const last = pts[pts.length - 1][1];
        const prev = pts[pts.length - 2][1];
        const chg = last - prev;
        return { id, sym, label, last, chg, pct: (chg / prev) * 100, asof: pts[pts.length - 1][0] };
      } catch {
        return { id, sym, label, last: null, chg: null, pct: null, asof: null };
      }
    })
  );
  body.innerHTML = `<table data-sortable>
      <tr><th>Contract</th><th>Underlying</th><th>Last</th><th>Δ 1D</th><th>Δ 1D %</th></tr>
      ${rows.map((r, i) => {
        const cls = r.chg == null ? "flat" : r.chg === 0 ? "flat" : r.chg > 0 ? "up" : "down";
        const chg = r.chg == null ? "—" : `${r.chg > 0 ? "+" : ""}${fmtPx(r.chg)}`;
        const pct = r.pct == null ? "—" : `${r.pct > 0 ? "+" : ""}${r.pct.toFixed(2)}%`;
        return `<tr class="clickable" data-i="${i}">` +
          `<td class="sym">${r.sym}</td><td>${r.label}</td>` +
          `<td>${fmtPx(r.last)}</td>` +
          `<td class="${cls}">${chg}</td><td class="${cls}">${pct}</td></tr>`;
      }).join("")}
    </table>`;
  const asof = rows.map((r) => r.asof).filter(Boolean).sort().pop();
  const foot = document.querySelector("#panel-futures .panel-foot");
  if (foot) foot.textContent = `DATA: YAHOO · ${asof ?? "—"}`;
  body.querySelectorAll("tr.clickable").forEach((tr) => {
    tr.addEventListener("click", () => {
      const [id, sym] = FUTURES[Number(tr.dataset.i)];
      openChart(id, `${sym} front-month future`, null);
    });
  });
}
