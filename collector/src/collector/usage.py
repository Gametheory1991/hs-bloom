"""Built-in usage analytics for hs-bloom (no third-party service).

Privacy: raw IPs are never stored — only a SHA256 hash salted with the
UTC date (so hashes rotate daily and can't be stitched across days).
No cookies; the client generates its own tab-scoped session UUID in
sessionStorage for event attribution.

Tables (created via Store SCHEMA):
  visits(ts, day, path, method, ip_hash, user_agent, referrer, device)
  events(ts, day, session, type, hub, subtab, detail)
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

# --- request filtering -------------------------------------------------

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|mediapartners|baidu|yandex|sogou|exabot|"
    r"facebot|ia_archiver|pingdom|uptimerobot|headless",
    re.IGNORECASE,
)

_SKIP_EXACT = {"/healthz", "/api/event"}  # telemetry ingestion is not a view
_SKIP_PREFIXES = ("/api/series",)  # too noisy; SPA pageviews come via /api/event
_SKIP_SUFFIXES = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".map", ".txt", ".xml",
)

_MOBILE_RE = re.compile(r"mobile|android|iphone|ipad|ipod|phone", re.IGNORECASE)

_EVENT_TYPES = {"pageview", "hub_click", "subtab_click"}


def should_log(path: str, user_agent: str) -> bool:
    """True when this request is worth a visits row."""
    if path in _SKIP_EXACT:
        return False
    if path.startswith(_SKIP_PREFIXES):
        return False
    lp = path.lower()
    if lp.endswith(_SKIP_SUFFIXES):
        return False
    if _BOT_RE.search(user_agent or ""):
        return False
    return True


def client_ip(x_forwarded_for: str | None, fallback: str | None) -> str:
    """Leftmost X-Forwarded-For entry (Render sets it); else direct peer."""
    if x_forwarded_for:
        first = x_forwarded_for.split(",")[0].strip()
        if first:
            return first
    return fallback or "unknown"


def ip_hash(ip: str, day: str) -> str:
    """SHA256(ip + daily salt), truncated. Never store the raw IP."""
    return hashlib.sha256(f"{day}:{ip}".encode()).hexdigest()[:32]


def device_of(user_agent: str) -> str:
    return "mobile" if _MOBILE_RE.search(user_agent or "") else "desktop"


def log_visit(store, *, ts: datetime, path: str, method: str,
              ip: str, user_agent: str, referrer: str) -> bool:
    """Insert one visits row. Returns False when filtered out."""
    ua = (user_agent or "")[:300]
    if not should_log(path, ua):
        return False
    day = ts.strftime("%Y-%m-%d")
    store._execute(
        "INSERT INTO visits(ts,day,path,method,ip_hash,user_agent,referrer,device)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (ts.isoformat(), day, path[:200], method[:10], ip_hash(ip, day),
         ua, (referrer or "")[:300], device_of(ua)),
    )
    return True


def record_event(store, *, ts: datetime, session: str, type: str,
                 hub: str | None = None, subtab: str | None = None,
                 detail: str | None = None) -> bool:
    """Insert one events row. Returns False on invalid type."""
    if type not in _EVENT_TYPES:
        return False
    day = ts.strftime("%Y-%m-%d")
    store._execute(
        "INSERT INTO events(ts,day,session,type,hub,subtab,detail)"
        " VALUES(?,?,?,?,?,?,?)",
        (ts.isoformat(), day, (session or "na")[:64], type,
         (hub or "")[:64], (subtab or "")[:64], (detail or "")[:200]),
    )
    return True


# --- stats ---------------------------------------------------------------

def _rows_to_daily(rows: list[tuple], idx: int = 1) -> list[dict]:
    return [{"day": r[0], "n": r[idx]} for r in rows]


def usage_stats(store, days: int = 30) -> dict:
    """Aggregate usage over the trailing `days` (UTC days, inclusive)."""
    days = max(1, min(int(days or 30), 365))
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    q = store._execute

    # Server-side: unique visitors + hits per day (document loads + API calls).
    uv = q("SELECT day, COUNT(DISTINCT ip_hash), COUNT(*) FROM visits"
           " WHERE day >= ? GROUP BY day ORDER BY day", (cutoff,))
    # SPA pageviews per day (client beacons; the honest "page view" count).
    pv = q("SELECT day, COUNT(*) FROM events WHERE day >= ? AND type='pageview'"
           " GROUP BY day ORDER BY day", (cutoff,))

    # Sessions from client session ids (tab-scoped UUIDs in sessionStorage).
    sess = q("SELECT session, MIN(ts), MAX(ts), COUNT(*) FROM events"
             " WHERE day >= ? AND type='pageview' GROUP BY session", (cutoff,))
    n_sessions = len(sess)
    bounces = sum(1 for s in sess if s[3] == 1)
    durations = []
    for _, t0, t1, n in sess:
        try:
            d = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds()
            durations.append(max(0.0, d))
        except (ValueError, TypeError):
            continue
    avg_dur = round(sum(durations) / len(durations), 1) if durations else 0.0

    # Top SPA routes.
    routes = q("SELECT hub, subtab, COUNT(*) FROM events"
               " WHERE day >= ? AND type='pageview' GROUP BY hub, subtab"
               " ORDER BY 3 DESC LIMIT 20", (cutoff,))
    # Hub clicks + share.
    hub_clicks = q("SELECT hub, COUNT(*) FROM events WHERE day >= ? AND type='hub_click'"
                   " GROUP BY hub ORDER BY 2 DESC", (cutoff,))
    total_hub_clicks = sum(r[1] for r in hub_clicks) or 1
    # Subtab clicks.
    sub_clicks = q("SELECT hub, subtab, COUNT(*) FROM events"
                   " WHERE day >= ? AND type='subtab_click' GROUP BY hub, subtab"
                   " ORDER BY 3 DESC LIMIT 20", (cutoff,))
    # Referrers (server-side; empty = direct/bookmark).
    refs = q("SELECT referrer, COUNT(*) FROM visits WHERE day >= ? AND referrer <> ''"
             " GROUP BY referrer ORDER BY 2 DESC LIMIT 15", (cutoff,))
    # Device split by unique visitor.
    dev = q("SELECT device, COUNT(DISTINCT ip_hash) FROM visits WHERE day >= ?"
            " GROUP BY device", (cutoff,))

    total_uv = q("SELECT COUNT(DISTINCT ip_hash) FROM visits WHERE day >= ?",
                 (cutoff,))[0][0]
    total_pv = q("SELECT COUNT(*) FROM events WHERE day >= ? AND type='pageview'",
                 (cutoff,))[0][0]

    return {
        "days": days,
        "as_of": now.isoformat(),
        "kpis": {
            "unique_visitors": total_uv,
            "pageviews": total_pv,
            "sessions": n_sessions,
            "bounce_rate": round(bounces / n_sessions, 4) if n_sessions else None,
            "avg_session_seconds": avg_dur,
        },
        "daily_visitors": [{"day": r[0], "visitors": r[1], "hits": r[2]} for r in uv],
        "daily_pageviews": _rows_to_daily(pv),
        "top_routes": [{"route": f"#/{r[0]}/{r[1]}".rstrip("/"), "pageviews": r[2]}
                       for r in routes],
        "hub_clicks": [{"hub": r[0], "clicks": r[1],
                        "share": round(r[1] / total_hub_clicks, 4)}
                       for r in hub_clicks],
        "subtab_clicks": [{"route": f"#/{r[0]}/{r[1]}".rstrip("/"), "clicks": r[2]}
                          for r in sub_clicks],
        "referrers": [{"referrer": r[0], "hits": r[1]} for r in refs],
        "devices": [{"device": r[0], "visitors": r[1]} for r in dev],
    }
