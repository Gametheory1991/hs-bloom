const TABS = ["mkt", "defi", "risk", "econ", "credit", "profit", "pos"];

const currentTab = () => {
  const t = location.hash.replace("#/", "");
  return TABS.includes(t) ? t : "mkt";
};

export function initTabs() {
  const apply = () => {
    const active = currentTab();
    document.querySelectorAll("main[data-tab]").forEach((m) =>
      m.classList.toggle("hidden", m.dataset.tab !== active));
    document.querySelectorAll("[data-tab-link]").forEach((a) => {
      a.classList.toggle("active", a.dataset.tabLink === active);
      if (a.dataset.tabLink === active) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    const link = document.querySelector(`[data-tab-link="${active}"]`);
    const nav = link?.parentElement;
    if (nav && (link.offsetLeft < nav.scrollLeft ||
        link.offsetLeft + link.offsetWidth > nav.scrollLeft + nav.clientWidth)) {
      nav.scrollLeft = link.offsetLeft - (nav.clientWidth - link.offsetWidth) / 2;
    }
  };
  window.addEventListener("hashchange", apply);
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest("input, textarea, select, [contenteditable], [role='dialog']")) return;
    const i = Number(e.key) - 1;
    if (i >= 0 && i < TABS.length) location.hash = `#/${TABS[i]}`;
  });
  // Keep gestures on the tab strip so table scrolling and chart scrubbing stay independent.
  const nav = document.querySelector(".tab-row");
  let touch = null;
  nav.addEventListener("touchstart", (e) => {
    touch = e.touches.length === 1 ? e.touches[0] : null;
  }, { passive: true });
  nav.addEventListener("touchcancel", () => { touch = null; }, { passive: true });
  nav.addEventListener("touchend", (e) => {
    if (!touch || e.touches.length || !e.changedTouches.length) { touch = null; return; }
    const end = e.changedTouches[0];
    const dx = end.clientX - touch.clientX;
    const dy = end.clientY - touch.clientY;
    touch = null;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 2) return;
    const next = TABS.indexOf(currentTab()) + (dx < 0 ? 1 : -1);
    if (next >= 0 && next < TABS.length) location.hash = `#/${TABS[next]}`;
  }, { passive: true });
  apply();
}
