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
  if (!iso) return "date n/a";  // honest empty state: no fake fetch-time stamp
  const d = new Date(iso);
  return isNaN(d) ? "date n/a" : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

// "Jun 8, 2026 · 4m ago" — or just "date n/a" when the source gave no date.
const fmtStamp = (iso) => iso ? `${fmtDate(iso)} · ${fmtAge(iso)}` : "date n/a";

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
        ${fmtStamp(n.published_at)}
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

// ---- Topic Watch: Overall board + per-topic second-level tabs ----
let _rwActiveTopic = "overall";   // "overall" or a topic id
let _rwLastPanel = null;
let _rwPendingTopic = null;       // set when a topic chip is clicked in News Feed

if (typeof document !== "undefined") {
  document.addEventListener("click", (e) => {
    const chip = e.target.closest("a[data-topic]");
    if (chip && chip.dataset.topic) _rwPendingTopic = chip.dataset.topic;
  }, true);
}

const DAY_MS = 86400000;
const _rwDate = (iso) => { const t = Date.parse(iso); return isNaN(t) ? null : t; };

const heatBadge = (n7) => {
  if (n7 >= 3) return `<span class="badge warn" title="${n7} items in the last 7 days">${n7} hot</span>`;
  if (n7 >= 1) return `<span class="badge" title="${n7} items in the last 7 days" style="color:var(--blue);border-color:var(--blue)">${n7} warm</span>`;
  return `<span class="muted" title="no items in the last 7 days">quiet</span>`;
};

const trendCell = (n7, nPrev) => {
  const d = n7 - nPrev;
  if (n7 === 0 && nPrev === 0) return `<span class="muted">—</span>`;
  if (d > 0) return `<span style="color:var(--blue);font-weight:700">▲ +${d}</span>`;
  if (d < 0) return `<span class="muted">▼ ${d}</span>`;
  return `<span class="muted">– 0</span>`;
};

function _rwOverallBoard(panel, tids, itemsByTopic) {
  const now = Date.now();
  const rows = tids.map((tid) => {
    const t = panel.topics[tid];
    const hits = itemsByTopic[tid] || [];
    const n7 = hits.filter((i) => { const d = _rwDate(i.published_at); return d && now - d < 7 * DAY_MS; }).length;
    const nPrev = hits.filter((i) => { const d = _rwDate(i.published_at); return d && now - d >= 7 * DAY_MS && now - d < 14 * DAY_MS; }).length;
    const latest = hits[0] || null;
    return { tid, label: t.label, count: t.count, n7, nPrev, latest };
  }).sort((a, b) => (_rwDate(b.latest?.published_at) ?? 0) - (_rwDate(a.latest?.published_at) ?? 0));

  return `
  <table data-sortable><thead><tr>
    <th>Topic</th><th title="Total items in window">Items</th>
    <th title="Items in the last 7 days">Heat (7d)</th>
    <th title="Last 7 days vs prior 7 days">Trend</th>
    <th>Latest</th><th>Latest headline</th>
  </tr></thead><tbody>
  ${rows.map((r) => `
    <tr data-topic-row="${esc(r.tid)}" style="cursor:pointer">
      <td><strong>${esc(r.label)}</strong></td>
      <td style="text-align:right">${r.count}</td>
      <td>${heatBadge(r.n7)}</td>
      <td style="white-space:nowrap">${trendCell(r.n7, r.nPrev)}</td>
      <td style="white-space:nowrap">${fmtDate(r.latest?.published_at)}</td>
      <td>${r.latest ? `
        <a href="${safeUrl(r.latest.link)}" target="_blank" rel="noopener noreferrer" data-stop-topic>${esc(r.latest.title)}</a>
        <div style="color:var(--ink);font-size:12px">▸ ${esc(r.latest.summary || "")}</div>
        <div class="news-meta">${esc(r.latest.agency_label || "")}</div>` : `<span class="muted">no hits in the current window</span>`}
      </td>
    </tr>`).join("")}
  </tbody></table>
  <p class="muted" style="margin-top:6px">Heat: 3+ items in 7 days = hot · 1–2 = warm · 0 = quiet. Trend compares the last 7 days to the prior 7. Click a row to open that topic.</p>`;
}

function _rwTopicList(tid, label, hits) {
  if (!hits.length) return `<p class="muted">No hits for ${esc(label)} in the current window.</p>`;
  return hits.map((n) => `
    <div class="news-item">
      <a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>
      <div style="margin:3px 0;color:var(--ink);font-size:12px">▸ ${esc(n.summary || "")}</div>
      <div class="news-meta">
        <span class="badge">${esc(n.agency_label || n.agency || "—")}</span>
        ${fmtStamp(n.published_at)}
      </div>
    </div>`).join("");
}

