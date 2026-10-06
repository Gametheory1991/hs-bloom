// Shared sortable-table utility for the HSUGAMA DASH terminal.
//
// Any table with a `data-sortable` attribute gets click-to-sort headers:
//   click 1 = high → low, click 2 = low → high, click 3 = back to default order.
// Missing values ("—", "n/a", empty, "building…", …) always sink to the bottom
// regardless of direction.
//
// Tables are auto-wired by a MutationObserver (see initSortableObserver),
// so panels only need to add `data-sortable` to their <table> markup —
// re-renders re-wire automatically.
//
// Per-column overrides:
//   <th data-sort="off">            — not sortable (sparklines, icons, …)
//   <th data-sort-type="num|text|date|rating"> — force a value type
//   <td data-sort-val="123">        — explicit sort value for a cell
//
// Value parsing (auto-detect): numbers with $, commas, %, bps, T/B/M/k
// suffixes, signed deltas, ISO dates, credit ratings (AAA…D, Aaa…C),
// otherwise plain text. Unicode minus (−) is normalized.

const MISSING = /^(—|–|-|n\/a|na|none|no history|building…|stats pending|—)$/i;

const RATING_SCORE = {
  AAA: 100, Aaa: 100,
  "AA+": 95, Aa1: 95, AA: 90, Aa2: 90, "AA-": 85, Aa3: 85,
  "A+": 80, A1: 80, A: 75, A2: 75, "A-": 70, A3: 70,
  "BBB+": 65, Baa1: 65, BBB: 60, Baa2: 60, "BBB-": 55, Baa3: 55,
  "BB+": 50, Ba1: 50, BB: 45, Ba2: 45, "BB-": 40, Ba3: 40,
  "B+": 35, B1: 35, B: 30, B2: 30, "B-": 25, B3: 25,
  "CCC+": 20, Caa1: 20, CCC: 15, Caa2: 15, "CCC-": 10, Caa3: 10,
  CC: 5, Ca: 5, C: 2, D: 0,
};
const RATING_RE = /^(AAA|AA|A|BBB|BB|B|CCC|CC|C|D|Aaa|Aa|A|Baa|Ba|B|Caa|Ca)(\d|[+-])?\b/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?/;
const NUM_SUFFIX = { T: 1e12, B: 1e9, M: 1e6, k: 1e3, K: 1e3 };

function cleanText(t) {
  return (t ?? "").replace(/−/g, "-").replace(/\s+/g, " ").trim();
}

// Returns { v: number|null, t: string } — v is the numeric value when the
// cell parses as a number/date/rating, t is the fallback text.
export function parseSortVal(raw, forceType) {
  let text = cleanText(raw);
  const td = raw && raw.dataset ? raw.dataset.sortVal : undefined;
  if (td != null && td !== "") text = cleanText(td);
  if (!text || MISSING.test(text)) return { v: null, t: "" };

  const type = forceType || "auto";
  if (type === "text") return { v: null, t: text.toLowerCase() };

  // Rating: "Baa2/BBB" → score of the first token. Higher = better quality.
  if (type === "rating" || (type === "auto" && RATING_RE.test(text))) {
    const tok = text.split(/[/\s]/)[0];
    const score = RATING_SCORE[tok];
    if (score != null) return { v: score, t: text.toLowerCase() };
    if (type === "rating") return { v: null, t: "" };
  }

  // Date: ISO prefix.
  if (type === "date" || (type === "auto" && DATE_RE.test(text))) {
    const ms = Date.parse(text.slice(0, 16));
    if (!isNaN(ms)) return { v: ms, t: text.toLowerCase() };
    if (type === "date") return { v: null, t: "" };
  }

  // Number: strip $, commas, %, bp, and magnitude suffixes.
  let t = text;
  let mult = 1;
  const suf = t.match(/([TBMkK])$/);
  if (suf && NUM_SUFFIX[suf[1]]) { mult = NUM_SUFFIX[suf[1]]; t = t.slice(0, -1); }
  t = t.replace(/[$,\s]/g, "").replace(/bps?$/i, "").replace(/%$/, "")
       .replace(/ct$/i, "").replace(/sh$/i, "").replace(/trades?\/d$/i, "");
  // A trailing run of letters that isn't a known unit → not a number.
  if (/[a-zA-Z]$/.test(t)) return { v: null, t: text.toLowerCase() };
  const num = parseFloat(t);
  if (!isNaN(num) && /^[+-]?(\d+\.?\d*|\.\d+)/.test(t)) return { v: num * mult, t: text.toLowerCase() };
  return { v: null, t: text.toLowerCase() };
}

function headerRows(table) {
  const thead = table.querySelector("thead");
  if (thead) return [...thead.rows];
  const rows = [...table.rows];
  const hrs = [];
  for (const r of rows) {
    if ([...r.cells].some((c) => c.tagName === "TH")) hrs.push(r);
    else break;
  }
  return hrs;
}

// Column index of a header cell = sum of colspans of preceding siblings.
function colIndexOf(cell) {
  let idx = 0;
  let sib = cell.previousElementSibling;
  while (sib) { idx += sib.colSpan || 1; sib = sib.previousElementSibling; }
  return idx;
}

