// Finance tab group: Banks / Primary Dealers / Broker-Dealers / ATS /
// Depository Institutions. Reads dash.panels.finance (backend
// _finance_panel). In-panel sub-tabs follow the finra.js pattern; index.html
// and tabs.js are untouched except for the new FLOW "Finance" sub entry.
import { getSeries } from "../api.js";
import { heatStyle, HEAT_LEGEND } from "../heatmap.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usdB = (x) =>
  x == null ? "—" : "$" + (x / 1e9).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "B";
const usdM = (x) =>
  x == null ? "—" : "$" + (x / 1e6).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "M";
const pct1 = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");
const num0 = (x) => (x == null ? "—" : x.toLocaleString("en-US", { maximumFractionDigits: 0 }));
const qlabel = (iso) => {
  if (!iso) return "—";
  const [y, m] = iso.split("-").map(Number);
  return `Q${Math.ceil(m / 3)} ${y}`;
};

const BANK_METRICS = [
  ["assets", "Total assets", usdB],
  ["deposits", "Deposits", usdB],
  ["loans", "Loans (net)", usdB],
  ["equity", "Stockholders equity", usdB],
  ["debt", "Long-term debt", usdB],
  ["nii", "Net interest income", usdM],
  ["nonint_income", "Non-interest income", usdM],
  ["nonint_expense", "Non-interest expense", usdM],
  ["net_income", "Net income", usdM],
  ["nim_proxy", "NIM proxy (TTM)", pct1],
  ["loan_to_deposit", "Loan-to-deposit", pct1],
  ["debt_to_equity", "Debt / equity", (x) => (x == null ? "—" : x.toFixed(2) + "x")],
  ["roa_ttm", "ROA (TTM)", pct1],
  ["roe_ttm", "ROE (TTM)", pct1],
  ["yoy_nii", "NII YoY", pct1],
  ["yoy_net_income", "Net income YoY", pct1],
];
const METRIC_FMT = Object.fromEntries(BANK_METRICS.map(([k, , f]) => [k, f]));
const METRIC_LABEL = Object.fromEntries(BANK_METRICS.map(([k, l]) => [k, l]));

const state = { tab: "banks", bank: "JPM", sort: { key: "assets", dir: -1 } };

function srcLine(label, upd, src) {
  return `<div class="muted" style="margin:4px 0 10px">Source: ${esc(label)}` +
    (upd ? ` · as of ${esc(upd)}` : " · not yet fetched") +
    (src ? ` · ${esc(src)}` : "") + `</div>`;
}

function tagBadges(tags) {
  if (!tags || !tags.length) return "";
  return " " + tags.map((t) =>
    `<span class="badge" title="curated tag">${esc(t.replace("_", " "))}</span>`).join(" ");
}

// ---------------- banks ----------------
function bankKpis(co) {
  const tiles = [
    ["Total assets", usdB(co.assets)],
    ["Deposits", usdB(co.deposits)],
    ["Loans (net)", usdB(co.loans)],
    ["Net interest income", usdM(co.nii)],
    ["NIM proxy (TTM)", pct1(co.nim_proxy)],
    ["ROE (TTM)", pct1(co.roe_ttm)],
    ["Net income", usdM(co.net_income)],
    ["Loan-to-deposit", pct1(co.loan_to_deposit)],
  ];
  return `<div class="kpis">` + tiles.map(([l, v]) =>
    `<div class="kpi"><div class="kpi-l">${esc(l)}</div>` +
    `<div class="kpi-v">${esc(v)}</div>` +
    `<div class="kpi-s muted">${esc(qlabel(co.latest_quarter))} quarter</div></div>`
  ).join("") + `</div>`;
}

async function bankSeries(ticker, metric) {
  try {
    const r = await getSeries(`bank_fixed_income:${ticker}:${metric}`, "10y");
    return (r.points ?? []).map(([d, v]) => ({ d, v }));
  } catch {
    return [];
  }
}

