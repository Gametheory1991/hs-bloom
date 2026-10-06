import { fmtAge } from "../fmt.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (url) => {
  try {
    const u = new URL(url);
    return (u.protocol === "http:" || u.protocol === "https:") ? u.href : "#";
  } catch { return "#"; }
};

const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

const topicChip = (tid, label) =>
  `<a href="#/regwatch/topics" class="badge" data-topic="${esc(tid)}" style="text-decoration:none">${esc(label)}</a>`;

function daysUntil(dateStr) {
  if (!dateStr) return null;
  const ms = Date.parse(dateStr + "T23:59:59Z") - Date.now();
  return Math.ceil(ms / 86400000);
}

const urgency = (days) =>
  days == null ? `<span class="muted">no deadline</span>`
  : days < 0 ? `<span class="badge warn">closed ${-days}d ago</span>`
  : days === 0 ? `<span class="badge warn">closes today</span>`
  : days <= 14 ? `<span class="badge warn">${days}d left</span>`
  : `<span class="badge">${days}d left</span>`;

export function renderRegwatchNews(panel) {
  const body = document.querySelector("#panel-regwatch-news .panel-body");
  const items = panel.items ?? [];
  if (!items.length) {
    body.innerHTML = `<p class="muted">No regulatory news yet — the hourly collector run hasn't completed.</p>`;
    return;
  }
  const topicLabel = (tid) => panel.topics?.[tid]?.label ?? tid;
  body.innerHTML = items.map((n) => `
    <div class="news-item">
      <a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>
      <div style="margin:3px 0;color:var(--ink);font-size:12px">▸ ${esc(n.summary || "")}</div>
      <div class="news-meta">
        <span class="badge">${esc(n.agency_label || n.agency || "—")}</span>
        ${fmtDate(n.published_at)} · ${fmtAge(n.published_at)}
        ${(n.topics ?? []).map((t) => topicChip(t, topicLabel(t))).join(" ")}
      </div>
    </div>`).join("");
}

export function renderRegwatchRules(panel) {
  const body = document.querySelector("#panel-regwatch-rules .panel-body");
  const rules = panel.rules ?? [];
  if (!rules.length) {
    body.innerHTML = `<p class="muted">No rulemaking items yet — the Federal Register scan runs hourly.</p>`;
    return;
  }
  const proposed = rules.filter((r) => r.type === "proposed")
    .sort((a, b) => (daysUntil(a.comments_close_on) ?? 9999) - (daysUntil(b.comments_close_on) ?? 9999));
  const finals = rules.filter((r) => r.type !== "proposed");
  const row = (r) => `
    <tr>
      <td><a href="${safeUrl(r.link)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a></td>
      <td><span class="badge">${esc(r.agency_label || "")}</span></td>
      <td style="white-space:nowrap">${fmtDate(r.published_at)}</td>
      <td style="white-space:nowrap">${r.type === "proposed" ? urgency(daysUntil(r.comments_close_on)) + (r.comments_close_on ? `<div class="news-meta">closes ${esc(r.comments_close_on)}</div>` : "") : "—"}</td>
    </tr>`;
  body.innerHTML = `
    <h3>PROPOSED RULES — OPEN FOR COMMENT (${proposed.length})</h3>
    <table data-sortable><thead><tr><th>Rule</th><th>Agency</th><th>Published</th><th>Comment deadline</th></tr></thead>
    <tbody>${proposed.map(row).join("")}</tbody></table>
    <h3 style="margin-top:14px">FINAL RULES (${finals.length})</h3>
    <table data-sortable><thead><tr><th>Rule</th><th>Agency</th><th>Published</th><th></th></tr></thead>
    <tbody>${finals.map(row).join("")}</tbody></table>
    <p class="muted" style="margin-top:8px">Source: Federal Register API (SEC, CFTC, Federal Reserve). FINRA rules surface via the FINRA notices feed.</p>`;
}

export function renderRegwatchTopics(panel) {
  const body = document.querySelector("#panel-regwatch-topics .panel-body");
  const topics = panel.topics ?? {};
  const items = panel.items ?? [];
  const byId = Object.fromEntries(items.map((i) => [i.id, i]));
  const tids = Object.keys(topics);
  if (!tids.length) {
    body.innerHTML = `<p class="muted">Topic tagging runs with the hourly collector.</p>`;
    return;
  }
  body.innerHTML = tids.map((tid) => {
    const t = topics[tid];
    const latest = (t.latest ?? []).map((id) => byId[id]).filter(Boolean).slice(0, 5);
    return `
    <div class="news-item" id="topic-${esc(tid)}">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <strong>${esc(t.label)}</strong>
        <span class="badge">${t.count} hit${t.count === 1 ? "" : "s"}</span>
      </div>
      ${latest.length ? latest.map((n) => `
        <div style="margin:4px 0 4px 12px">
          <a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer" style="font-size:12px">${esc(n.title)}</a>
          <div class="news-meta">${esc(n.agency_label || "")} · ${fmtDate(n.published_at)}</div>
        </div>`).join("") : `<div class="news-meta" style="margin-left:12px">no hits in the current window</div>`}
    </div>`;
  }).join("");
}
