// Shared data & chart export for the HSUGAMA DASH terminal.
//
// What it does:
//   * Every <h3> section header that is followed by a table gets a ⤓ button
//     offering CSV and XLSX download of that table (visible columns, current
//     sort order, flattened multi-row headers).
//   * Every <h3> followed by a chart (uPlot canvas or standalone SVG) gets
//     PNG / JPG export — white background, title + as-of burned in.
//   * Every panel title gets an "⤓ ALL" button bundling all of that panel's
//     tables into one multi-sheet XLSX.
//   * The chart modal (#chart-overlay) gets PNG / JPG buttons.
//
// Sections are auto-wired by a MutationObserver (same pattern as
// sortable.js), so panels need no code changes — re-renders re-wire.
//
// Pure helpers (slug, csvEscape, matrixToCSV, flattenHeaderMatrix) are
// DOM-free and unit-testable in node.

export function slug(s) {
  return (s ?? "export")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "export";
}

export function todayStamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function csvEscape(v) {
  const s = v == null ? "" : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function matrixToCSV(headers, rows) {
  const lines = [headers.map(csvEscape).join(",")];
  for (const r of rows) lines.push(r.map(csvEscape).join(","));
  return lines.join("\r\n") + "\r\n";
}

// Flatten a header matrix (array of rows, each an array of {text, colspan})
// into one header per column, joining stacked labels with " / ".
export function flattenHeaderMatrix(headerRows) {
  const nCols = Math.max(0, ...headerRows.map((r) => r.reduce((a, c) => a + (c.colspan || 1), 0)));
  const grid = headerRows.map(() => new Array(nCols).fill(""));
  headerRows.forEach((cells, ri) => {
    let ci = 0;
    for (const c of cells) {
      while (ci < nCols && grid[ri][ci] !== "") ci++;
      const span = c.colspan || 1;
      for (let k = 0; k < span && ci + k < nCols; k++) {
        if (grid[ri][ci + k] === "") grid[ri][ci + k] = c.text;
      }
      ci += span;
    }
  });
  const out = [];
  for (let ci = 0; ci < nCols; ci++) {
    const parts = [];
    for (let ri = 0; ri < headerRows.length; ri++) {
      const t = (grid[ri][ci] || "").trim();
      if (t && !parts.includes(t)) parts.push(t);
    }
    out.push(parts.join(" / "));
  }
  return out;
}

function cellText(cell) {
  const clone = cell.cloneNode(true);
  clone.querySelectorAll(".sort-arrow").forEach((a) => a.remove());
  return clone.textContent.replace(/\s+/g, " ").trim();
}

// ---- DOM-dependent table extraction ---------------------------------------

function headerRowsOf(table) {
  const thead = table.querySelector("thead");
  const rows = thead ? [...thead.rows] : [...table.rows].filter((r) => [...r.cells].some((c) => c.tagName === "TH"));
  return rows.map((r) => [...r.cells].filter((c) => c.tagName === "TH").map((c) => ({ text: cellText(c), colspan: c.colSpan || 1 })));
}

function bodyRowsOf(table) {
  const tbody = table.querySelector("tbody");
  if (tbody) return [...tbody.rows];
  const hrs = new Set([...table.rows].filter((r) => [...r.cells].some((c) => c.tagName === "TH")));
  return [...table.rows].filter((r) => !hrs.has(r));
}

export function tableToMatrix(table) {
  const headers = flattenHeaderMatrix(headerRowsOf(table));
  const nCols = headers.length;
  const rows = bodyRowsOf(table).map((tr) => {
    const cells = [...tr.cells];
    const out = [];
    let ci = 0;
    for (const c of cells) {
      const span = c.colSpan || 1;
      const t = cellText(c);
      for (let k = 0; k < span && out.length < nCols; k++) out.push(k === 0 ? t : "");
      ci += span;
    }
    while (out.length < nCols) out.push("");
    return out.slice(0, nCols);
  });
  return { headers, rows };
}

function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 4000);
}

function sectionTitle(h3) {
  const clone = h3.cloneNode(true);
  clone.querySelectorAll(".exp-wrap").forEach((e) => e.remove());
  return clone.textContent.replace(/\s+/g, " ").trim();
}

