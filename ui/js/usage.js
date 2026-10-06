// Built-in usage beacons — fire-and-forget, never breaks the UI.
// Session id is a tab-scoped UUID in sessionStorage (no cookies).
function sid() {
  try {
    let s = sessionStorage.getItem("osb-sid");
    if (!s) {
      s = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      sessionStorage.setItem("osb-sid", s);
    }
    return s;
  } catch {
    return "na";
  }
}

export function track(type, data = {}) {
  try {
    const body = JSON.stringify({ type, session: sid(), ...data });
    if (navigator.sendBeacon) navigator.sendBeacon("/api/event", body);
    // No fetch fallback: beacons must never delay navigation or fail loudly.
  } catch {
    /* fire-and-forget */
  }
}
