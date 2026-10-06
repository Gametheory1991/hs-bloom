// ecosystem-map.js — reusable Messari-style ecosystem landscape for universe maps.
// Takes a universe doc's verticals and renders grouped category boxes with
// clickable node chips (styled initials; no logo assets needed).
//
// Usage:
//   import { ecosystemMap } from "../ecosystem-map.js";
//   slot.innerHTML = ecosystemMap(doc, {
//     asOf: doc.as_of, source: "press-reported deals + SEC XBRL",
//     onSelect: (id) => { /* show node detail */ },
//   });
//   slot.querySelectorAll(".eco-chip").forEach(c =>
//     c.addEventListener("click", () => onSelect(c.dataset.node)));
//
// Node chips carry data-node="<id>"; the caller wires clicks. Works on mobile
// (boxes stack vertically via CSS).
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Deterministic pastel color per group label.
const GROUP_HUES = {};
function groupColor(g) {
  if (!(g in GROUP_HUES)) {
    let h = 0;
    for (const ch of String(g)) h = (h * 31 + ch.charCodeAt(0)) % 360;
    GROUP_HUES[g] = h;
  }
  const h = GROUP_HUES[g];
  return `hsl(${h}, 45%, 42%)`;
}

function initials(name) {
  const words = String(name || "?").split(/[\s\-_&]+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return String(name || "?").slice(0, 2).toUpperCase();
}

function chip(n) {
  const color = groupColor(n._group || "Other");
  const tk = n.ticker ? `<span class="eco-tk">${esc(n.ticker)}</span>` : "";
  return `<button class="eco-chip" data-node="${esc(n.id)}" title="${esc(n.name)}${n.ticker ? ` (${esc(n.ticker)})` : ""} — click for detail">` +
    `<span class="eco-av" style="background:${color}">${esc(initials(n.name))}</span>` +
    `<span>${esc(n.name)}</span>${tk}</button>`;
}

export function ecosystemMap(doc, opts = {}) {
  const verticals = doc.verticals || [];
  if (!verticals.length) return `<div class="empty-state">NO DATA — universe has not run yet</div>`;
  const all = verticals.flatMap((v) => (v.companies || []).map((c) => ({ ...c, _v: v.id, _vlabel: v.label, _group: v.group })));
  // Group verticals by macro group (preserve first-seen order).
  const groups = [];
  const gidx = {};
  verticals.forEach((v) => {
    const g = v.group || "Other";
    if (!(g in gidx)) { gidx[g] = groups.length; groups.push({ label: g, verticals: [] }); }
    groups[gidx[g]].verticals.push(v);
  });
  const boxes = groups.map((g) => {
    const verts = g.verticals.map((v) => {
      const list = (v.companies || []).map((c) => ({ ...c, _group: v.group }));
      if (!list.length) return "";
      return `<div class="eco-vert"><h5>${esc(v.label)} <span class="cnt">(${list.length})</span></h5>` +
        `<div class="eco-chips">${list.map(chip).join("")}</div></div>`;
    }).join("");
    return `<div class="eco-group"><h4>${esc(g.label)}</h4><div class="eco-verts">${verts}</div></div>`;
  }).join("");
  const n = all.length;
  const asof = opts.asOf || doc.as_of || "—";
  const src = opts.source || "universe graph job";
  return `<div class="eco-map">${boxes}</div>` +
    `<div class="eco-foot">Data as of ${esc(asof)} · ${n} nodes · Source: ${esc(src)} · click a chip for detail</div>`;
}