function metaFor(h3) {
  const meta = { title: sectionTitle(h3), asof: "", notes: [] };
  const muted = h3.querySelector(".muted");
  if (muted) meta.asof = muted.textContent.replace(/\s+/g, " ").trim();
  // Footnote paragraphs that follow the table in the same section.
  let el = h3.nextElementSibling;
  let hops = 0;
  while (el && hops < 6 && !/^H[23]$/.test(el.tagName)) {
    if (el.tagName === "P" && el.classList.contains("muted") && el.textContent.trim().length > 20)
      meta.notes.push(el.textContent.replace(/\s+/g, " ").trim());
    el = el.nextElementSibling;
    hops++;
  }
  return meta;
}

function tabSlug() {
  return slug((location.hash || "#/mkt").replace("#/", "")) || "mkt";
}

export function exportTableCSV(table, baseName, meta) {
  const { headers, rows } = tableToMatrix(table);
  const pre = [
    `# HSUGAMA DASH export — ${meta?.title ?? baseName}`,
    `# Exported: ${new Date().toISOString()}`,
  ];
  if (meta?.asof) pre.push(`# As of: ${meta.asof}`);
  for (const n of meta?.notes ?? []) pre.push(`# Note: ${n}`);
  pre.push("");
  const csv = pre.join("\r\n") + "\r\n" + matrixToCSV(headers, rows);
  downloadBlob(`${baseName}.csv`, new Blob([csv], { type: "text/csv;charset=utf-8" }));
}

function xlsxAvailable() {
  return typeof window !== "undefined" && window.XLSX && window.XLSX.utils;
}

function aoaSheet(headers, rows, meta) {
  const aoa = [];
  aoa.push([`HSUGAMA DASH — ${meta?.title ?? ""}`]);
  aoa.push([`Exported ${new Date().toISOString()}${meta?.asof ? ` · ${meta.asof}` : ""}`]);
  for (const n of meta?.notes ?? []) aoa.push([`Note: ${n}`]);
  aoa.push([]);
  aoa.push(headers);
  for (const r of rows) aoa.push(r);
  const ws = window.XLSX.utils.aoa_to_sheet(aoa);
  ws["!cols"] = headers.map((h, i) => {
    const w = Math.max(10, Math.min(42, h.length + 2,
      ...rows.slice(0, 200).map((r) => String(r[i] ?? "").length + 2)));
    return { wch: w };
  });
  return ws;
}

function aboutSheet() {
  return window.XLSX.utils.aoa_to_sheet([
    ["HSUGAMA DASH — data export"],
    ["Exported", new Date().toISOString()],
    ["Dashboard", location.origin + location.pathname],
    ["", ""],
    ["Values are as displayed in the terminal (formatted text)."],
    ["Missing values render as — . Sort order matches the on-screen table."],
  ]);
}

// sheets: [{ name, table }] — meta taken from each table's h3 when available.
export function exportTablesXLSX(sheets, baseName) {
  if (!xlsxAvailable()) {
    alert("XLSX library not loaded — try the CSV export instead.");
    return;
  }
  const wb = window.XLSX.utils.book_new();
  for (const { name, table, meta } of sheets) {
    const { headers, rows } = tableToMatrix(table);
    const ws = aoaSheet(headers, rows, meta);
    let sname = slug(name).slice(0, 28) || "sheet";
    let uniq = sname, i = 2;
    while (wb.SheetNames.includes(uniq)) uniq = `${sname}-${i++}`;
    window.XLSX.utils.book_append_sheet(wb, ws, uniq);
  }
  window.XLSX.utils.book_append_sheet(wb, aboutSheet(), "about");
  window.XLSX.writeFile(wb, `${baseName}.xlsx`);
}

// ---- chart image export ----------------------------------------------------

function findChartSource(section) {
  // Prefer a uPlot canvas; fall back to a standalone (non-sparkline) SVG.
  const canvas = section.querySelector("canvas");
  if (canvas && canvas.width > 50 && canvas.height > 50) return { kind: "canvas", el: canvas };
  const svg = [...section.querySelectorAll("svg")].find(
    (s) => !s.closest("table") && (s.getBoundingClientRect().width || 0) > 120);
  if (svg) return { kind: "svg", el: svg };
  return null;
}