function lineChart(el, seriesList, fmt) {
  // seriesList: [{label, color, pts:[{d,v}]}]
  el.innerHTML = "";
  const byDate = new Map();
  seriesList.forEach((s, i) => (s.pts ?? []).forEach((p) => {
    const row = byDate.get(p.d) ?? seriesList.map(() => null);
    row[i] = p.v;
    byDate.set(p.d, row);
  }));
  const dates = [...byDate.keys()].sort();
  if (dates.length < 2 || typeof uPlot === "undefined") {
    el.innerHTML = `<span class="muted">no history yet</span>`;
    return;
  }
  const xs = dates.map((d) => Date.parse(d) / 1000);
  const cols = seriesList.map((_, i) => dates.map((d) => byDate.get(d)[i]));
  const axisStyle = { stroke: "#6b7280", grid: { stroke: "#e5e7eb" } };
  const isPct = seriesList.every((s) => s.pct);
  new uPlot({
    width: Math.max(280, el.clientWidth || 600), height: 240,
    scales: { x: { time: true } },
    series: [{},
      ...seriesList.map((s) => ({ label: s.label, stroke: s.color, width: 2, spanGaps: true }))],
    axes: [{ ...axisStyle },
      { ...axisStyle, values: (u, v) => v.map((x) =>
        x == null ? "" : (isPct ? (x * 100).toFixed(2) + "%" : fmt(x))) }],
    legend: { show: true },
  }, [xs, ...cols], el);
}

async function renderBankDetail(fin, ticker) {
  const wrap = document.getElementById("fin-bank-detail");
  if (!wrap) return;
  const co = fin.companies[ticker];
  if (!co) { wrap.innerHTML = `<p class="muted">No data for ${esc(ticker)} yet.</p>`; return; }
  wrap.innerHTML = bankKpis(co) +
    `<div class="grid3" style="margin-top:12px">
       <div><h4>Net interest income <span class="muted">quarterly</span></h4><div id="fin-ch-nii"></div></div>
       <div><h4>Deposits vs loans <span class="muted">quarter-end</span></h4><div id="fin-ch-dep"></div></div>
       <div><h4>NIM proxy <span class="muted">TTM</span></h4><div id="fin-ch-nim"></div></div>
     </div>
     <h4>Statements <span class="muted">last 8 quarters · $M except ratios</span></h4>
     <div id="fin-stmt"><p class="muted">Loading statements…</p></div>`;
  const [nii, dep, loa, nim] = await Promise.all([
    bankSeries(ticker, "nii"), bankSeries(ticker, "deposits"),
    bankSeries(ticker, "loans"), bankSeries(ticker, "nim_proxy"),
  ]);
  lineChart(document.getElementById("fin-ch-nii"),
    [{ label: "NII", color: "#2563eb", pts: nii }], usdM);
  lineChart(document.getElementById("fin-ch-dep"), [
    { label: "Deposits", color: "#2563eb", pts: dep },
    { label: "Loans", color: "#0891b2", pts: loa },
  ], usdB);
  lineChart(document.getElementById("fin-ch-nim"),
    [{ label: "NIM proxy", color: "#7c3aed", pts: nim, pct: true }], pct1);
  // statements table: last 8 quarters
  const metrics = ["assets", "deposits", "loans", "equity", "nii",
    "nonint_income", "net_income", "nim_proxy", "roe_ttm", "loan_to_deposit"];
  const data = {};
  await Promise.all(metrics.map(async (m) => { data[m] = await bankSeries(ticker, m); }));
  const qmap = new Map();
  metrics.forEach((m) => (data[m] ?? []).forEach((p) => {
    if (!qmap.has(p.d)) qmap.set(p.d, {});
    qmap.get(p.d)[m] = p.v;
  }));
  const qs = [...qmap.keys()].sort().slice(-8);
  const stmtEl = document.getElementById("fin-stmt");
  if (!qs.length) { stmtEl.innerHTML = `<p class="muted">No quarterly history yet.</p>`; return; }
  const money = (m, v) => v == null ? "—"
    : (["nim_proxy", "roe_ttm", "loan_to_deposit"].includes(m) ? pct1(v)
      : "$" + (v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 0 }) + "M");
  stmtEl.innerHTML =
    `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Metric</th>` +
    qs.map((q) => `<th class="r">${esc(qlabel(q))}</th>`).join("") +
    `</tr></thead><tbody>` +
    metrics.map((m) => `<tr><td>${esc(METRIC_LABEL[m])}</td>` +
      qs.map((q) => `<td class="r">${esc(money(m, qmap.get(q)[m]))}</td>`).join("") +
      `</tr>`).join("") +
    `</tbody></table></div>` +
    `<div class="muted" style="margin-top:6px">NIM proxy = TTM net interest income / average total assets ` +
    `(banks rarely tag average earning assets; labeled proxy). Income items are de-annualized to true calendar ` +
    `quarters (10-Q facts are YTD; Q4 = 10-K annual minus Q3 YTD).</div>`;
}

