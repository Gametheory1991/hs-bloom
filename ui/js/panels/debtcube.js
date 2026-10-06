// DEBT OUTSTANDING CUBE — product x maturity x holder slices of MSPD
// Table III (CUSIP outstanding) + NY Fed SOMA CUSIP holdings. Computed by
// collector/debt_cube.py (compute-only, no network) from the mspd_cusips /
// soma_cusips / mspd_table1 docs; refreshed daily by the debt_cube job.
// Fed by /api/debt-cube.
//
// Mounted on the QUANT tab below the config-driven Z.1 sections (the cycle
// engine can't express the three dropdown dimensions). Preserved across
// cycle re-renders via data-batch11 (see panels/cycle.js). Dropdown state
// lives in the DOM, so it survives the 60s tick — we only re-fetch and
// repaint, never rebuild the shell.
import { fmtAge, isStale } from "../fmt.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const num = (x, d = 1) =>
  x == null ? "—" : Number(x).toLocaleString("en-US",
    { minimumFractionDigits: d, maximumFractionDigits: d });

const PRODUCTS = ["bills", "notes", "bonds", "tips", "frns"];
const MATURITIES = ["<1Y", "1-3Y", "3-5Y", "5-10Y", "10-20Y", "20Y+"];
const HOLDERS = ["soma", "public", "nonmarketable"];
const HOLDER_LABEL = { soma: "SOMA (Fed)", public: "Public (ex-SOMA)", nonmarketable: "Non-marketable" };
const STALE_MIN = 2880; // 2x the daily cube cadence

let _cells = [], _asof = null, _denom = null, _updated = null;

async function getDebtCube() {
  const resp = await fetch("/api/debt-cube");
  if (!resp.ok) throw new Error(`debt-cube: HTTP ${resp.status}`);
  return resp.json();
}

function shellHTML() {
  const opts = (vals, labelOf) =>
    `<option value="all">All</option>` + vals.map((v) =>
      `<option value="${esc(v)}">${esc(labelOf ? labelOf(v) : v.toUpperCase())}</option>`).join("");
  return `<section class="panel"><div class="panel-title">DEBT OUTSTANDING — PRODUCT × MATURITY × HOLDER (MSPD + SOMA)</div>` +
    `<div class="panel-body">` +
    `<div class="trace-controls">` +
      `<label>Product <select data-dc="product">${opts(PRODUCTS)}</select></label>` +
      `<label>Maturity <select data-dc="maturity">${opts(MATURITIES, (v) => v)}</select></label>` +
      `<label>Holder <select data-dc="holder">${opts(HOLDERS, (v) => HOLDER_LABEL[v])}</select></label>` +
    `</div>` +
    `<table data-sortable><thead><tr><th>Product</th><th>Maturity</th><th>Holder</th>` +
      `<th class="num">Notional $bn</th><th class="num">% of total</th></tr></thead>` +
      `<tbody data-dc-rows></tbody></table>` +
    `<p class="muted" data-dc-note></p>` +
    `</div><div class="panel-foot muted" data-dc-foot></div></section>`;
}

function filtersOf(wrap) {
  const val = (k) => wrap.querySelector(`select[data-dc="${k}"]`).value;
  const v = val("product"), m = val("maturity"), h = val("holder");
  return { product: v === "all" ? null : v, maturity: m === "all" ? null : m, holder: h === "all" ? null : h };
}

function paint(wrap) {
  const f = filtersOf(wrap);
  const rows = _cells.filter((c) =>
    (!f.product || c.product === f.product) &&
    (!f.maturity || c.maturity === f.maturity) &&
    (!f.holder || c.holder === f.holder));
  const tb = wrap.querySelector("[data-dc-rows]");
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="5" class="muted">No cells for this slice — cube builds on the next scheduler run.</td></tr>`;
  } else {
    tb.innerHTML = rows.map((c) =>
      `<tr><td class="sym">${esc(c.product.toUpperCase())}</td>` +
      `<td>${esc(c.maturity)}</td>` +
      `<td>${esc(HOLDER_LABEL[c.holder] ?? c.holder)}</td>` +
      `<td class="num">${num(c.notional_bn, 1)}</td>` +
      `<td class="num">${num(c.pct_of_total, 2)}</td></tr>`).join("");
  }
  const totBn = rows.reduce((s, c) => s + (c.notional_bn ?? 0), 0);
  const totPct = rows.reduce((s, c) => s + (c.pct_of_total ?? 0), 0);
  const note = wrap.querySelector("[data-dc-note]");
  note.textContent = rows.length
    ? `${rows.length} cells · slice total $${num(totBn, 1)}B (${num(totPct, 2)}% of total)` +
      (_denom ? ` · total public debt $${num(_denom.notional_bn, 1)}B @ ${_asof ?? "—"}` : "")
    : "";
  const foot = wrap.querySelector("[data-dc-foot]");
  foot.textContent = `DATA: MSPD TABLES 1/3 · NY FED SOMA · ${fmtAge(_updated)}`;
  foot.classList.toggle("stale", isStale(_updated, STALE_MIN));
}

export async function renderDebtCube() {
  const root = document.getElementById("cycle-quant");
  if (!root) return;
  let wrap = root.querySelector("[data-debtcube]");
  if (!wrap) {
    wrap = document.createElement("div");
    wrap.setAttribute("data-batch11", "debtcube"); // preserved by renderCycle re-renders
    wrap.setAttribute("data-debtcube", "");
    wrap.innerHTML = shellHTML();
    root.appendChild(wrap);
    wrap.querySelectorAll("select[data-dc]").forEach((s) =>
      s.addEventListener("change", () => paint(wrap)));
  }
  try {
    const d = await getDebtCube();
    _cells = d.cells ?? [];
    _asof = d.asof ?? null;
    _denom = d.denominator ?? null;
    _updated = d.updated_at ?? null;
  } catch { /* keep stale cells; table still renders */ }
  paint(wrap);
}