async function svgToCanvas(svg) {
  const rect = svg.getBoundingClientRect();
  const scale = 2;
  const clone = svg.cloneNode(true);
  const w = Math.max(300, Math.round(rect.width || 800));
  const h = Math.max(150, Math.round(rect.height || 400));
  clone.setAttribute("width", String(w * scale));
  clone.setAttribute("height", String(h * scale));
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const xml = new XMLSerializer().serializeToString(clone);
  const url = URL.createObjectURL(new Blob([xml], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
    const c = document.createElement("canvas");
    c.width = w * scale; c.height = h * scale;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    return c;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function composeLabeled(srcCanvas, title, sub) {
  const pad = 18, headH = 64;
  const out = document.createElement("canvas");
  out.width = srcCanvas.width;
  out.height = srcCanvas.height + headH;
  const ctx = out.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, out.width, out.height);
  const k = out.width / 900; // scale type with image width
  ctx.fillStyle = "#1a1a1a";
  ctx.font = `600 ${Math.round(22 * k)}px system-ui, -apple-system, sans-serif`;
  ctx.fillText(title.slice(0, 90), pad, pad + Math.round(24 * k));
  ctx.fillStyle = "#6b7280";
  ctx.font = `${Math.round(14 * k)}px system-ui, -apple-system, sans-serif`;
  ctx.fillText(`HSUGAMA DASH · ${sub}`.slice(0, 120), pad, pad + Math.round(46 * k));
  ctx.drawImage(srcCanvas, 0, headH);
  return out;
}

function findChartSourceInParts(parts) {
  for (const p of parts) {
    const cands = p.tagName === "CANVAS" ? [p] : [...(p.querySelectorAll ? p.querySelectorAll("canvas") : [])];
    const cv = cands.find((c) => c.width > 50 && c.height > 50);
    if (cv) return { kind: "canvas", el: cv };
  }
  for (const p of parts) {
    const svgs = p.tagName === "SVG" ? [p] : [...(p.querySelectorAll ? p.querySelectorAll("svg") : [])];
    const sv = svgs.find((s) => !s.closest("table") && (s.getBoundingClientRect().width || 0) > 120);
    if (sv) return { kind: "svg", el: sv };
  }
  return null;
}

export async function exportChartImage(section, format, meta) {
  const src = findChartSource(section);
  if (!src) { alert("No chart found in this section."); return; }
  await exportChartSrc(src, format, meta);
}

export async function exportChartParts(parts, format, meta) {
  const src = findChartSourceInParts(parts);
  if (!src) { alert("No chart found in this section."); return; }
  await exportChartSrc(src, format, meta);
}

async function exportChartSrc(src, format, meta) {
  try {
    const base = src.kind === "canvas" ? src.el : await svgToCanvas(src.el);
    const labeled = composeLabeled(base, meta?.title ?? "chart", meta?.asof || todayStamp());
    const mime = format === "jpg" ? "image/jpeg" : "image/png";
    const dataUrl = labeled.toDataURL(mime, 0.92);
    const bin = atob(dataUrl.split(",")[1]);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    downloadBlob(`${slug(tabSlug() + "-" + (meta?.title ?? "chart"))}-${todayStamp()}.${format}`,
      new Blob([bytes], { type: mime }));
  } catch (err) {
    alert(`Chart export failed — ${err.message}`);
  }
}

// ---- auto-wiring ------------------------------------------------------------

// Live sibling elements belonging to an h3's section (up to next h3/h2).
function liveParts(h3) {
  const parts = [];
  let el = h3.nextElementSibling;
  let hops = 0;
  while (el && hops < 12 && !/^H[23]$/.test(el.tagName)) { parts.push(el); el = el.nextElementSibling; hops++; }
  return parts;
}

function firstInParts(parts, selector) {
  for (const p of parts) {
    if (p.matches && p.matches(selector)) return p;
    const hit = p.querySelector ? p.querySelector(selector) : null;
    if (hit) return hit;
  }
  return null;
}

function sectionScope(h3) {
  // Cloned fragment for safe text inspection.
  const frag = document.createElement("div");
  for (const p of liveParts(h3)) frag.appendChild(p.cloneNode(true));
  return { parts: liveParts(h3), frag };
}

function makeMenu(options) {
  const menu = document.createElement("span");
  menu.className = "exp-menu hidden";
  for (const { label, fn } of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "exp-opt";
    b.textContent = label;
    b.addEventListener("click", (ev) => { ev.stopPropagation(); menu.classList.add("hidden"); fn(); });
    menu.appendChild(b);
  }
  return menu;
}

function wireSection(h3) {
  if (h3.dataset.expWired) return;
  h3.dataset.expWired = "1";
  const parts = liveParts(h3);
  const realTable = firstInParts(parts, "table");
  // Detect charts on LIVE nodes (clones lose canvas bitmaps and SVG layout).
  const hasChart = !!findChartSourceInParts(parts);
  if (!realTable && !hasChart) return;

  const meta = metaFor(h3);
  const base = `${tabSlug()}-${slug(meta.title)}-${todayStamp()}`;
  const options = [];
  if (realTable) {
    options.push({ label: "CSV", fn: () => exportTableCSV(realTable, base, meta) });
    if (xlsxAvailable()) options.push({ label: "XLSX", fn: () => exportTablesXLSX([{ name: meta.title, table: realTable, meta }], base) });
  }
  if (hasChart) {
    // Query the LIVE section at click time (clones lose canvas bitmaps).
    options.push({ label: "PNG", fn: () => exportChartParts(liveParts(h3), "png", meta) });
    options.push({ label: "JPG", fn: () => exportChartParts(liveParts(h3), "jpg", meta) });
  }
  if (!options.length) return;

  const wrap = document.createElement("span");
  wrap.className = "exp-wrap";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "exp-btn";
  btn.textContent = "⤓";
  btn.title = "Export this section (CSV / XLSX / PNG / JPG)";
  const menu = makeMenu(options);
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    document.querySelectorAll(".exp-menu").forEach((m) => { if (m !== menu) m.classList.add("hidden"); });
    menu.classList.toggle("hidden");
  });
  wrap.appendChild(btn);
  wrap.appendChild(menu);
  h3.appendChild(wrap);
}