function renderBankCompare(fin) {
  const cos = Object.entries(fin.companies ?? {});
  if (!cos.length) return `<p class="muted">No bank financials yet (the bank_financials job hasn't run).</p>`;
  const cols = ["assets", "deposits", "loans", "nii", "nim_proxy", "roe_ttm", "yoy_nii", "net_income"];
  const { key, dir } = state.sort;
  const rows = cos.map(([t, c]) => ({ t, c }))
    .sort((a, b) => {
      const av = a.c[key] ?? -Infinity, bv = b.c[key] ?? -Infinity;
      return (av - bv) * dir;
    });
  const th = (k, label) =>
    `<th class="r sortable" data-k="${k}">${esc(label)}${key === k ? (dir < 0 ? " ▼" : " ▲") : ""}</th>`;
  return `<div class="tbl-wrap"><table class="tbl" id="fin-compare"><thead><tr><th>Bank</th>` +
    cols.map((k) => th(k, METRIC_LABEL[k])).join("") + `</tr></thead><tbody>` +
    rows.map(({ t, c }) => `<tr><td><b>${esc(t)}</b> <span class="muted">${esc(c.name)}</span></td>` +
      cols.map((k) => {
        const v = c[k];
        const txt = METRIC_FMT[k] ? METRIC_FMT[k](v) : String(v ?? "—");
        const hs = (["yoy_nii", "yoy_net_income"].includes(k) && v != null)
          ? heatStyle({ pct: v }) : "";
        return `<td class="r"${hs}>${esc(txt)}</td>`;
      }).join("") + `</tr>`).join("") +
    `</tbody></table></div>` +
    `<div class="muted" style="margin-top:6px">${HEAT_LEGEND} · click a column header to sort · ` +
    `latest quarter per bank (${esc(qlabel(cos[0][1].latest_quarter))} for most)</div>`;
}

function renderBanksView(fin) {
  const cos = fin.companies ?? {};
  const tickers = Object.keys(cos).sort();
  if (!tickers.length) {
    return `<div class="notice">No bank financials yet (the <code>bank_financials</code> job hasn't run). ` +
      `Directories below are available.</div>`;
  }
  if (!cos[state.bank]) state.bank = tickers[0];
  const sel = `<label class="muted">Bank: <select id="fin-bank-sel">` +
    tickers.map((t) =>
      `<option value="${esc(t)}"${t === state.bank ? " selected" : ""}>${esc(t)}: ${esc(cos[t].name)}</option>`
    ).join("") + `</select></label>`;
  return `<h3>BANK FINANCIALS <span class="muted">SEC XBRL quarterly · three-statement</span></h3>` +
    srcLine("SEC EDGAR XBRL companyconcept (free)", fin.as_of, null) +
    `<div style="margin:8px 0">${sel}</div>` +
    `<div id="fin-bank-detail"></div>` +
    `<h3 style="margin-top:16px">COMPARISON <span class="muted">latest reported quarter per bank</span></h3>` +
    renderBankCompare(fin);
}

// ---------------- directories ----------------
function dirSearchRow(id, count) {
  return `<div style="margin:8px 0"><input type="search" id="${id}" placeholder="Search by name, CRD, or MPID…" ` +
    `style="width:min(420px,100%)" aria-label="Search directory"></div>` +
    `<div class="muted" id="${id}-count">${count} firms</div>`;
}