// Rulemaking items tagged with this topic (Federal Register, last 120 days).
function _rwTopicRules(tid, label, rules) {
  const hits = (rules ?? []).filter((r) => (r.topics || []).includes(tid));
  const head = `<h3 style="margin-top:16px">RULEMAKING — ${esc(label)}</h3>`;
  if (!hits.length)
    return head + `<p class="muted">No rulemaking in the last 120 days.</p>`;
  return head + `
  <table data-sortable><thead><tr><th>Rule</th><th>Agency</th><th>Published</th><th>Type</th></tr></thead>
  <tbody>${hits.map((r) => `
    <tr>
      <td><a href="${safeUrl(r.link)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a></td>
      <td><span class="badge">${esc(r.agency_label || "")}</span></td>
      <td style="white-space:nowrap">${fmtDate(r.published_at)}</td>
      <td>${esc(r.type || "—")}</td>
    </tr>`).join("")}
  </tbody></table>`;
}

// ---- International: non-US agency items (ESMA, FCA) ----
const _rwIntlNote = `
  <p class="muted" style="margin:10px 0">Non-US bond-market transparency: ESMA (EU MiFID II/MiFIR post-trade
  transparency, FITRS, consolidated tape) and the UK FCA (gilt and corporate bond transparency).
  <strong>Ediphy</strong> (ediphy.io) runs the commercial "tape of tapes" — consolidated post-trade bond data
  across ESMA, the UK and US TRACE — but publishes no free data feed or RSS, so its tape is tracked here via
  ESMA/FCA announcements and news mentions, not direct data.</p>`;

function _rwInternational(panel) {
  const agencies = panel.intl_agencies ?? ["esma", "fca"];
  const hits = (panel.items ?? []).filter((i) => agencies.includes(i.agency));
  return _rwIntlNote + (
    hits.length ? _rwTopicList("international", "International", hits)
                : `<p class="muted">No international items in the current window — the ESMA/FCA feeds run hourly.</p>`);
}

export function renderRegwatchTopics(panel) {
  _rwLastPanel = panel;
  if (_rwPendingTopic) { _rwActiveTopic = _rwPendingTopic; _rwPendingTopic = null; }
  const body = document.querySelector("#panel-regwatch-topics .panel-body");
  const topics = panel.topics ?? {};
  const items = panel.items ?? [];
  const tids = Object.keys(topics);
  if (!tids.length) {
    body.innerHTML = `<p class="muted">Topic tagging runs with the hourly collector.</p>`;
    return;
  }
  // full per-topic item lists from the feed (newest first); no backend change needed
  const itemsByTopic = {};
  for (const tid of tids) itemsByTopic[tid] = items.filter((i) => (i.topics || []).includes(tid));
  if (_rwActiveTopic !== "overall" && !topics[_rwActiveTopic]) _rwActiveTopic = "overall";

  const tabBtn = (id, label) =>
    `<button type="button" data-rw-tab="${esc(id)}" class="${_rwActiveTopic === id ? "active" : ""}">${esc(label)}</button>`;
  const strip = `
    <div class="sub-row" style="margin:0 -10px 10px;padding-left:10px" role="tablist" aria-label="Topic Watch tabs">
      ${tabBtn("overall", "Overall")}
      ${tabBtn("international", "International")}
      ${tids.map((tid) => tabBtn(tid, topics[tid].label)).join("")}
    </div>`;

  const content = _rwActiveTopic === "overall"
    ? _rwOverallBoard(panel, tids, itemsByTopic)
    : _rwActiveTopic === "international"
    ? _rwInternational(panel)
    : `<h3>${esc(topics[_rwActiveTopic].label)} — ${itemsByTopic[_rwActiveTopic].length} hit${itemsByTopic[_rwActiveTopic].length === 1 ? "" : "s"}</h3>` +
      _rwTopicList(_rwActiveTopic, topics[_rwActiveTopic].label, itemsByTopic[_rwActiveTopic]) +
      _rwTopicRules(_rwActiveTopic, topics[_rwActiveTopic].label, panel.rules);

  body.innerHTML = strip + `<div role="tabpanel">${content}</div>`;

  body.querySelectorAll("[data-rw-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      _rwActiveTopic = btn.dataset.rwTab;
      renderRegwatchTopics(_rwLastPanel);
    });
  });
  body.querySelectorAll("[data-topic-row]").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("a")) return;  // let headline links open the source
      _rwActiveTopic = row.dataset.topicRow;
      renderRegwatchTopics(_rwLastPanel);
    });
  });
}
