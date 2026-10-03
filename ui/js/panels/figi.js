import { fmtAge } from "../fmt.js";

// Third-party names come from OpenFIGI — escape before innerHTML.
const esc = (s) => String(s).replace(/[&<>\"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const BASE = window.OSBLOOM_API ?? "";
const ID_TYPES = ["TICKER", "CUSIP", "ISIN", "SEDOL", "FIGI"];

// Interactive FIGI lookup (batch 11): appended to the STRUCT tab next to the
// TRACE panels. Proxies OpenFIGI v3 mapping via /api/figi/lookup; when no
// OPENFIGI_API_KEY is configured the endpoint returns ok:false and we show
// the setup hint instead of a table.
export function renderFigiLookup() {
  const root = document.getElementById("cycle-struct");
  if (!root || document.getElementById("figi-lookup")) return;
  const wrap = document.createElement("div");
  wrap.setAttribute("data-batch11", "figi");
  wrap.innerHTML = `
    <section class="panel" id="figi-lookup">
      <div class="panel-title">FIGI LOOKUP — OPENFIGI</div>
      <div class="panel-body">
        <div class="figi-form">
          <select id="figi-idtype" aria-label="ID type">
            ${ID_TYPES.map((t) => `<option value="${t}"${t === "TICKER" ? " selected" : ""}>${t}</option>`).join("")}
          </select>
          <input id="figi-idvalue" type="text" placeholder="AAPL / 037833100 / US0378331005…" autocomplete="off" spellcheck="false" />
          <button id="figi-go" type="button">LOOKUP</button>
        </div>
        <div id="figi-result" class="muted" style="margin-top:6px">Enter an identifier above.</div>
      </div>
      <div class="panel-foot muted">DATA: OPENFIGI · ON DEMAND</div>
    </section>`;
  root.appendChild(wrap);

  const input = wrap.querySelector("#figi-idvalue");
  const go = async () => {
    const idtype = wrap.querySelector("#figi-idtype").value;
    const idvalue = input.value.trim();
    const out = wrap.querySelector("#figi-result");
    if (!idvalue) { out.innerHTML = `<span class="muted">Enter an identifier first.</span>`; return; }
    out.innerHTML = `<span class="muted">Looking up…</span>`;
    try {
      const resp = await fetch(`${BASE}/api/figi/lookup?idtype=${encodeURIComponent(idtype)}&idvalue=${encodeURIComponent(idvalue)}`);
      const data = await resp.json();
      if (!data.ok) {
        out.innerHTML = `<span class="warn">${esc(data.message ?? "Lookup failed.")}</span>`;
        return;
      }
      if (!data.results.length) {
        out.innerHTML = `<span class="muted">No match for ${esc(idvalue)}.</span>`;
        return;
      }
      const cell = (x) => esc(x ?? "—");
      out.innerHTML = `<table class="figi-table">
        <tr><th>Name</th><th>Ticker</th><th>FIGI</th><th>Comp. FIGI</th><th>Type</th><th>Exch</th></tr>
        ${data.results.map((r) => `<tr>
          <td class="sym">${cell(r.name)}</td><td>${cell(r.ticker)}</td>
          <td class="mono">${cell(r.figi)}</td><td class="mono">${cell(r.composite_figi)}</td>
          <td>${cell(r.security_type)}</td><td>${cell(r.exchange_code)}</td>
        </tr>`).join("")}</table>`;
    } catch (e) {
      out.innerHTML = `<span class="warn">Request failed: ${esc(e.message)}</span>`;
    }
  };
  wrap.querySelector("#figi-go").addEventListener("click", go);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
}