function renderPDView(d) {
  const firms = d?.firms ?? [];
  const rows = firms.map((f, i) =>
    `<tr data-i="${i}"><td>${esc(f.name)}${tagBadges(f.tags)}</td>` +
    `<td>${esc((f.type ?? "").replace(/_/g, " "))}</td>` +
    `<td class="muted">NY Fed dealer stats feed</td></tr>`).join("");
  return `<h3>PRIMARY DEALERS <span class="muted">${firms.length} firms</span></h3>` +
    srcLine("Federal Reserve Bank of New York primary dealer list", d?.as_of, null) +
    `<div class="muted" style="margin-bottom:8px">${esc(d?.note ?? "")}</div>` +
    dirSearchRow("fin-pd-q", firms.length) +
    `<div class="tbl-wrap"><table class="tbl" id="fin-pd-tbl"><thead><tr><th>Firm</th><th>Type</th><th>Positions link</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></div>`;
}

function renderBDView(d) {
  const firms = d?.firms ?? [];
  const rows = firms.map((f, i) =>
    `<tr data-i="${i}"><td>${esc(f.name)}${tagBadges(f.tags)}</td>` +
    `<td class="r">${f.crd ? `<a href="https://brokercheck.finra.org/firm/summary/${esc(String(f.crd))}" target="_blank" rel="noopener">${esc(String(f.crd))}</a>` : "—"}</td>` +
    `<td>${esc((f.type ?? "").replace(/_/g, " "))}</td>` +
    `<td class="r">${f.branches != null ? num0(f.branches) : "—"}</td></tr>`).join("");
  return `<h3>BROKER-DEALERS <span class="muted">${firms.length} firms</span></h3>` +
    srcLine("FINRA BrokerCheck firm search API (free, no key)", d?.as_of, null) +
    `<div class="muted" style="margin-bottom:8px">${esc(d?.note ?? "")}</div>` +
    dirSearchRow("fin-bd-q", firms.length) +
    `<div class="tbl-wrap"><table class="tbl" id="fin-bd-tbl"><thead><tr><th>Firm</th><th class="r">CRD</th><th>Type</th><th class="r">Branches</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></div>`;
}

function renderATSView(d) {
  const firms = d?.firms ?? [];
  const rows = firms.map((f, i) =>
    `<tr data-i="${i}"><td>${esc(f.ats_name)}${tagBadges(f.tags)}</td>` +
    `<td>${esc(f.ats_id)}</td><td>${esc(f.firm_name)}</td>` +
    `<td>${f.has_volume ? `<a href="#/equity/ats">volume</a>` : `<span class="muted">—</span>`}</td></tr>`).join("");
  return `<h3>ALTERNATIVE TRADING SYSTEMS <span class="muted">${firms.length} venues</span></h3>` +
    srcLine("FINRA ATS firms list (TRACE ATS identifiers)", d?.as_of, null) +
    `<div class="muted" style="margin-bottom:8px">${esc(d?.note ?? "")}</div>` +
    dirSearchRow("fin-ats-q", firms.length) +
    `<div class="tbl-wrap"><table class="tbl" id="fin-ats-tbl"><thead><tr><th>ATS name</th><th>ATS ID (MPID)</th><th>Firm</th><th>Dark-pool volume</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></div>`;
}

function renderDepView(d) {
  const firms = d?.firms ?? [];
  const rows = firms.map((f, i) =>
    `<tr data-i="${i}"><td><b>${esc(f.mpid)}</b></td><td>${esc(f.bank_name)}</td>` +
    `<td>${esc(f.treasury_ts ?? "—")}</td><td>${esc(f.agency ?? "—")}</td></tr>`).join("");
  return `<h3>DEPOSITORY INSTITUTIONS <span class="muted">${firms.length} MPIDs</span></h3>` +
    srcLine("FINRA TRACE depository institutions MPID list", d?.as_of, null) +
    `<div class="muted" style="margin-bottom:8px">${esc(d?.note ?? "")}</div>` +
    dirSearchRow("fin-dep-q", firms.length) +
    `<div class="tbl-wrap"><table class="tbl" id="fin-dep-tbl"><thead><tr><th>MPID</th><th>Bank</th><th>Treasury (TS)</th><th>Agency (CA/SP)</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></div>`;
}

