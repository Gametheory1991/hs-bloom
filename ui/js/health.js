// Data-health strip: one dot per collector fetcher, fed by /healthz.
// green = healthy (no recorded error); amber = errored before but the last
// run succeeded; red = currently failing. Hover a dot for the fetcher name.
import { getHealth } from "./api.js";

function dotState(f) {
  if (!f.last_error_at) return "ok";
  if (!f.last_success) return "bad";
  return f.last_success >= f.last_error_at ? "warn" : "bad";
}

export function initHealth() {
  const el = document.getElementById("health-strip");
  if (!el) return;
  const render = async () => {
    try {
      const h = await getHealth();
      el.innerHTML = (h.fetchers ?? [])
        .map((f) => {
          const when = f.last_error_at ? ` · last error ${f.last_error_at}` : "";
          const title = `${f.name}${when}`.replace(/"/g, "&quot;");
          return `<span class="hdot ${dotState(f)}" title="${title}"></span>`;
        })
        .join("");
      el.classList.toggle("degraded", !h.ok);
    } catch {
      // Collector unreachable: the main banner already reports that.
    }
  };
  render();
  setInterval(render, 60_000);
}
