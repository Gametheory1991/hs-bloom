// CENTRAL tab: central-bank watch.
// (a) FOMC meeting calendar 2026–2027 with day-countdowns. Dates are vendored
//     statics, VERIFIED 2026-10-03 against
//     https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
//     (page "Last Update: September 16, 2026"). "*" = meeting with Summary of
//     Economic Projections. Decision day = second day of each meeting.
// (b) Implied Fed-funds path from the SOFR 3M futures strip (Yahoo .CME expiry
//     symbols, all verified live 2026-10-03): implied 3M rate = 100 - price.
// Fed speaker calendar: SKIPPED — no free machine-readable source verifies
// (see CHANGES.md).
import { getSeries } from "../api.js";

// [start, end, sep?] — decision day is the second date.
const FOMC = [
  ["2026-01-27", "2026-01-28", false], ["2026-03-17", "2026-03-18", true],
  ["2026-04-28", "2026-04-29", false], ["2026-06-16", "2026-06-17", true],
  ["2026-07-28", "2026-07-29", false], ["2026-09-15", "2026-09-16", true],
  ["2026-10-27", "2026-10-28", false], ["2026-12-08", "2026-12-09", true],
  ["2027-01-26", "2027-01-27", false], ["2027-03-16", "2027-03-17", true],
  ["2027-04-27", "2027-04-28", false], ["2027-06-08", "2027-06-09", true],
  ["2027-07-27", "2027-07-28", false], ["2027-09-14", "2027-09-15", true],
  ["2027-10-26", "2027-10-27", false], ["2027-12-07", "2027-12-08", true],
];

const STRIP = [
  ["sofr-v26", "Oct-26"], ["sofr-z26", "Dec-26"], ["sofr-h27", "Mar-27"],
  ["sofr-m27", "Jun-27"], ["sofr-u27", "Sep-27"], ["sofr-z27", "Dec-27"],
  ["sofr-h28", "Mar-28"],
];

const dayMs = 86400_000;
const todayUTC = () => {
  const n = new Date();
  return Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate());
};

function fomcRows() {
  const t = todayUTC();
  return FOMC.map(([s, e, sep]) => {
    const end = Date.parse(e + "T00:00:00Z");
    const days = Math.round((end - t) / dayMs);
    return { s, e, sep, days, past: days < 0, next: false };
  }).map((r, i, arr) => {
    if (!r.past && !arr.some((x) => !x.past && x.days < r.days)) r.next = true;
    return r;
  });
}

function pathChart(rows) {
  // rows: [{label, impl}] — tiny SVG polyline of the implied path.
  const W = 560, H = 120, P = 28;
  const vals = rows.map((r) => r.impl);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const X = (i) => P + (i * (W - 2 * P)) / (vals.length - 1);
  const Y = (v) => H - P - ((v - lo) / span) * (H - 2 * P);
  const pts = vals.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const dots = vals.map((v, i) =>
    `<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="3" fill="var(--amber)"/>` +
    `<text x="${X(i).toFixed(1)}" y="${(Y(v) - 8).toFixed(1)}" text-anchor="middle" class="svg-lab">${v.toFixed(2)}%</text>` +
    `<text x="${X(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="svg-lab">${rows[i].label}</text>`
  ).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="strip-chart" role="img" aria-label="Implied SOFR path">` +
    `<polyline points="${pts}" fill="none" stroke="var(--amber)" stroke-width="2"/>${dots}</svg>`;
}

export async function renderCentral() {
  const body = document.querySelector("#panel-central .panel-body");
  if (!body) return;

  // --- FOMC calendar ---
  const cal = fomcRows();
  const calHtml = `<div class="panel-subhead">FOMC MEETINGS — COUNTDOWN TO DECISION</div>
    <table><tr><th>Meeting</th><th>Decision</th><th>SEP</th><th>Countdown</th></tr>
    ${cal.map((r) => {
      const cls = r.past ? "flat" : r.next ? "up" : "";
      const cd = r.past ? "done" : r.days === 0 ? "<b>TODAY</b>" :
        r.days === 1 ? "tomorrow" : `in ${r.days}d`;
      return `<tr class="${cls}"><td>${r.s} → ${r.e}</td><td>${r.e}</td>` +
        `<td>${r.sep ? "*" : ""}</td><td class="${cls}">${cd}</td></tr>`;
    }).join("")}</table>
    <div class="muted" style="font-size:10px">* Summary of Economic Projections · dates verified vs federalreserve.gov 2026-10-03</div>`;

  // --- SOFR strip ---
  let stripHtml = `<div class="panel-subhead">IMPLIED FED-FUNDS PATH — SOFR 3M STRIP</div>`;
  try {
    const rows = [];
    for (const [id, label] of STRIP) {
      const s = await getSeries(id, "1y");
      const pts = s.points ?? [];
      const last = pts.length ? pts[pts.length - 1][1] : null;
      rows.push({ label, impl: last == null ? null : 100 - last, asof: pts.length ? pts[pts.length - 1][0] : null });
    }
    const good = rows.filter((r) => r.impl != null);
    if (!good.length) {
      stripHtml += `<div class="muted">strip unavailable</div>`;
    } else {
      stripHtml += pathChart(good) +
        `<table><tr><th>Contract</th><th>Implied 3M</th><th>As of</th></tr>` +
        good.map((r) => `<tr><td class="sym">${r.label}</td><td>${r.impl.toFixed(2)}%</td><td>${r.asof ?? "—"}</td></tr>`).join("") +
        `</table><div class="muted" style="font-size:10px">implied = 100 − futures price · strip rolls: codes cover Oct-26 → Mar-28</div>`;
    }
  } catch {
    stripHtml += `<div class="muted">strip unavailable</div>`;
  }

  body.innerHTML = calHtml + stripHtml;
  const foot = document.querySelector("#panel-central .panel-foot");
  if (foot) foot.textContent = "DATA: FRED+CME/YAHOO · FOMC DATES: FEDERALRESERVE.GOV";
}
