const STORAGE_KEY = "hsbloom:last-insight";

let registrationPromise = null;
const supportsNotifications = () => ("Notification" in window) && ("serviceWorker" in navigator);

function stateLabel(permission) {
  if (permission === "granted") return "ALERTS ON";
  if (permission === "denied") return "ALERTS BLOCKED";
  return "ENABLE ALERTS";
}

function updateButton() {
  const btn = document.getElementById("alerts-toggle");
  if (!btn) return;
  btn.textContent = stateLabel(Notification.permission);
  btn.disabled = Notification.permission === "denied";
}

export function initNotifications() {
  const btn = document.getElementById("alerts-toggle");
  if (!supportsNotifications()) {
    if (btn) {
      btn.textContent = "ALERTS UNSUPPORTED";
      btn.disabled = true;
    }
    return;
  }
  registrationPromise = navigator.serviceWorker.register("/sw.js").catch(() => null);
  updateButton();
  btn?.addEventListener("click", async () => {
    if (Notification.permission !== "default") return;
    await Notification.requestPermission();
    updateButton();
  });
}

export async function notifyInsights(panel) {
  if (!supportsNotifications() || !panel?.generated_at || Notification.permission !== "granted" || !registrationPromise) return;
  const signature = [
    panel.digest_id ?? panel.generated_at,
    panel.alerts?.[0]?.id ?? "",
    panel.trends?.[0]?.id ?? "",
  ].join("|");
  if (localStorage.getItem(STORAGE_KEY) === signature) return;
  const lead = panel.alerts?.[0] ?? panel.trends?.[0];
  const registration = await registrationPromise;
  if (!registration) return;
  try {
    await registration.showNotification("hs-bloom digest", {
      body: lead ? `${lead.name}: ${lead.summary}` : (panel.newsletter?.headline ?? "Digest updated"),
      tag: "hsbloom-digest",
      data: { url: "/#/pulse/snapshot" },
    });
    localStorage.setItem(STORAGE_KEY, signature);
  } catch {
    // best-effort notifications: do not fail dashboard refresh and allow retries
  }
}
