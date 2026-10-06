// PREDICT tab: prediction-market data + edge engine output.
// Reads dash.panels.predict (backend pred_edge doc + venue snapshots).
// All edge/mispricing figures are model estimates, labeled as such —
// the backend disclaimer is rendered verbatim in the panel foot.
const pct = (x) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const usd = (x) =>
  x == null ? "—" : "$" + x.toLocaleString("en-US", { maximumFractionDigits: 0 });
const link = (url, text) =>
  url ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>` : text;

function edgeTable(edges) {
  if (!edges.length)
    return `<p class="muted">No cross-venue divergences above threshold right now.</p>`;
  return `<table data-sortable>
      <tr><th>Event</th><th>Poly Yes</th><th>Kalshi Yes</th><th>Spread</th>
      <th>Edge est.</th><th>Score</th><th>Tradable?</th></tr>
      ${edges.map((e) => {
        const sCls = e.spread > 0 ? "up" : e.spread < 0 ? "down" : "flat";
        const tCls = e.tradable_estimate ? "up" : "flat";
        return `<tr><td>${e.label}<br><span class="muted">${link(e.poly_url, "poly")} · ${link(e.kalshi_url, "kalshi")}</span></td>` +
          `<td>${pct(e.poly_yes)}</td><td>${pct(e.kalshi_yes)}</td>` +
          `<td class="${sCls}">${e.spread > 0 ? "+" : ""}${(e.spread * 100).toFixed(1)}¢</td>` +
          `<td>${(e.edge_estimate * 100).toFixed(1)}¢</td>` +
          `<td>${e.mispricing_score}</td>` +
          `<td class="${tCls}">${e.tradable_estimate ? "yes*" : "no"}</td></tr>`;
      }).join("")}
    </table>
    <p class="muted">* "tradable" = spread clears a ~4¢ round-trip cost estimate and both legs pass liquidity gates. Model estimate, not a guarantee.</p>`;
}

function marketsTable(title, rows, priceKey) {
  if (!rows.length) return `<p class="muted">No ${title} data yet.</p>`;
  return `<h3>${title}</h3><table data-sortable>
      <tr><th>Market</th><th>Yes</th><th>24h vol</th></tr>
      ${rows.map((m) => {
        const label = m.question ?? m.title ?? "—";
        const px = m[priceKey] ?? m.yes_price ?? m.last_price;
        return `<tr><td>${link(m.url, label.slice(0, 90))}</td>` +
          `<td>${pct(px)}</td><td>${usd(m.volume24h)}</td></tr>`;
      }).join("")}
    </table>`;
}

function calibTable(rows) {
  if (!rows.length)
    return `<p class="muted">No resolved markets scored yet — calibration builds as tracked markets settle.</p>`;
  return `<table data-sortable>
      <tr><th>Venue</th><th>Category</th><th>n</th><th>Brier</th><th>Win rate</th><th>Avg implied</th></tr>
      ${rows.map((r) => `<tr><td>${r.venue}</td><td>${r.category}</td><td>${r.n}</td>` +
        `<td>${r.brier.toFixed(3)}</td><td>${(r.win_rate * 100).toFixed(1)}%</td>` +
        `<td>${(r.avg_implied * 100).toFixed(1)}%</td></tr>`).join("")}
    </table>
    <p class="muted">Brier = mean squared error of implied probability vs outcome (lower is better; 0.25 = coin-flip).</p>`;
}

function moversTable(movers) {
  if (!movers.length) return "";
  return `<h3>BIGGEST 1D MOVERS — POLYMARKET</h3><table data-sortable>
      <tr><th>Market</th><th>Yes</th><th>Δ 1D</th><th>24h vol</th></tr>
      ${movers.map((m) => {
        const cls = m.chg_1d > 0 ? "up" : "down";
        return `<tr><td>${link(m.url, (m.label ?? "").slice(0, 90))}</td>` +
          `<td>${pct(m.yes)}</td><td class="${cls}">${m.chg_1d > 0 ? "+" : ""}${(m.chg_1d * 100).toFixed(1)}pp</td>` +
          `<td>${usd(m.volume24h)}</td></tr>`;
      }).join("")}
    </table>`;
}

export function renderPredict(p) {
  const body = document.querySelector("#panel-predict .panel-body");
  if (!body) return;
  body.innerHTML =
    `<h3>CROSS-VENUE EDGE ESTIMATES</h3>` + edgeTable(p.edges ?? []) +
    marketsTable("TOP POLYMARKET (24H VOLUME)", p.polymarket ?? [], "yes_price") +
    marketsTable("TOP KALSHI (24H VOLUME)", p.kalshi ?? [], "last_price") +
    moversTable(p.movers ?? []) +
    `<h3>CALIBRATION LEADERBOARD</h3>` + calibTable(p.calibration ?? []) +
    `<p class="muted">Tracking ${p.tracked_count ?? 0} markets for resolution · ` +
    `resolved this run: ${p.resolved_this_run ?? 0}` +
    `${(p.skipped ?? []).length ? ` · skipped: ${p.skipped.join("; ")}` : ""}</p>` +
    (p.disclaimer ? `<p class="muted">${p.disclaimer}</p>` : "");
}
