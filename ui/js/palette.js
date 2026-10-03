// Command palette: Cmd+K / Ctrl+K (or "/" when not typing) opens a
// fuzzy-search overlay across tabs, panels, and series. Enter navigates,
// up/down moves, Esc closes. The entry index is supplied by main.js via
// updateIndex() — this module never hardcodes the series list.
let entries = [];   // [{label, sub, hash}]
let open = false;
let sel = 0;
let root = null;

function fuzzyScore(q, text) {
  q = q.toLowerCase(); text = text.toLowerCase();
  let qi = 0, score = 0, run = 0;
  for (let ti = 0; ti < text.length && qi < q.length; ti++) {
    if (text[ti] === q[qi]) {
      qi++; run++;
      score += 2 + run;                       // consecutive bonus
      if (ti === 0 || /[\s\-_/]/.test(text[ti - 1])) score += 4;  // word-start bonus
    } else { run = 0; }
  }
  if (qi < q.length) return -1;               // not a subsequence → no match
  return score - text.length * 0.05;          // shorter labels win ties
}

function renderList(q) {
  const box = root.querySelector(".palette-results");
  const scored = [];
  for (const e of entries) {
    const s = q ? fuzzyScore(q, e.label + " " + (e.sub ?? "")) : 1;
    if (s >= 0) scored.push([s, e]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  const top = scored.slice(0, 12).map(([, e]) => e);
  sel = Math.min(sel, Math.max(0, top.length - 1));
  box.innerHTML = top.length
    ? top.map((e, i) => `<div class="palette-item${i === sel ? " sel" : ""}" data-i="${i}">
        <span class="palette-label">${e.label}</span>
        <span class="palette-sub muted">${e.sub ?? ""}</span></div>`).join("")
    : `<div class="muted" style="padding:8px">no matches</div>`;
  box.querySelectorAll(".palette-item").forEach((el) => {
    el.addEventListener("click", () => go(top[Number(el.dataset.i)]));
  });
  root._top = top;
}

function go(e) {
  if (!e) return;
  close();
  if (e.hash && location.hash !== e.hash) location.hash = e.hash;
}

function close() {
  open = false;
  root.classList.add("hidden");
}

function toggle() {
  open ? close() : (open = true, sel = 0, root.classList.remove("hidden"),
    root.querySelector("input").value = "", renderList(""),
    root.querySelector("input").focus());
}

export function updateIndex(list) { entries = list; }

export function initPalette() {
  root = document.createElement("div");
  root.id = "palette";
  root.className = "overlay hidden";
  root.innerHTML = `<div class="overlay-box palette-box">
      <input class="palette-input" placeholder="jump to tab, panel, series…  (esc to close)" autocomplete="off">
      <div class="palette-results"></div></div>`;
  document.body.appendChild(root);
  const input = root.querySelector("input");
  input.addEventListener("input", () => { sel = 0; renderList(input.value.trim()); });
  input.addEventListener("keydown", (e) => {
    const top = root._top ?? [];
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, top.length - 1); renderList(input.value.trim()); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); renderList(input.value.trim()); }
    else if (e.key === "Enter") { go(top[sel]); }
    else if (e.key === "Escape") { close(); }
  });
  root.addEventListener("click", (e) => { if (e.target === root) close(); });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); toggle(); return; }
    if (e.key === "Escape" && open) { close(); return; }
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const t = document.activeElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      e.preventDefault(); toggle();
    }
  });
}
