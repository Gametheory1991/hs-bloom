// FINRA TRACE Fact Book panel — quarterly OTC bond-market tables.
// Standalone render module for a future STRUCTURE "Fact Book" subtab.
// Reads dash.panels.factbook (backend _factbook_panel, to be added).
//
// Expected payload:
//   {
//     as_of: "Q2 2026", quarter_end: "2026-06-30",
//     headlines: { ig: {trades, pv}, hy: {...}, convig, convhy, corp,
//                  agency, abs, absx, cmo, mbs, tba },
//     buckets_latest: { ig: { trades: {ge25m: v, ...}, pv: {...} }, ... },
//     buy_sell_latest: { ig: [{bucket, gross, net, ratio}, ...], ... },
//     top: { ig_trades: [{rank, symbol, issuer, coupon, maturity, rating,
//                         trades|pv, dealers}], ig_pv, hy_trades, hy_pv,
//            conv_trades, conv_pv, agency_trades, agency_pv },
//     hist: { "fb-ig-trades": [{d, v}, ...], "fb-ig-pv": [{d, v}, ...] },
//       // quarterly history per product; BOTH -trades (ADT) and -pv (ADV)
//       // should be attached so the ADT/ADV/BOTH toggle can chart either
//       // series or overlay both on dual axes.
//     annual_top: { "2025": { ig_trades: [...], ... } },  // yearly top-N
//     annual_as_of: "2025",
//     interval: { "2025": { corp: { fine: [{t, trades, par, avg_size}, ...],
//                                  coarse: { bucket: { trades: {period: v},
//                                                       par: {...} } } },
//                           abs: { scope: "ABS Auto Loan", fine: [...],
//                                  coarse: {...} }, ... } },
//     interval_note: "...",   // HONEST CAVEAT: interval data comes from
//     updated_at, source      // FINRA's ANNUAL workbooks -> yearly refresh,
//                             // not quarterly. Securitized interval data
//                             // covers the largest sub-segment only.
//     annual_adv_adt: { "2025": { ig: { trades: {total, buckets: {ge25m: v}},
///                                         par: {...},
//                                          buy_sell: { trades: {ge25m: {gross, net, ratio}},
//                                                      par: {...} } }, ... } },
//       // full-year average-daily trades/par by bucket; buy_sell flavors are
//       // "trades"/"par" (corp/agency/tba) or "trades_opb"/"trades_rpb"/
//       // "par_opb"/"par_rpb" (sec). buy_sell gross/net are ANNUAL totals
//       // (counts/yr for trades flavor, $/yr for par flavor); ratio is unit-free.
//     issue: { "2025": { corp: { label, total: {period: v},
//                                breakdown: {label: {period: v}}, periods: [...] },
//                        conv/agency/abs/absx/cmo/mbs: {...} } },
//       // issues outstanding = COUNTS of CUSIPs (no par outstanding in FINRA's
//       // tables). Breakdowns: rating (corp), issuer (agency), type (sec).
//     issue_mix: { "2025": { corp: { AAA: {issues, trades, par}, ... },
//                            agency: { issuer: {...} },
//                            abs/absx/cmo/mbs/tba: { label: {...} } } },
//       // workbook-year snapshot: issues outstanding + avg-daily S1 trades/par.
//     issue_note: "...",       // HONEST CAVEAT: counts only, annual refresh.
//     participant: { "2025": { corp: { segment, tiers: ["5","10","25","50"],
//                      trades_pct: {tier: {period: v}}, par_pct: {...},
//                      firms_reporting: {period: v}, unique_firms: {...},
//                      avg_firms_per_day: {...}, periods: [...] }, ... } },
//       // % of S1 activity captured by most-active-N firms (fractions, annual
//       // totals); primary all-eligible-firms table per product.
//     participant_note: "...", // HONEST CAVEAT: annual refresh, no quarterly
//                             // equivalent; segment splits exist in source.
//   }
// trades = avg-daily trade count (ADT); pv/gross/net = avg-daily $ par (ADV).
// buy-sell ratio = customer buy $ / customer sell $; net = buy - sell.
// interval fine-grid t labels are bucket starts ("08:00") + "After Hours".

import { rangeCells, statsFromValues, RANGE_TH } from "../rangeviz.js";

// Q/Q delta helpers: nominal + % change, per Harry's standing rules.
const qqDelta = (hist) => {
  const vs = (hist ?? []).map((p) => p.v).filter((v) => v != null && isFinite(v));
  if (vs.length < 2) return { nom: null, pct: null };
  const now = vs[vs.length - 1], prev = vs[vs.length - 2];
  const nom = now - prev;
  const pct = prev !== 0 ? nom / Math.abs(prev) : null;
  return { nom, pct };
};
const deltaCell = (nom, pct, fmt) => {
  if (nom == null || !isFinite(nom)) return "—";
  const n = `${nom >= 0 ? "+" : "−"}${fmt(Math.abs(nom))}`;
  const p = pct == null || !isFinite(pct) ? "—" : `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`;
  return `<b>${n}</b> <span class="muted">(${p})</span>`;
};

const big = (x) =>
  x == null ? "—" : x.toLocaleString("en-US", { maximumFractionDigits: 0 });
const usdB = (x) =>
  x == null ? "—" : "$" + (x / 1e9).toLocaleString("en-US", { maximumFractionDigits: 2 }) + "B";
const usdM = (x) =>
  x == null ? "—" : "$" + (x / 1e6).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "M";
const r2 = (x) => (x == null ? "—" : Number(x).toFixed(2));

const PRODUCTS = [
  ["ig", "Investment Grade"], ["hy", "High Yield"],
  ["convig", "Conv IG"], ["convhy", "Conv HY"],
  ["corp", "Corporate (P1)"], ["agency", "Agency"],
  ["abs", "ABS"], ["absx", "ABSX"], ["cmo", "CMO"],
  ["mbs", "MBS"], ["tba", "TBA"],
];
const BUCKET_LABEL = {
  ge25m: "≥ $25M", b10_25m: "$10–25M", b5_10m: "$5–10M",
  b1_5m: "$1–5M", b100k_1m: "$100K–1M", lt100k: "< $100K",
};
const BUCKET_ORDER = ["ge25m", "b10_25m", "b5_10m", "b1_5m", "b100k_1m", "lt100k"];
const TOP_LISTS = [
  ["ig_trades", "Top 50 IG — trades"], ["ig_pv", "Top 50 IG — par value"],
  ["hy_trades", "Top 50 HY — trades"], ["hy_pv", "Top 50 HY — par value"],
  ["conv_trades", "Top 25 Conv — trades"], ["conv_pv", "Top 25 Conv — par value"],
  ["agency_trades", "Top 50 Agency — trades"], ["agency_pv", "Top 50 Agency — par value"],
];