// A row that must never move during sorting: group labels, dividers,
// loading/error placeholders. Mark with data-sort-row="off" or class "nosort".
function isFixedRow(r) {
  return r.dataset.sortRow === "off" || r.classList.contains("nosort");
}

function bodyRows(table, nHeaderRows) {
  const tbody = table.querySelector("tbody");
  if (tbody) return [...tbody.rows];
  return [...table.rows].slice(nHeaderRows);
}

function cellAt(row, idx) {
  let acc = 0;
  for (const c of row.cells) {
    const span = c.colSpan || 1;
    if (idx >= acc && idx < acc + span) return c;
    acc += span;
  }
  return null;
}

function clearArrows(table) {
  table.querySelectorAll(".sort-arrow").forEach((a) => a.remove());
  table.querySelectorAll("th[data-scol]").forEach((th) => th.removeAttribute("data-scol"));
}

export function makeTableSortable(table) {
  if (!table || table.dataset.sortWired) return;
  table.dataset.sortWired = "1";
  const hrs = headerRows(table);
  if (!hrs.length) return;
  const brows = bodyRows(table, hrs.length);
  if (!brows.length) return;

  // Collect sortable header cells: THs with colspan 1, not opted out.
  const cols = [];
  for (const hr of hrs) {
    for (const th of hr.cells) {
      if (th.tagName !== "TH") continue;
      if (th.dataset.sort === "off") continue;
      if ((th.colSpan || 1) !== 1) continue; // group header
      const idx = colIndexOf(th);
      if (cols.some((c) => c.idx === idx)) continue;
      cols.push({ th, idx, type: th.dataset.sortType || "auto" });
      th.dataset.scol = "1";
      th.title = (th.title ? th.title + " — " : "") + "Click to sort high→low";
    }
  }
  if (!cols.length) return;

  // Remember default order. Fixed rows (group labels, dividers) are pinned
  // at their original positions; only data rows reorder among the rest.
  const fixedIdx = new Set();
  brows.forEach((r, i) => {
    r.dataset.origIdx = i;
    if (isFixedRow(r)) fixedIdx.add(i);
  });
  const dataRows = brows.filter((r) => !isFixedRow(r));
  let sortIdx = -1, sortDir = 0; // 0 = none, -1 = desc (high→low), 1 = asc

  const applySort = () => {
    clearArrows(table);
    const parent = brows[0].parentNode;
    let ordered;
    if (sortDir === 0) {
      ordered = [...dataRows].sort((a, b) => a.dataset.origIdx - b.dataset.origIdx);
    } else {
      const col = cols.find((c) => c.idx === sortIdx);
      ordered = [...dataRows].sort((a, b) => {
        const va = parseSortVal(cellAt(a, col.idx)?.textContent, col.type);
        const vb = parseSortVal(cellAt(b, col.idx)?.textContent, col.type);
        // Missing values always sink, regardless of direction.
        if (va.v == null && vb.v == null) {
          const t = va.t < vb.t ? -1 : va.t > vb.t ? 1 : 0;
          return t * sortDir;
        }
        if (va.v == null) return 1;
        if (vb.v == null) return -1;
        if (va.v !== vb.v) return (va.v - vb.v) * sortDir;
        const t = va.t < vb.t ? -1 : va.t > vb.t ? 1 : 0;
        return t * sortDir;
      });
      const arrow = document.createElement("span");
      arrow.className = "sort-arrow";
      arrow.textContent = sortDir === -1 ? " ▼" : " ▲";
      col.th.appendChild(arrow);
      col.th.dataset.scol = sortDir === -1 ? "desc" : "asc";
    }
    // Rebuild: fixed rows stay pinned at their original slots; data rows
    // fill the remaining slots in sorted order.
    let di = 0;
    const final = [];
    for (let i = 0; i < brows.length; i++) {
      final.push(fixedIdx.has(i) ? brows[i] : ordered[di++]);
    }
    for (const r of final) parent.appendChild(r);
  };

  for (const col of cols) {
    col.th.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (sortIdx === col.idx) {
        sortDir = sortDir === -1 ? 1 : sortDir === 1 ? 0 : -1;
      } else {
        sortIdx = col.idx;
        sortDir = -1; // first click: high → low
      }
      applySort();
    });
  }
}

export function wireSortableTables(root) {
  const scope = root || document;
  scope.querySelectorAll("table[data-sortable]:not([data-sort-wired])").forEach(makeTableSortable);
}

// Auto-wire every data-sortable table as it enters the DOM (covers
// re-renders). Idempotent — call once at boot.
export function initSortableObserver() {
  const wire = (node) => {
    if (node.nodeType !== 1) return;
    if (node.matches && node.matches("table[data-sortable]")) makeTableSortable(node);
    if (node.querySelectorAll) node.querySelectorAll("table[data-sortable]:not([data-sort-wired])").forEach(makeTableSortable);
  };
  wire(document);
  new MutationObserver((muts) => {
    for (const m of muts) m.addedNodes.forEach(wire);
  }).observe(document.body, { childList: true, subtree: true });
}