function wireSearch(inputId, tableId, firms, keyFn) {
  const inp = document.getElementById(inputId);
  const tbl = document.getElementById(tableId);
  const cnt = document.getElementById(inputId + "-count");
  if (!inp || !tbl) return;
  inp.addEventListener("input", () => {
    const q = inp.value.trim().toLowerCase();
    let n = 0;
    tbl.querySelectorAll("tbody tr").forEach((tr) => {
      const f = firms[Number(tr.dataset.i)];
      const hit = !q || keyFn(f).toLowerCase().includes(q);
      tr.style.display = hit ? "" : "none";
      if (hit) n++;
    });
    if (cnt) cnt.textContent = `${n} of ${firms.length} firms`;
  });
}

// ---------------- main ----------------
const TABS = [
  ["banks", "Banks"],
  ["pd", "Primary Dealers"],
  ["bd", "Broker-Dealers"],
  ["ats", "ATS"],
  ["dep", "Depository Institutions"],
];

export function renderFinance(p) {
  const body = document.querySelector("#panel-finance .panel-body");
  if (!body) return;
  const fin = p.financials ?? null;
  const views = {
    banks: () => renderBanksView(fin),
    pd: () => renderPDView(p.primary_dealers),
    bd: () => renderBDView(p.broker_dealers),
    ats: () => renderATSView(p.ats),
    dep: () => renderDepView(p.depository),
  };
  body.innerHTML =
    `<nav class="sub-row finance-subtabs" role="tablist" aria-label="Finance views">` +
    TABS.map(([id, label]) =>
      `<button type="button" id="fin-tab-${id}" role="tab" aria-selected="${id === state.tab}"` +
      `${id === state.tab ? ` class="active"` : ""}>${esc(label)}</button>`).join("") +
    `</nav><div id="fin-view" role="tabpanel"></div>`;
  const view = document.getElementById("fin-view");
  const setTab = (id) => {
    state.tab = id;
    TABS.forEach(([tid]) => {
      const b = document.getElementById(`fin-tab-${tid}`);
      if (b) {
        b.classList.toggle("active", tid === id);
        b.setAttribute("aria-selected", String(tid === id));
      }
    });
    view.innerHTML = views[id]();
    if (id === "banks" && fin?.companies) {
      renderBankDetail(fin, state.bank).catch(() => {});
      const sel = document.getElementById("fin-bank-sel");
      if (sel) sel.addEventListener("change", () => {
        state.bank = sel.value;
        renderBankDetail(fin, state.bank).catch(() => {});
      });
      const cmp = document.getElementById("fin-compare");
      if (cmp) cmp.querySelectorAll("th.sortable").forEach((th) =>
        th.addEventListener("click", () => {
          const k = th.dataset.k;
          if (state.sort.key === k) state.sort.dir *= -1;
          else state.sort = { key: k, dir: -1 };
          setTab("banks");
        }));
    }
    if (id === "pd") wireSearch("fin-pd-q", "fin-pd-tbl", p.primary_dealers?.firms ?? [],
      (f) => `${f.name} ${f.type ?? ""}`);
    if (id === "bd") wireSearch("fin-bd-q", "fin-bd-tbl", p.broker_dealers?.firms ?? [],
      (f) => `${f.name} ${f.crd ?? ""} ${f.type ?? ""} ${(f.tags ?? []).join(" ")}`);
    if (id === "ats") wireSearch("fin-ats-q", "fin-ats-tbl", p.ats?.firms ?? [],
      (f) => `${f.ats_name} ${f.ats_id} ${f.firm_name} ${(f.tags ?? []).join(" ")}`);
    if (id === "dep") wireSearch("fin-dep-q", "fin-dep-tbl", p.depository?.firms ?? [],
      (f) => `${f.mpid} ${f.bank_name}`);
  };
  TABS.forEach(([id]) => {
    const b = document.getElementById(`fin-tab-${id}`);
    if (b) b.addEventListener("click", () => setTab(id));
  });
  setTab(state.tab);
}