function spark(hist, w = 200, h = 40) {
  if (!hist || hist.length < 2) return `<span class="muted">no history</span>`;
  const vs = hist.map((p) => p.v).filter((v) => v != null);
  if (vs.length < 2) return `<span class="muted">no history</span>`;
  const lo = Math.min(...vs), hi = Math.max(...vs), rng = hi - lo || 1;
  const pts = hist
    .map((p, i) => {
      if (p.v == null) return null;
      const x = (i / (hist.length - 1)) * w;
      const y = h - 3 - ((p.v - lo) / rng) * (h - 6);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  const last = vs[vs.length - 1];
  const cls = last >= vs[0] ? "up" : "down";
  return `<svg width="${w}" height="${h}" class="spark"><polyline points="${pts}" fill="none" stroke="currentColor" class="${cls}" stroke-width="1.5"/></svg>`;
}

function tiles(d) {
  const heads = d.headlines ?? {};
  const cells = PRODUCTS.filter(([p]) => heads[p]).map(([p, label]) => {
    const h = heads[p];
    const hist = (d.hist ?? {})[`fb-${p}-trades`];
    return `<div class="radar-cell">
      <span class="radar-cell-label">${label}</span>
      <span class="radar-cell-value">${big(h.trades)}</span>
      <span class="radar-cell-pct">trades/day · ${usdB(h.pv)}/day</span>
      ${hist ? spark(hist, 150, 30) : ""}
    </div>`;
  }).join("");
  return `<h3>HEADLINES — AVG DAILY <span class="muted">${d.as_of ?? "—"}</span></h3>
    <div class="radar-cells">${cells || `<p class="muted">No Fact Book data yet.</p>`}</div>`;
}

function sizeTable(d, prod) {
  const b = (d.buckets_latest ?? {})[prod];
  if (!b) return `<p class="muted">No size breakdown for this product.</p>`;
  const hist = (d.hist ?? {})[`fb-${prod}-pv`] ?? [];
  const { nom, pct } = qqDelta(hist);
  const s = statsFromValues(hist.map((p) => p.v));
  const rows = BUCKET_ORDER.map((k) =>
    `<tr><td>${BUCKET_LABEL[k]}</td><td>${big(b.trades?.[k])}</td>` +
    `<td>${usdM(b.pv?.[k])}</td></tr>`).join("");
  return `<div class="table-scroll"><table data-sortable><tr><th>Trade size</th><th>Trades/day</th><th>Par/day</th>` +
    `<th>Q/Q Δ</th>${RANGE_TH}</tr>${rows}` +
    `<tr class="total"><td><b>All sizes</b></td><td></td><td></td>` +
    `<td class="num">${deltaCell(nom, pct, (v) => "$" + v.toFixed(1) + "M")}</td>${rangeCells(s, "quarterly")}</tr>` +
    `</table></div>`;
}

function buySellTable(d, prod) {
  const rows = (d.buy_sell_latest ?? {})[prod];
  if (!rows || !rows.length)
    return `<p class="muted">No buy-sell split for this product.</p>`;
  const hist = (d.hist ?? {})[`fb-${prod}-pv`] ?? [];
  const { nom, pct } = qqDelta(hist);
  const s = statsFromValues(hist.map((p) => p.v));
  const trs = rows.map((r) =>
    `<tr><td>${BUCKET_LABEL[r.bucket] ?? r.bucket}</td>` +
    `<td>${usdB(r.gross)}</td><td>${usdM(r.net)}</td><td>${r2(r.ratio)}</td></tr>`).join("");
  return `<div class="table-scroll"><table data-sortable><tr><th>Trade size</th><th>Gross $/qtr</th><th>Net $/qtr</th>` +
    `<th>Buy/Sell</th><th>Q/Q Δ</th>${RANGE_TH}</tr>${trs}` +
    `<tr class="total"><td><b>All sizes</b></td><td></td><td></td><td></td>` +
    `<td class="num">${deltaCell(nom, pct, (v) => "$" + v.toFixed(1) + "M")}</td>${rangeCells(s, "quarterly")}</tr>` +
    `</table></div>
    <p class="muted">Buy/sell = customer buy $ ÷ customer sell $; net = buy − sell. &gt;1 = net buying. Gross/net are quarterly totals, not daily.</p>`;
}

function topTable(d, key) {
  const rows = (d.top ?? {})[key];
  if (!rows || !rows.length) return `<p class="muted">No list yet.</p>`;
  const isPv = key.endsWith("_pv");
  const trs = rows.map((b) =>
    `<tr><td>${b.rank}</td><td><b>${b.symbol ?? "—"}</b></td>` +
    `<td style="text-align:left;white-space:normal">${(b.issuer ?? "").slice(0, 34)}</td>` +
    `<td>${b.coupon ?? "—"}</td><td>${b.maturity ?? "—"}</td>` +
    `<td>${b.rating ?? "—"}</td>` +
    `<td>${isPv ? usdM(b.pv) : big(b.trades)}</td>` +
    `<td>${b.dealers ?? "—"}</td></tr>`).join("");
  return `<div class="table-scroll"><table data-sortable><tr><th>#</th><th>Symbol</th><th style="text-align:left">Issuer</th>` +
    `<th>Cpn</th><th>Maturity</th><th>Rtg</th><th>${isPv ? "Par value" : "Trades"}</th>` +
    `<th>Dealers</th></tr>${trs}</table></div>`;
}

function productOptions(d, sel) {
  const avail = new Set([
    ...Object.keys(d.headlines ?? {}),
    ...Object.keys(d.buckets_latest ?? {}),
    ...Object.keys(d.buy_sell_latest ?? {}),
  ]);
  return PRODUCTS.filter(([p]) => avail.has(p))
    .map(([p, label]) => `<option value="${p}"${p === sel ? " selected" : ""}>${label}</option>`)
    .join("");
}

function topOptions(d, sel) {
  const top = d.top ?? {};
  return TOP_LISTS.filter(([k]) => (top[k] ?? []).length)
    .map(([k, label]) => `<option value="${k}"${k === sel ? " selected" : ""}>${label} (${(top[k] ?? []).length})</option>`)
    .join("");
}

const fmtQ = (ds) => {
  const m = /^(\d{4})-(\d{2})-\d{2}/.exec(ds || "");
  return m ? `Q${Math.ceil(+m[2] / 3)}'${m[1].slice(2)}` : (ds || "");
};

const sortPeriods = (ps) => {
  const key = (p) => {
    const m = /^Q([1-4])\s+(\d{4})/i.exec(p || "");
    if (m) return [+m[2], +m[1]];
    // Bare annual labels ("2025") sort AFTER that year's quarters. The source
    // workbooks carry both, and plotting the annual point before Q1 drew a
    // spurious spike-and-drop in the issues/concentration/trend charts.
    return [/^\d{4}$/.test(p || "") ? +p : 0, 5];
  };
  return [...ps].sort((a, b) => (key(a)[0] - key(b)[0]) || (key(a)[1] - key(b)[1]));
};

// Series colors for the quarterly history chart — match the TRACE chart's
// light-theme palette (blue main series, cyan overlay).
const FB_ADT_COLOR = "#2563eb";
const FB_ADV_COLOR = "#0891b2";

// Quarterly ADT/ADV history chart for one product.
// metric: "adt" | "adv" | "both". "both" overlays the two series on dual
// y-axes (ADT left/blue, ADV right/cyan) — the scales differ by ~5 orders of
// magnitude, so a shared axis would flatten one series into a straight line.
function histChart(d, prod, metric) {
  const both = metric === "both";
  const isAdv = metric === "adv";
  const tradesH = ((d.hist ?? {})[`fb-${prod}-trades`] ?? []).filter((p) => p.v != null);
  const pvH = ((d.hist ?? {})[`fb-${prod}-pv`] ?? []).filter((p) => p.v != null);
  if (!both && (isAdv ? pvH : tradesH).length < 2)
    return `<p class="muted">No quarterly ${isAdv ? "ADV (par)" : "ADT (trades)"} history yet.</p>`;
  if (both && (tradesH.length < 2 || pvH.length < 2))
    return `<p class="muted">No quarterly ADT + ADV history yet.</p>`;
  const label = (PRODUCTS.find(([p]) => p === prod) ?? [prod, prod])[1];
  const W = 660, H = 170, PL = 52, PB = 22, PT = 10, PR = both ? 58 : 0;
  const plotW = W - PL - PR - 10;
  const plotH = H - PT - PB;
  const xlab = (dates, X) => dates.map((dd, i) =>
    (dates.length > 14 && i % 2) ? "" :
    `<text x="${X(i).toFixed(1)}" y="${H - 6}" font-size="9" text-anchor="middle" fill="#6b7280">${fmtQ(dd)}</text>`
  ).join("");
  if (!both) {
    const hist = isAdv ? pvH : tradesH;
    const color = isAdv ? FB_ADV_COLOR : FB_ADT_COLOR;
    const vs = hist.map((p) => p.v);
    const lo = Math.min(...vs), hi = Math.max(...vs), rng = hi - lo || 1;
    const X = (i) => PL + (i / (hist.length - 1)) * plotW;
    const Y = (v) => PT + (1 - (v - lo) / rng) * plotH;
    const pts = hist.map((p, i) => `${X(i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ");
    const fmtV = isAdv ? usdB : big;
    const first = hist[0], last = hist[hist.length - 1];
    const chg = (last.v - first.v) / (first.v || 1);
    const cls = chg >= 0 ? "up" : "down";
    return `<div class="kv"><span>${isAdv ? "ADV — avg-daily par value" : "ADT — avg-daily trades"} · ${label}</span>
      <span class="${cls}">${fmtV(last.v)} <span class="muted">${chg >= 0 ? "+" : ""}${(chg * 100).toFixed(1)}% since ${fmtQ(first.d)}</span></span></div>
      <svg viewBox="0 0 ${W} ${H}" class="strip-chart" preserveAspectRatio="xMidYMid meet">
        <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6"/>
        ${hist.map((p, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="2.6" fill="${color}"><title>${fmtQ(p.d)}: ${fmtV(p.v)}</title></circle>`).join("")}
        <text x="4" y="${(Y(hi) + 3).toFixed(1)}" font-size="9" fill="#6b7280">${fmtV(hi)}</text>
        <text x="4" y="${(Y(lo) + 3).toFixed(1)}" font-size="9" fill="#6b7280">${fmtV(lo)}</text>
        ${xlab(hist.map((p) => p.d), X)}
      </svg>`;
  }
  // BOTH: align the two series on the union of their quarterly dates.
  const tByD = new Map(tradesH.map((p) => [p.d, p.v]));
  const vByD = new Map(pvH.map((p) => [p.d, p.v]));
  const dates = [...new Set([...tByD.keys(), ...vByD.keys()])].sort();
  const tVs = tradesH.map((p) => p.v), vVs = pvH.map((p) => p.v);
  const tLo = Math.min(...tVs), tHi = Math.max(...tVs), tRng = tHi - tLo || 1;
  const vLo = Math.min(...vVs), vHi = Math.max(...vVs), vRng = vHi - vLo || 1;
  const X = (i) => PL + (dates.length < 2 ? 0 : (i / (dates.length - 1)) * plotW);
  const Yt = (v) => PT + (1 - (v - tLo) / tRng) * plotH;
  const Yv = (v) => PT + (1 - (v - vLo) / vRng) * plotH;
  const line = (byD, Y) => dates
    .map((dd, i) => byD.get(dd) == null ? null : `${X(i).toFixed(1)},${Y(byD.get(dd)).toFixed(1)}`)
    .filter(Boolean).join(" ");
  const dots = (byD, Y, color, fmtV) => dates
    .map((dd, i) => byD.get(dd) == null ? "" :
      `<circle cx="${X(i).toFixed(1)}" cy="${Y(byD.get(dd)).toFixed(1)}" r="2.6" fill="${color}"><title>${fmtQ(dd)}: ${fmtV(byD.get(dd))}</title></circle>`)
    .join("");
  const tLast = tradesH[tradesH.length - 1], vLast = pvH[pvH.length - 1];
  return `<div class="kv"><span>ADT + ADV — avg-daily · ${label}</span>
    <span><span style="color:${FB_ADT_COLOR}">●</span> ADT ${big(tLast.v)} <span class="muted">trades/d</span>
    &nbsp;<span style="color:${FB_ADV_COLOR}">●</span> ADV ${usdB(vLast.v)}<span class="muted">/day</span></span></div>
    <svg viewBox="0 0 ${W} ${H}" class="strip-chart" preserveAspectRatio="xMidYMid meet">
      <polyline points="${line(tByD, Yt)}" fill="none" stroke="${FB_ADT_COLOR}" stroke-width="1.6"/>
      <polyline points="${line(vByD, Yv)}" fill="none" stroke="${FB_ADV_COLOR}" stroke-width="1.6"/>
      ${dots(tByD, Yt, FB_ADT_COLOR, big)}${dots(vByD, Yv, FB_ADV_COLOR, usdB)}
      <text x="4" y="${(Yt(tHi) + 3).toFixed(1)}" font-size="9" fill="${FB_ADT_COLOR}">${big(tHi)}</text>
      <text x="4" y="${(Yt(tLo) + 3).toFixed(1)}" font-size="9" fill="${FB_ADT_COLOR}">${big(tLo)}</text>
      <text x="${W - 4}" y="${(Yv(vHi) + 3).toFixed(1)}" font-size="9" text-anchor="end" fill="${FB_ADV_COLOR}">${usdB(vHi)}</text>
      <text x="${W - 4}" y="${(Yv(vLo) + 3).toFixed(1)}" font-size="9" text-anchor="end" fill="${FB_ADV_COLOR}">${usdB(vLo)}</text>
      ${xlab(dates, X)}
    </svg>`;
}

// 15-minute execution grid: bars = % of trades, line = % of par value.
// After Hours bar highlighted.
function intChart(entry) {
  const fine = entry.fine ?? [];
  if (!fine.length)
    return `<p class="muted">No 15-minute grid for this product in FINRA's file (TBA: coarse buckets only).</p>`;
  const parKey = fine[0].par != null ? "par" : (fine[0].opb != null ? "opb" : null);
  const parLabel = parKey === "opb" ? "OPB %" : "Par %";
  const n = fine.length;
  const W = 700, H = 190, PB = 24, PT = 12;
  const bw = (W - 10) / n;
  const tmax = Math.max(...fine.map((x) => x.trades ?? 0));
  const pmax = parKey ? Math.max(...fine.map((x) => x[parKey] ?? 0)) : 0;
  const Y = (v, mx) => PT + (1 - v / (mx || 1)) * (H - PT - PB);
  const bars = fine.map((x, i) => {
    const ah = x.t === "After Hours";
    const y = Y(x.trades ?? 0, tmax);
    return `<rect x="${(4 + i * bw + bw * 0.18).toFixed(1)}" y="${y.toFixed(1)}" width="${(bw * 0.64).toFixed(1)}" height="${(H - PB - y).toFixed(1)}" fill="${ah ? "#e07b39" : "#2563eb"}" opacity="${ah ? 1 : 0.75}"><title>${x.t}: ${(100 * (x.trades ?? 0)).toFixed(2)}% of trades</title></rect>`;
  }).join("");
  const line = parKey ? `<polyline points="${fine.map((x, i) => `${(4 + i * bw + bw * 0.5).toFixed(1)},${Y(x[parKey] ?? 0, pmax).toFixed(1)}`).join(" ")}" fill="none" stroke="#16a34a" stroke-width="1.6"/>` : "";
  const xlab = fine.map((x, i) =>
    (x.t === "After Hours" || i % 6 === 0) ?
    `<text x="${(4 + i * bw + bw * 0.5).toFixed(1)}" y="${H - 8}" font-size="9" text-anchor="middle" fill="#6b7280">${x.t === "After Hours" ? "AH" : x.t}</text>` : ""
  ).join("");
  return `<div class="kv"><span>15-min buckets — % of daily ${entry.scope ? `(${entry.scope})` : ""}</span>
    <span class="muted"><span style="color:#2563eb">■</span> trades${parKey ? ` &nbsp;<span style="color:#16a34a">—</span> ${parLabel}` : ""} &nbsp;<span style="color:#e07b39">■</span> after hours</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="strip-chart" preserveAspectRatio="xMidYMid meet">${bars}${line}${xlab}</svg>`;
}

// Coarse bucket quarterly trend: trades% vs par% across annual+quarterly periods.
function coarseChart(entry, bucket) {
  const c = (entry.coarse ?? {})[bucket];
  if (!c) return `<p class="muted">No bucket history.</p>`;
  const periods = sortPeriods([...new Set([...Object.keys(c.trades ?? {}), ...Object.keys(c.par ?? {})])]);
  if (!periods.length) return `<p class="muted">No bucket history.</p>`;
  const W = 660, H = 150, PL = 44, PB = 22, PT = 10;
  const all = periods.flatMap((p) => [c.trades?.[p], c.par?.[p]]).filter((v) => v != null);
  const mx = Math.max(...all, 1e-9);
  const X = (i) => PL + (periods.length < 2 ? 0 : (i / (periods.length - 1)) * (W - PL - 10));
  const Y = (v) => PT + (1 - v / mx) * (H - PT - PB);
  const line = (obj, color) => {
    const pts = periods.map((p, i) => obj?.[p] == null ? null : `${X(i).toFixed(1)},${Y(obj[p]).toFixed(1)}`).filter(Boolean).join(" ");
    return pts ? `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6"/>` : "";
  };
  const pct = (v) => `${(100 * v).toFixed(2)}%`;
  return `<div class="kv"><span>${bucket} — share of daily activity</span>
    <span class="muted"><span style="color:#2563eb">—</span> trades% &nbsp;<span style="color:#e07b39">—</span> par%</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="strip-chart" preserveAspectRatio="xMidYMid meet">
      ${line(c.trades, "#2563eb")}${line(c.par, "#e07b39")}
      ${periods.map((p, i) => `<text x="${X(i).toFixed(1)}" y="${H - 6}" font-size="9" text-anchor="middle" fill="#6b7280">${p}</text>`).join("")}
      <text x="4" y="${(Y(mx) + 3).toFixed(1)}" font-size="9" fill="#6b7280">${pct(mx)}</text>
      <text x="4" y="${(H - PB + 3).toFixed(1)}" font-size="9" fill="#6b7280">0%</text>
    </svg>`;
}

const INT_PRODUCTS = [
  ["corp", "Corporate"], ["agency", "Agency"], ["abs", "ABS"],
  ["absx", "ABSX"], ["cmo", "CMO"], ["mbs", "MBS"], ["tba", "TBA"],
];

function intYearOptions(d, sel) {
  const yrs = Object.keys(d.interval ?? {}).sort().reverse();
  return yrs.map((y) => `<option value="${y}"${y === sel ? " selected" : ""}>${y}</option>`).join("");
}

function intProdOptions(d, year, sel) {
  const avail = Object.keys((d.interval ?? {})[year] ?? {});
  return INT_PRODUCTS.filter(([p]) => avail.includes(p))
    .map(([p, label]) => {
      const sc = (d.interval[year][p] ?? {}).scope;
      return `<option value="${p}"${p === sel ? " selected" : ""}>${label}${sc ? ` (${sc})` : ""}</option>`;
    }).join("");
}

function intervalBlock(d, year, iprod) {
  const entry = (d.interval ?? {})[year]?.[iprod];
  if (!entry) return `<p class="muted">No time-of-day data for this selection.</p>`;
  const buckets = Object.keys(entry.coarse ?? {});
  const bsel = window.__fbIntBucket && buckets.includes(window.__fbIntBucket)
    ? window.__fbIntBucket
    : (buckets.includes("After Hours") ? "After Hours" : buckets[0]);
  window.__fbIntBucket = bsel;
  return `${intChart(entry)}
    <div class="panel-subhead"><span>BUCKET TREND</span>
      <span class="seg"><select id="fb-intbucket" class="figi-form" style="max-width:220px">${
        buckets.map((b) => `<option value="${b}"${b === bsel ? " selected" : ""}>${b}</option>`).join("")
      }</select></span></div>
    <div id="fb-coarse">${coarseChart(entry, bsel)}</div>
    <p class="muted">${d.interval_note ?? "Time-of-day stats refresh yearly (annual FINRA workbooks)."}</p>`;
}

function annualYearOptions(d, sel) {
  const yrs = Object.keys(d.annual_top ?? {}).sort().reverse();
  return yrs.map((y) => `<option value="${y}"${y === sel ? " selected" : ""}>${y}</option>`).join("");
}

function annualListOptions(d, year, sel) {
  const lists = d.annual_top?.[year] ?? {};
  return TOP_LISTS.filter(([k]) => (lists[k] ?? []).length)
    .map(([k, label]) => `<option value="${k}"${k === sel ? " selected" : ""}>${label} (${(lists[k] ?? []).length})</option>`)
    .join("");
}

// ---------- annual sections ----------

const ISSUE_PRODS = [
  ["corp", "Corporate"], ["conv", "Convertible"], ["agency", "Agency"],
  ["abs", "ABS"], ["absx", "ABSX"], ["cmo", "CMO"], ["mbs", "MBS"],
];
const PART_PRODS = [
  ["corp", "Corporate"], ["agency", "Agency"], ["abs", "ABS"],
  ["absx", "ABSX"], ["cmo", "CMO"], ["mbs", "MBS"], ["tba", "TBA"],
];
const MIX_FMT = { issues: big, trades: big, par: usdM, rpb: usdM };
const MIX_COL_LABEL = { issues: "Issues", trades: "Trades/d", par: "Par/d", rpb: "RPB/d" };

function annYearOptions(d, sel) {
  const yrs = Object.keys(d.annual_adv_adt ?? {}).sort().reverse();
  return yrs.map((y) => `<option value="${y}"${y === sel ? " selected" : ""}>${y}</option>`).join("");
}

function annProdOptions(d, year, sel) {
  const avail = Object.keys((d.annual_adv_adt ?? {})[year] ?? {});
  return PRODUCTS.filter(([p]) => avail.includes(p))
    .map(([p, label]) => `<option value="${p}"${p === sel ? " selected" : ""}>${label}</option>`).join("");
}

function issueYearOptions(d, sel) {
  const yrs = Object.keys(d.issue ?? {}).sort().reverse();
  return yrs.map((y) => `<option value="${y}"${y === sel ? " selected" : ""}>${y}</option>`).join("");
}

function issueProdOptions(d, year, sel) {
  const avail = Object.keys((d.issue ?? {})[year] ?? {});
  return ISSUE_PRODS.filter(([p]) => avail.includes(p))
    .map(([p, label]) => `<option value="${p}"${p === sel ? " selected" : ""}>${label}</option>`).join("");
}

function partYearOptions(d, sel) {
  const yrs = Object.keys(d.participant ?? {}).sort().reverse();
  return yrs.map((y) => `<option value="${y}"${y === sel ? " selected" : ""}>${y}</option>`).join("");
}

function partProdOptions(d, year, sel) {
  const avail = Object.keys((d.participant ?? {})[year] ?? {});
  return PART_PRODS.filter(([p]) => avail.includes(p))
    .map(([p, label]) => `<option value="${p}"${p === sel ? " selected" : ""}>${label}</option>`).join("");
}

// Horizontal bars for one bucket map ({ge25m: v, ...}).
function bucketBars(buckets, fmtV, title) {
  const rows = BUCKET_ORDER.filter((k) => buckets[k] != null);
  if (!rows.length) return `<p class="muted">No bucket data.</p>`;
  const mx = Math.max(...rows.map((k) => buckets[k]));
  const W = 660, RH = 26, LW = 92;
  const H = rows.length * RH + 8;
  const bars = rows.map((k, i) => {
    const v = buckets[k], w = (v / (mx || 1)) * (W - LW - 96);
    const y = i * RH + 4;
    return `<text x="0" y="${y + 15}" font-size="10" fill="#374151">${BUCKET_LABEL[k]}</text>` +
      `<rect x="${LW}" y="${y}" width="${Math.max(w, 1).toFixed(1)}" height="16" fill="#2563eb" opacity="0.8">` +
      `<title>${BUCKET_LABEL[k]}: ${fmtV(v)}</title></rect>` +
      `<text x="${(LW + w + 6).toFixed(1)}" y="${y + 13}" font-size="10" fill="#374151">${fmtV(v)}</text>`;
  }).join("");
  return `<div class="kv"><span>${title}</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="strip-chart" preserveAspectRatio="xMidYMid meet">${bars}</svg>`;
}

// Generic line chart over period labels ("2023", "Q4 2025", ...).
function periodLineChart(pts, fmtV, color = "#2563eb") {
  if (pts.length < 2) return `<p class="muted">Not enough history.</p>`;
  const W = 660, H = 170, PL = 56, PB = 22, PT = 10;
  const vs = pts.map((p) => p.v);
  const lo = Math.min(...vs), hi = Math.max(...vs), rng = hi - lo || 1;
  const X = (i) => PL + (i / (pts.length - 1)) * (W - PL - 10);
  const Y = (v) => PT + (1 - (v - lo) / rng) * (H - PT - PB);
  return `<svg viewBox="0 0 ${W} ${H}" class="strip-chart" preserveAspectRatio="xMidYMid meet">
    <polyline points="${pts.map((p, i) => `${X(i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" ")}" fill="none" stroke="${color}" stroke-width="1.6"/>
    ${pts.map((p, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="2.6" fill="${color}"><title>${p.d}: ${fmtV(p.v)}</title></circle>`).join("")}
    ${pts.map((p, i) => `<text x="${X(i).toFixed(1)}" y="${H - 6}" font-size="9" text-anchor="middle" fill="#6b7280">${p.d}</text>`).join("")}
    <text x="4" y="${(Y(hi) + 3).toFixed(1)}" font-size="9" fill="#6b7280">${fmtV(hi)}</text>
    <text x="4" y="${(Y(lo) + 3).toFixed(1)}" font-size="9" fill="#6b7280">${fmtV(lo)}</text>
  </svg>`;
}

// Multi-tier line chart: {tierLabel: {period: v}}.
function tierChart(chartData, fmtV) {
  const tiers = Object.keys(chartData);
  if (!tiers.length) return `<p class="muted">No tier history.</p>`;
  const periods = sortPeriods([...new Set(tiers.flatMap((t) => Object.keys(chartData[t])))]);
  if (periods.length < 2) return `<p class="muted">Not enough history.</p>`;
  const W = 660, H = 180, PL = 52, PB = 22, PT = 10;
  const all = tiers.flatMap((t) => periods.map((p) => chartData[t][p]).filter((v) => v != null));
  const mx = Math.max(...all, 1e-9), mn = Math.min(...all, 0);
  const X = (i) => PL + (i / (periods.length - 1)) * (W - PL - 10);
  const Y = (v) => PT + (1 - (v - mn) / ((mx - mn) || 1)) * (H - PT - PB);
  const colors = ["#2563eb", "#e07b39", "#16a34a", "#9333ea"];
  const lines = tiers.map((t, ti) => {
    const pts = periods.map((p, i) => chartData[t][p] == null ? null :
      `${X(i).toFixed(1)},${Y(chartData[t][p]).toFixed(1)}`).filter(Boolean).join(" ");
    return pts ? `<polyline points="${pts}" fill="none" stroke="${colors[ti % colors.length]}" stroke-width="1.6"/>` : "";
  }).join("");
  const legend = tiers.map((t, ti) =>
    `<span style="color:${colors[ti % colors.length]}">—</span> ${t}`).join(" &nbsp; ");
  return `<div class="kv"><span class="muted">${legend}</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="strip-chart" preserveAspectRatio="xMidYMid meet">${lines}
    ${periods.map((p, i) => `<text x="${X(i).toFixed(1)}" y="${H - 6}" font-size="9" text-anchor="middle" fill="#6b7280">${p}</text>`).join("")}
    <text x="4" y="${(Y(mx) + 3).toFixed(1)}" font-size="9" fill="#6b7280">${fmtV(mx)}</text>
    <text x="4" y="${(Y(mn) + 3).toFixed(1)}" font-size="9" fill="#6b7280">${fmtV(mn)}</text>
  </svg>`;
}

function defaultFlavor(entry, metric) {
  const flavors = Object.keys(entry.buy_sell ?? {});
  if (!flavors.length) return null;
  const want = metric === "adv" ? "par" : "trades";
  return flavors.find((f) => f === want)
      ?? flavors.find((f) => f.includes(want))
      ?? flavors[0];
}

// Annual buy-sell: gross/net are ANNUAL totals (counts/yr for the trades
// flavor, $/yr for par) — the ratio is unit-free.
function annBuySellTable(entry, flavor) {
  const bs = entry?.buy_sell?.[flavor];
  if (!bs) return `<p class="muted">No buy-sell split for this flavor.</p>`;
  const isTrades = flavor.startsWith("trades");
  const unit = isTrades ? "trades/yr" : "$/yr";
  const gf = isTrades ? big : usdB, nf = isTrades ? big : usdM;
  const rows = BUCKET_ORDER.filter((k) => bs[k]).map((k) => {
    const r = bs[k];
    return `<tr><td>${BUCKET_LABEL[k]}</td><td>${gf(r.gross)}</td>` +
      `<td>${nf(r.net)}</td><td>${r2(r.ratio)}</td></tr>`;
  }).join("");
  return `<div class="table-scroll"><table data-sortable><tr><th>Trade size</th><th>Gross ${unit}</th>` +
    `<th>Net ${unit}</th><th>Buy/Sell</th></tr>${rows}</table></div>
    <p class="muted">Ratio is unit-free (&gt;1 = net buying). Gross/net are ${isTrades ? "annual trade counts" : "annual $ totals"}, not daily.</p>`;
}

function annAdvAdtBlock(d, year, prod, metric) {
  const entry = d.annual_adv_adt?.[year]?.[prod];
  if (!entry) return `<p class="muted">No annual ADV/ADT for this selection.</p>`;
  const isAdv = metric === "adv";
  const leg = entry[isAdv ? "par" : "trades"] ?? {};
  const label = (PRODUCTS.find(([p]) => p === prod) ?? [prod, prod])[1];
  const fmtV = isAdv ? usdB : big;
  const unit = isAdv ? "par value/day" : "trades/day";
  const flavors = Object.keys(entry.buy_sell ?? {});
  const flavor = window.__fbAnnFlavor && flavors.includes(window.__fbAnnFlavor)
    ? window.__fbAnnFlavor : defaultFlavor(entry, metric);
  window.__fbAnnFlavor = flavor;
  return `
    <div class="kv"><span>${year} full-year average — ${label}</span>
      <span>${fmtV(leg.total)} <span class="muted">${unit}</span></span></div>
    ${bucketBars(leg.buckets ?? {}, fmtV, `${isAdv ? "ADV" : "ADT"} by trade size — ${year}`)}
    <div class="panel-subhead"><span>CUSTOMER BUY-SELL — ${year} ANNUAL TOTALS</span>
      <span class="seg"><select id="fb-annflavor" class="figi-form" style="max-width:190px">${
        flavors.map((f) => `<option value="${f}"${f === flavor ? " selected" : ""}>${f}</option>`).join("")
      }</select></span></div>
    <div id="fb-annbs">${annBuySellTable(entry, flavor)}</div>`;
}

function issueBlock(d, year, prod) {
  const entry = d.issue?.[year]?.[prod];
  if (!entry) return `<p class="muted">No issues-outstanding data for this selection.</p>`;
  const label = (ISSUE_PRODS.find(([p]) => p === prod) ?? [prod, prod])[1];
  const periods = sortPeriods(Object.keys(entry.total ?? {}));
  const pts = periods.map((p) => ({ d: p, v: entry.total[p] })).filter((x) => x.v != null);
  const latest = periods[periods.length - 1];
  const first = pts[0], last = pts[pts.length - 1];
  const chg = last && first && first.v ? (last.v - first.v) / first.v : null;
  const bdRows = Object.entries(entry.breakdown ?? {})
    .map(([lab, per]) => [lab, per[latest]])
    .filter(([, v]) => v != null)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([lab, v]) => `<tr><td style="text-align:left">${lab}</td><td>${big(v)}</td></tr>`)
    .join("");
  const mix = d.issue_mix?.[year]?.[prod];
  const mixCols = mix ? [...new Set(Object.values(mix).flatMap((o) => Object.keys(o)))] : [];
  const mixRows = mix ? Object.entries(mix)
    .sort((a, b) => (b[1].issues ?? b[1].trades ?? 0) - (a[1].issues ?? a[1].trades ?? 0))
    .slice(0, 14)
    .map(([lab, o]) => `<tr><td style="text-align:left">${lab}</td>` +
      mixCols.map((c) => `<td>${(MIX_FMT[c] ?? big)(o[c])}</td>`).join("") + `</tr>`).join("") : "";
  return `
    <div class="kv"><span>Issues outstanding — ${label} (${entry.label ?? ""})</span>
      <span>${big(last?.v)} <span class="muted">as of ${latest ?? "—"}${
        chg != null ? ` · ${chg >= 0 ? "+" : ""}${(chg * 100).toFixed(1)}% since ${first.d}` : ""}</span></span></div>
    ${periodLineChart(pts, big)}
    <div class="kv"><span>Breakdown — ${latest ?? ""} (counts)</span></div>
    <div class="table-scroll"><table data-sortable><tr><th style="text-align:left">Segment</th><th>Issues</th></tr>${bdRows || `<tr><td colspan="2" class="muted">—</td></tr>`}</table></div>
    ${mix ? `<div class="kv"><span>Mix snapshot — ${year} (issues / avg-daily S1 trades / par)</span></div>
    <div class="table-scroll"><table data-sortable><tr><th style="text-align:left">Segment</th>${mixCols.map((c) => `<th>${MIX_COL_LABEL[c] ?? c}</th>`).join("")}</tr>${mixRows}</table></div>` : ""}
    <p class="muted">${d.issue_note ?? "Issues outstanding are counts of CUSIPs; FINRA publishes no par outstanding. Annual refresh."}</p>`;
}

function partBlock(d, year, prod, metric) {
  const entry = d.participant?.[year]?.[prod];
  if (!entry) return `<p class="muted">No participant data for this selection.</p>`;
  const label = (PART_PRODS.find(([p]) => p === prod) ?? [prod, prod])[1];
  const isPar = metric === "par";
  const src = isPar ? entry.par_pct : entry.trades_pct;
  const chartData = {};
  for (const t of entry.tiers ?? []) if (src?.[t]) chartData[`Top ${t}`] = src[t];
  const periods = sortPeriods([...new Set(Object.values(chartData).flatMap((o) => Object.keys(o)))]);
  const latest = periods[periods.length - 1];
  const firms = latest
    ? `${big(entry.firms_reporting?.[latest])} reporting firms · ${big(entry.unique_firms?.[latest])} unique · ${big(entry.avg_firms_per_day?.[latest])}/day avg`
    : "—";
  const topLine = (entry.tiers ?? []).map((t) => {
    const v = src?.[t]?.[latest];
    return v == null ? "" : `<span><b>Top ${t}</b> ${(100 * v).toFixed(1)}%</span>`;
  }).filter(Boolean).join(" &nbsp;·&nbsp; ");
  return `
    <div class="kv"><span>% of S1 ${isPar ? "par value" : "trades"} captured — ${label} · ${entry.segment ?? ""}</span>
      <span class="muted">${firms}</span></div>
    <div class="kv"><span class="muted">${latest ?? ""}:</span><span>${topLine || "—"}</span></div>
    ${tierChart(chartData, (v) => `${(100 * v).toFixed(1)}%`)}
    <p class="muted">${d.participant_note ?? "Dealer-concentration stats refresh yearly; no quarterly equivalent."}</p>`;
}

export function renderFactbook(data) {
  const host = document.querySelector("#panel-factbook .panel-body");
  if (!host) return;
  const d = data ?? {};
  if (!d.as_of) {
    host.innerHTML = `<h3>TRACE FACT BOOK</h3><p class="muted">No Fact Book data yet — the quarterly fetcher has not run.</p>`;
    return;
  }
  const prod = window.__fbProd && d.headlines?.[window.__fbProd]
    ? window.__fbProd : (PRODUCTS.find(([p]) => d.headlines?.[p]) ?? ["ig"])[0];
  const list = window.__fbList && d.top?.[window.__fbList]?.length
    ? window.__fbList : (TOP_LISTS.find(([k]) => (d.top?.[k] ?? []).length) ?? ["ig_trades"])[0];
  const metric = ["adt", "adv", "both"].includes(window.__fbMetric) ? window.__fbMetric : "adt";
  const iyear = window.__fbIntYear && d.interval?.[window.__fbIntYear]
    ? window.__fbIntYear : (Object.keys(d.interval ?? {}).sort().reverse()[0] ?? null);
  const iprod = window.__fbIntProd && d.interval?.[iyear]?.[window.__fbIntProd]
    ? window.__fbIntProd : (Object.keys(d.interval?.[iyear] ?? {})[0] ?? null);
  const ayear = window.__fbAYear && d.annual_top?.[window.__fbAYear]
    ? window.__fbAYear : (Object.keys(d.annual_top ?? {}).sort().reverse()[0] ?? null);
  const alist = window.__fbAList && d.annual_top?.[ayear]?.[window.__fbAList]?.length
    ? window.__fbAList : ((TOP_LISTS.find(([k]) => (d.annual_top?.[ayear]?.[k] ?? []).length) ?? [null])[0]);
  const annyear = window.__fbAnnYear && d.annual_adv_adt?.[window.__fbAnnYear]
    ? window.__fbAnnYear : (Object.keys(d.annual_adv_adt ?? {}).sort().reverse()[0] ?? null);
  const annprod = window.__fbAnnProd && d.annual_adv_adt?.[annyear]?.[window.__fbAnnProd]
    ? window.__fbAnnProd : (Object.keys(d.annual_adv_adt?.[annyear] ?? {})[0] ?? null);
  const annmetric = window.__fbAnnMetric === "adv" ? "adv" : "adt";
  const issueyear = window.__fbIssueYear && d.issue?.[window.__fbIssueYear]
    ? window.__fbIssueYear : (Object.keys(d.issue ?? {}).sort().reverse()[0] ?? null);
  const issueprod = window.__fbIssueProd && d.issue?.[issueyear]?.[window.__fbIssueProd]
    ? window.__fbIssueProd : (Object.keys(d.issue?.[issueyear] ?? {})[0] ?? null);
  const partyear = window.__fbPartYear && d.participant?.[window.__fbPartYear]
    ? window.__fbPartYear : (Object.keys(d.participant ?? {}).sort().reverse()[0] ?? null);
  const partprod = window.__fbPartProd && d.participant?.[partyear]?.[window.__fbPartProd]
    ? window.__fbPartProd : (Object.keys(d.participant?.[partyear] ?? {})[0] ?? null);
  const partmetric = window.__fbPartMetric === "par" ? "par" : "trades";
  window.__fbProd = prod;
  window.__fbList = list;
  window.__fbMetric = metric;
  window.__fbIntYear = iyear;
  window.__fbIntProd = iprod;
  window.__fbAYear = ayear;
  window.__fbAList = alist;
  window.__fbAnnYear = annyear;
  window.__fbAnnProd = annprod;
  window.__fbAnnMetric = annmetric;
  window.__fbIssueYear = issueyear;
  window.__fbIssueProd = issueprod;
  window.__fbPartYear = partyear;
  window.__fbPartProd = partprod;
  window.__fbPartMetric = partmetric;

  host.innerHTML = `
    <h3>TRACE FACT BOOK <span class="muted">${d.as_of} · quarter ended ${d.quarter_end ?? "—"}</span></h3>
    ${tiles(d)}
    <div class="panel-subhead"><span>SIZE DISTRIBUTION — AVG DAILY</span>
      <span class="seg"><select id="fb-prod" class="figi-form" style="max-width:220px">${productOptions(d, prod)}</select></span></div>
    <div id="fb-size">${sizeTable(d, prod)}</div>
    <div class="panel-subhead"><span>CUSTOMER BUY-SELL — PAR VALUE/QUARTER</span></div>
    <div id="fb-bs">${buySellTable(d, prod)}</div>
    <div class="panel-subhead"><span>QUARTERLY HISTORY</span>
      <span class="seg" id="fb-metric"><button data-m="adt"${metric === "adt" ? ' class="on"' : ""}>ADT</button><button data-m="adv"${metric === "adv" ? ' class="on"' : ""}>ADV</button><button data-m="both"${metric === "both" ? ' class="on"' : ""} title="Overlay ADT (blue, left axis) and ADV (cyan, right axis)">BOTH</button></span></div>
    <div id="fb-hist">${histChart(d, prod, metric)}</div>
    <div class="panel-subhead"><span>MOST-TRADED ISSUES — QUARTERLY</span>
      <span class="seg"><select id="fb-list" class="figi-form" style="max-width:220px">${topOptions(d, list)}</select></span></div>
    <div id="fb-top">${topTable(d, list)}</div>
    <div class="panel-subhead"><span>TIME OF DAY — EXECUTION STATS <span class="muted">· annual refresh</span></span>
      <span class="seg"><select id="fb-intyear" class="figi-form" style="max-width:110px">${intYearOptions(d, iyear)}</select>
      <select id="fb-intprod" class="figi-form" style="max-width:200px">${intProdOptions(d, iyear, iprod)}</select></span></div>
    <div id="fb-int">${iyear && iprod ? intervalBlock(d, iyear, iprod) : `<p class="muted">No time-of-day data yet.</p>`}</div>
    <div class="panel-subhead"><span>MOST-TRADED ISSUES — ANNUAL</span>
      <span class="seg"><select id="fb-ayear" class="figi-form" style="max-width:110px">${annualYearOptions(d, ayear)}</select>
      <select id="fb-alist" class="figi-form" style="max-width:220px">${annualListOptions(d, ayear, alist)}</select></span></div>
    <div id="fb-atop">${ayear && alist ? topTable({ top: d.annual_top[ayear] }, alist) : `<p class="muted">No annual top lists yet.</p>`}</div>
    <div class="panel-subhead"><span>ANNUAL ADV/ADT — FULL YEAR <span class="muted">· annual refresh</span></span>
      <span class="seg"><select id="fb-annyear" class="figi-form" style="max-width:110px">${annYearOptions(d, annyear)}</select>
      <select id="fb-annprod" class="figi-form" style="max-width:200px">${annProdOptions(d, annyear, annprod)}</select>
      <span class="seg" id="fb-annmetric"><button data-m="adt"${annmetric === "adt" ? ' class="on"' : ""}>ADT</button><button data-m="adv"${annmetric === "adv" ? ' class="on"' : ""}>ADV</button></span></span></div>
    <div id="fb-ann">${annyear && annprod ? annAdvAdtBlock(d, annyear, annprod, annmetric) : `<p class="muted">No annual ADV/ADT yet.</p>`}</div>
    <div class="panel-subhead"><span>ISSUES OUTSTANDING — ANNUAL <span class="muted">· counts, annual refresh</span></span>
      <span class="seg"><select id="fb-issueyear" class="figi-form" style="max-width:110px">${issueYearOptions(d, issueyear)}</select>
      <select id="fb-issueprod" class="figi-form" style="max-width:200px">${issueProdOptions(d, issueyear, issueprod)}</select></span></div>
    <div id="fb-issue">${issueyear && issueprod ? issueBlock(d, issueyear, issueprod) : `<p class="muted">No issues-outstanding data yet.</p>`}</div>
    <div class="panel-subhead"><span>DEALER CONCENTRATION — ANNUAL <span class="muted">· annual refresh</span></span>
      <span class="seg"><select id="fb-partyear" class="figi-form" style="max-width:110px">${partYearOptions(d, partyear)}</select>
      <select id="fb-partprod" class="figi-form" style="max-width:200px">${partProdOptions(d, partyear, partprod)}</select>
      <span class="seg" id="fb-partmetric"><button data-m="trades"${partmetric === "trades" ? ' class="on"' : ""}>Trades</button><button data-m="par"${partmetric === "par" ? ' class="on"' : ""}>Par</button></span></span></div>
    <div id="fb-part">${partyear && partprod ? partBlock(d, partyear, partprod, partmetric) : `<p class="muted">No participant data yet.</p>`}</div>`;

  host.querySelector("#fb-prod")?.addEventListener("change", (e) => {
    window.__fbProd = e.target.value;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-size").innerHTML = sizeTable(dd, window.__fbProd);
    host.querySelector("#fb-bs").innerHTML = buySellTable(dd, window.__fbProd);
    host.querySelector("#fb-hist").innerHTML = histChart(dd, window.__fbProd, window.__fbMetric);
  });
  host.querySelector("#fb-list")?.addEventListener("change", (e) => {
    window.__fbList = e.target.value;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-top").innerHTML = topTable(dd, window.__fbList);
  });
  host.querySelector("#fb-metric")?.addEventListener("click", (e) => {
    const m = e.target?.dataset?.m;
    if (m !== "adt" && m !== "adv" && m !== "both") return;
    window.__fbMetric = m;
    host.querySelectorAll("#fb-metric button").forEach((b) =>
      b.classList.toggle("on", b.dataset.m === m));
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-hist").innerHTML = histChart(dd, window.__fbProd, m);
  });
  const rerenderInt = () => {
    const dd = window.__fbData;
    if (!dd || !window.__fbIntYear || !window.__fbIntProd) return;
    host.querySelector("#fb-int").innerHTML =
      intervalBlock(dd, window.__fbIntYear, window.__fbIntProd);
    wireIntBucket();
  };
  const wireIntBucket = () => {
    host.querySelector("#fb-intbucket")?.addEventListener("change", (e) => {
      window.__fbIntBucket = e.target.value;
      const dd = window.__fbData;
      if (!dd) return;
      const entry = dd.interval?.[window.__fbIntYear]?.[window.__fbIntProd];
      if (entry) host.querySelector("#fb-coarse").innerHTML = coarseChart(entry, window.__fbIntBucket);
    });
  };
  wireIntBucket();
  host.querySelector("#fb-intyear")?.addEventListener("change", (e) => {
    window.__fbIntYear = e.target.value;
    window.__fbIntProd = null;
    window.__fbIntBucket = null;
    const dd = window.__fbData;
    if (!dd) return;
    // rebuild the product selector for the new year, then the block
    host.querySelector("#fb-intprod").innerHTML = intProdOptions(dd, window.__fbIntYear, null);
    window.__fbIntProd = host.querySelector("#fb-intprod")?.value ?? null;
    rerenderInt();
  });
  host.querySelector("#fb-intprod")?.addEventListener("change", (e) => {
    window.__fbIntProd = e.target.value;
    window.__fbIntBucket = null;
    rerenderInt();
  });
  host.querySelector("#fb-ayear")?.addEventListener("change", (e) => {
    window.__fbAYear = e.target.value;
    window.__fbAList = null;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-alist").innerHTML = annualListOptions(dd, window.__fbAYear, null);
    window.__fbAList = host.querySelector("#fb-alist")?.value ?? null;
    host.querySelector("#fb-atop").innerHTML = window.__fbAList
      ? topTable({ top: dd.annual_top[window.__fbAYear] }, window.__fbAList)
      : `<p class="muted">No annual top lists yet.</p>`;
  });
  host.querySelector("#fb-alist")?.addEventListener("change", (e) => {
    window.__fbAList = e.target.value;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-atop").innerHTML =
      topTable({ top: dd.annual_top[window.__fbAYear] }, window.__fbAList);
  });
  const rerenderAnn = () => {
    const dd = window.__fbData;
    if (!dd || !window.__fbAnnYear || !window.__fbAnnProd) return;
    host.querySelector("#fb-ann").innerHTML =
      annAdvAdtBlock(dd, window.__fbAnnYear, window.__fbAnnProd, window.__fbAnnMetric);
    wireAnnFlavor();
  };
  const wireAnnFlavor = () => {
    host.querySelector("#fb-annflavor")?.addEventListener("change", (e) => {
      window.__fbAnnFlavor = e.target.value;
      const dd = window.__fbData;
      if (!dd) return;
      const entry = dd.annual_adv_adt?.[window.__fbAnnYear]?.[window.__fbAnnProd];
      if (entry) host.querySelector("#fb-annbs").innerHTML = annBuySellTable(entry, window.__fbAnnFlavor);
    });
  };
  wireAnnFlavor();
  host.querySelector("#fb-annyear")?.addEventListener("change", (e) => {
    window.__fbAnnYear = e.target.value;
    window.__fbAnnProd = null;
    window.__fbAnnFlavor = null;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-annprod").innerHTML = annProdOptions(dd, window.__fbAnnYear, null);
    window.__fbAnnProd = host.querySelector("#fb-annprod")?.value ?? null;
    rerenderAnn();
  });
  host.querySelector("#fb-annprod")?.addEventListener("change", (e) => {
    window.__fbAnnProd = e.target.value;
    window.__fbAnnFlavor = null;
    rerenderAnn();
  });
  host.querySelector("#fb-annmetric")?.addEventListener("click", (e) => {
    const m = e.target?.dataset?.m;
    if (m !== "adt" && m !== "adv") return;
    window.__fbAnnMetric = m;
    host.querySelectorAll("#fb-annmetric button").forEach((b) =>
      b.classList.toggle("on", b.dataset.m === m));
    rerenderAnn();
  });
  host.querySelector("#fb-issueyear")?.addEventListener("change", (e) => {
    window.__fbIssueYear = e.target.value;
    window.__fbIssueProd = null;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-issueprod").innerHTML = issueProdOptions(dd, window.__fbIssueYear, null);
    window.__fbIssueProd = host.querySelector("#fb-issueprod")?.value ?? null;
    if (window.__fbIssueYear && window.__fbIssueProd)
      host.querySelector("#fb-issue").innerHTML = issueBlock(dd, window.__fbIssueYear, window.__fbIssueProd);
  });
  host.querySelector("#fb-issueprod")?.addEventListener("change", (e) => {
    window.__fbIssueProd = e.target.value;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-issue").innerHTML = issueBlock(dd, window.__fbIssueYear, window.__fbIssueProd);
  });
  host.querySelector("#fb-partyear")?.addEventListener("change", (e) => {
    window.__fbPartYear = e.target.value;
    window.__fbPartProd = null;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-partprod").innerHTML = partProdOptions(dd, window.__fbPartYear, null);
    window.__fbPartProd = host.querySelector("#fb-partprod")?.value ?? null;
    if (window.__fbPartYear && window.__fbPartProd)
      host.querySelector("#fb-part").innerHTML = partBlock(dd, window.__fbPartYear, window.__fbPartProd, window.__fbPartMetric);
  });
  host.querySelector("#fb-partprod")?.addEventListener("change", (e) => {
    window.__fbPartProd = e.target.value;
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-part").innerHTML = partBlock(dd, window.__fbPartYear, window.__fbPartProd, window.__fbPartMetric);
  });
  host.querySelector("#fb-partmetric")?.addEventListener("click", (e) => {
    const m = e.target?.dataset?.m;
    if (m !== "trades" && m !== "par") return;
    window.__fbPartMetric = m;
    host.querySelectorAll("#fb-partmetric button").forEach((b) =>
      b.classList.toggle("on", b.dataset.m === m));
    const dd = window.__fbData;
    if (!dd) return;
    host.querySelector("#fb-part").innerHTML = partBlock(dd, window.__fbPartYear, window.__fbPartProd, m);
  });
  window.__fbData = d;
}
