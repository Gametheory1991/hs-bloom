// ALERT TUNING panel (mkt tab): per-alert-type threshold multiplier + mute,
// persisted via PUT /api/alerts/config. Transient saved/error state per row.
import { getAlertConfig, putAlertConfig } from "../api.js";

async function load() {
  const body = document.querySelector("#panel-alerts .panel-body");
  if (!body) return;
  let types = [];
  try {
    const cfg = await getAlertConfig();
    types = cfg.types ?? [];
  } catch (err) {
    body.innerHTML = `<div class="muted">alert config unavailable (${err.message})</div>`;
    return;
  }
  if (!types.length) {
    body.innerHTML = `<div class="muted">no alert types</div>`;
    return;
  }
  body.innerHTML = types.map((t) => `
    <div class="alert-row" data-id="${t.id}">
      <div class="alert-info"><div class="alert-name">${t.label}</div>
        <div class="alert-state muted"></div></div>
      <label class="alert-ctl">threshold ×
        <input type="number" min="0.1" max="10" step="0.1" value="${t.threshold_mult}" class="alert-mult"></label>
      <label class="alert-ctl">mute <input type="checkbox" class="alert-mute" ${t.muted ? "checked" : ""}></label>
    </div>`).join("");

  body.querySelectorAll(".alert-row").forEach((row) => {
    const id = row.dataset.id;
    const state = row.querySelector(".alert-state");
    const mult = row.querySelector(".alert-mult");
    const mute = row.querySelector(".alert-mute");
    const save = async () => {
      state.textContent = "saving…";
      state.className = "alert-state muted";
      try {
        await putAlertConfig({ id, threshold_mult: parseFloat(mult.value), muted: mute.checked });
        state.textContent = "saved ✓";
        state.className = "alert-state up";
      } catch (err) {
        state.textContent = `error: ${err.message}`;
        state.className = "alert-state down";
      }
      setTimeout(() => { state.textContent = ""; }, 2500);
    };
    mult.addEventListener("change", save);
    mute.addEventListener("change", save);
  });
}

export function renderAlerts() { load(); }
