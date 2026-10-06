// Hub + subtab navigation — Phase 1 IA (2026-10-05).
// 19 legacy tabs -> 6 hubs. Hash format: #/hub/sub (e.g. #/markets/equities).
// Legacy single-segment hashes (#/mkt, #/finra, ...) redirect to their hub home,
// so old bookmarks, palette entries and external links keep working.

export const HUBS = [
  { id: "pulse", label: "PULSE",
    subs: [["snapshot", "Snapshot"], ["talktrack", "Talk Track"], ["deck", "Deck Mode"]] },
  { id: "macro", label: "MACRO",
    subs: [["calendar", "Calendar"], ["central", "Central Banks"], ["auctions", "UST Auctions"],
           ["bonds", "World Bonds"], ["credit", "Credit"], ["cycle", "Cycle"]] },
  { id: "markets", label: "MARKETS",
    subs: [["equities", "Equities"], ["volcorr", "Vol & Corr"], ["futures", "Futures"],
           ["etfs", "ETFs"], ["scorecard", "Scorecard"], ["digital", "Digital"]] },
  { id: "positioning", label: "POSITIONING",
    subs: [["positions", "Positions"], ["flows", "13F Flows"], ["shorts", "Short Interest"],
           ["predict", "Predict"]] },
  { id: "structure", label: "STRUCTURE",
    subs: [["trace", "TRACE Volume"], ["maps", "Maps"], ["desks", "Desks"]] },
  { id: "desk", label: "DESK",
    subs: [["alerts", "Alerts"], ["briefcheck", "Brief Check"], ["analyst", "Analyst"]] },
];

// Legacy tab id -> "hub/sub" redirect target.
const LEGACY = {
  mkt: "pulse/snapshot", defi: "markets/digital", risk: "positioning/positions",
  econ: "macro/cycle", credit: "macro/credit", profit: "macro/cycle",
  pos: "positioning/positions", map: "structure/maps", xcorr: "markets/volcorr",
  vol: "markets/volcorr", movers: "markets/equities", hyper: "structure/desks",
  futures: "markets/futures", flows: "positioning/flows", scorecard: "markets/scorecard",
  etf: "markets/etfs", central: "macro/central", predict: "positioning/predict",
  finra: "structure/trace",
};

const hubById = (id) => HUBS.find((h) => h.id === id);

export function currentRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  let hub = hubById(parts[0]);
  if (!hub && LEGACY[parts[0]]) {
    // Legacy hash: bounce to the new home without adding a history entry.
    location.replace(`#/${LEGACY[parts[0]]}`);
    return currentRoute();
  }
  if (!hub) hub = hubById("pulse");
  let sub = parts[1];
  if (!hub.subs.some(([sid]) => sid === sub)) sub = hub.subs[0][0];
  return { hub: hub.id, sub };
}

function renderSubs(hubId, activeSub) {
  const hub = hubById(hubId);
  const bar = document.getElementById("sub-row");
  bar.innerHTML = hub.subs
    .map(([sid, label]) =>
      `<button type="button" data-sub-link="${sid}" class="${sid === activeSub ? "active" : ""}">${label}</button>`)
    .join("");
  bar.querySelectorAll("[data-sub-link]").forEach((b) =>
    b.addEventListener("click", () => {
      const target = `#/${hubId}/${b.dataset.subLink}`;
      if (location.hash !== target) location.hash = target;
    }));
}

export function initTabs() {
  const apply = () => {
    const { hub, sub } = currentRoute();
    document.querySelectorAll("main[data-hub]").forEach((m) =>
      m.classList.toggle("hidden", m.dataset.hub !== hub || m.dataset.sub !== sub));
    document.querySelectorAll("[data-hub-link]").forEach((b) =>
      b.classList.toggle("active", b.dataset.hubLink === hub));
    renderSubs(hub, sub);
  };
  window.addEventListener("hashchange", apply);
  // Hub buttons: click switches to that hub's default sub.
  document.querySelectorAll("[data-hub-link]").forEach((b) =>
    b.addEventListener("click", () => {
      const target = `#/${b.dataset.hubLink}`;
      if (location.hash !== target) location.hash = target;
      else apply(); // already there — re-apply in case content is stale
    }));
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = document.activeElement?.tagName ?? "";
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) return; // don't hijack typing
    const i = Number(e.key) - 1;
    if (i >= 0 && i < HUBS.length) location.hash = `#/${HUBS[i].id}`;
  });
  // DESK / Analyst launcher: opens the chat drawer (wired in chat.js).
  document.addEventListener("click", (e) => {
    if (e.target?.id === "analyst-open") document.getElementById("chat-toggle")?.click();
  });
  apply();
}