function wirePanelAll(panel) {
  if (panel.dataset.expAllWired) return;
  panel.dataset.expAllWired = "1";
  const title = panel.querySelector(".panel-title");
  const body = panel.querySelector(".panel-body");
  if (!title || !body) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "exp-all-btn";
  btn.textContent = "⤓ ALL";
  btn.title = "Export every table in this panel to one multi-sheet XLSX";
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const sheets = [];
    const seen = new Set();
    for (const h3 of body.querySelectorAll("h3")) {
      const table = firstInParts(liveParts(h3), "table");
      if (table && !seen.has(table)) {
        seen.add(table);
        sheets.push({ name: sectionTitle(h3), table, meta: metaFor(h3) });
      }
    }
    if (!sheets.length) { alert("No tables found in this panel."); return; }
    exportTablesXLSX(sheets, `${tabSlug()}-panel-${todayStamp()}`);
  });
  title.appendChild(btn);
}

function wireOverlay() {
  const head = document.querySelector("#chart-overlay .overlay-head");
  if (!head || head.dataset.expWired) return;
  head.dataset.expWired = "1";
  const mk = (label, format) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "exp-btn";
    b.textContent = label;
    b.title = `Export chart as ${format.toUpperCase()}`;
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const title = document.getElementById("chart-title")?.textContent ?? "chart";
      exportChartImage(document.getElementById("chart-overlay"), format.toLowerCase(),
        { title, asof: todayStamp() });
    });
    return b;
  };
  const closeBtn = head.querySelector("#chart-close");
  head.insertBefore(mk("PNG", "PNG"), closeBtn);
  head.insertBefore(mk("JPG", "JPG"), closeBtn);
}

function wireAll(root) {
  const scope = root && root.querySelectorAll ? root : document;
  if (scope === document || scope === document.body) {
    document.querySelectorAll("section.panel").forEach(wirePanelAll);
    wireOverlay();
  } else if (scope.matches && scope.matches("section.panel")) {
    wirePanelAll(scope);
  }
  const h3s = scope.querySelectorAll ? scope.querySelectorAll("h3") : [];
  h3s.forEach(wireSection);
  if (scope.tagName === "H3") wireSection(scope);
}

export function initExportObserver() {
  const handle = (node) => {
    if (!node || node.nodeType !== 1) return;
    if (node.matches("section.panel")) wireAll(node);
    else if (node.matches("h3")) wireSection(node);
    else if (node.querySelectorAll) {
      node.querySelectorAll("section.panel").forEach(wirePanelAll);
      node.querySelectorAll("h3:not([data-exp-wired])").forEach(wireSection);
    }
    if (node.querySelector && node.querySelector("#chart-overlay")) wireOverlay();
  };
  handle(document);
  new MutationObserver((muts) => {
    for (const m of muts) m.addedNodes.forEach(handle);
  }).observe(document.body, { childList: true, subtree: true });
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest(".exp-wrap")) document.querySelectorAll(".exp-menu").forEach((m) => m.classList.add("hidden"));
  });
}
