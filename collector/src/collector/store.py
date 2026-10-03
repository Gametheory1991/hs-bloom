"""SQLite persistence for series, JSON docs, fetcher health and the alert outbox."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS series_points(
  series_id TEXT NOT NULL,
  d         TEXT NOT NULL,
  value     REAL NOT NULL,
  PRIMARY KEY(series_id, d)
);
CREATE TABLE IF NOT EXISTS docs(
  key        TEXT PRIMARY KEY,
  payload    TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  source     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fetcher_status(
  name          TEXT PRIMARY KEY,
  last_success  TEXT,
  last_error    TEXT,
  last_error_at TEXT,
  active_source TEXT
);
CREATE TABLE IF NOT EXISTS alert_events(
  event_id TEXT PRIMARY KEY,
  series_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  direction TEXT NOT NULL,
  as_of TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alert_events_date ON alert_events(detected_at);
CREATE INDEX IF NOT EXISTS alert_events_series ON alert_events(series_id, as_of);
CREATE TABLE IF NOT EXISTS alert_deliveries(
  event_id TEXT NOT NULL REFERENCES alert_events(event_id),
  channel TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  retry_at TEXT NOT NULL,
  sent_at TEXT,
  last_error TEXT,
  PRIMARY KEY(event_id, channel)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Doc:
    payload: Any
    updated_at: str
    source: str


class Store:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self.conn.executescript(SCHEMA)

    def upsert_points(self, series_id: str, points: Iterable[tuple[date, float]]) -> None:
        with self._lock:
            self.conn.executemany(
                "INSERT INTO series_points(series_id, d, value) VALUES(?,?,?) "
                "ON CONFLICT(series_id, d) DO UPDATE SET value=excluded.value",
                [(series_id, dt.isoformat(), v) for dt, v in points],
            )
            self.conn.commit()

    def points(self, series_id: str, since: date | None = None) -> dict[date, float]:
        q = "SELECT d, value FROM series_points WHERE series_id=?"
        args: list[Any] = [series_id]
        if since is not None:
            q += " AND d >= ?"
            args.append(since.isoformat())
        with self._lock:
            rows = self.conn.execute(q, args).fetchall()
        return {
            date.fromisoformat(d): v
            for d, v in rows
            if isinstance(v, (int, float))  # sqlite dynamic typing: skip corrupt rows
        }

    def put_doc(self, key: str, payload: Any, source: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO docs(key, payload, updated_at, source) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at, source=excluded.source",
                (key, json.dumps(payload), _now(), source),
            )
            self.conn.commit()

    def doc(self, key: str) -> Doc | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT payload, updated_at, source FROM docs WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            # a corrupted doc must behave like a missing doc, never 500 the API
            log.warning("dropping corrupted doc %r", key)
            return None
        return Doc(payload=payload, updated_at=row[1], source=row[2])

    def record_success(self, name: str, active_source: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO fetcher_status(name, last_success, active_source) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET last_success=excluded.last_success, "
                "active_source=excluded.active_source",
                (name, _now(), active_source),
            )
            self.conn.commit()

    def record_error(self, name: str, error: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO fetcher_status(name, last_error, last_error_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET last_error=excluded.last_error, "
                "last_error_at=excluded.last_error_at",
                (name, error, _now()),
            )
            self.conn.commit()

    def prune_outside_range(self, series_id: str, lo: float, hi: float) -> None:
        """Delete stored points outside [lo, hi] — cleanup for feeds that once
        served corrupt values (upsert alone never removes them)."""
        with self._lock:
            self.conn.execute(
                "DELETE FROM series_points WHERE series_id=? AND (value < ? OR value > ?)",
                (series_id, lo, hi),
            )
            self.conn.commit()

    def status(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.statuses() if s["name"] == name), None)

    def record_alert(self, event: dict, channels: list[str], now: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO alert_events VALUES(?,?,?,?,?,?,?)",
                (event["event_id"], event["series_id"], event["kind"], event["direction"],
                 event["as_of"], now, json.dumps(event, allow_nan=False)),
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO alert_deliveries(event_id,channel,retry_at) VALUES(?,?,?)",
                [(event["event_id"], channel, now) for channel in channels],
            )
            self.conn.commit()

    def alerts(self, *, series_id: str | None = None, kind: str | None = None,
               direction: str | None = None, since: str | None = None,
               until: str | None = None, detected_since: str | None = None,
               limit: int = 50, offset: int = 0) -> dict:
        clauses, args = [], []
        for column, value in (("series_id", series_id), ("kind", kind), ("direction", direction)):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        for column, operator, value in (("as_of", ">=", since), ("as_of", "<=", until),
                                        ("detected_at", ">=", detected_since)):
            if value is not None:
                clauses.append(f"{column}{operator}?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            total = self.conn.execute("SELECT count(*) FROM alert_events" + where, args).fetchone()[0]
            rows = self.conn.execute(
                "SELECT event_id,payload,detected_at FROM alert_events" + where +
                " ORDER BY detected_at DESC,event_id LIMIT ? OFFSET ?", [*args, limit, offset],
            ).fetchall()
            items = []
            for event_id, payload, detected_at in rows:
                item = json.loads(payload)
                item["detected_at"] = detected_at
                deliveries = self.conn.execute(
                    "SELECT channel,state,attempts,sent_at,last_error,retry_at "
                    "FROM alert_deliveries WHERE event_id=? ORDER BY channel", (event_id,),
                ).fetchall()
                item["deliveries"] = [
                    dict(zip(("channel", "state", "attempts", "sent_at", "last_error", "retry_at"), row))
                    for row in deliveries
                ]
                items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def claim_alert_deliveries(self, channels: list[str], now: str, lease_until: str,
                               limit: int = 100) -> list[dict]:
        """Lease due work so concurrent runs cannot repeat successful deliveries."""
        if not channels:
            return []
        placeholders = ",".join("?" for _ in channels)
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self.conn.execute(
                    "SELECT d.event_id,d.channel,d.attempts,e.payload FROM alert_deliveries d "
                    "JOIN alert_events e ON e.event_id=d.event_id "
                    f"WHERE d.channel IN ({placeholders}) "
                    "AND d.state IN ('pending','error','delivering') AND d.retry_at<=? "
                    "ORDER BY d.retry_at,d.event_id,d.channel LIMIT ?", [*channels, now, limit],
                ).fetchall()
                self.conn.executemany(
                    "UPDATE alert_deliveries SET state='delivering',attempts=attempts+1,retry_at=? "
                    "WHERE event_id=? AND channel=?",
                    [(lease_until, event_id, channel) for event_id, channel, _, _ in rows],
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return [
            {"event": json.loads(payload), "channel": channel, "attempts": attempts + 1}
            for _, channel, attempts, payload in rows
        ]

    def cancel_disabled_alert_deliveries(self, config: dict) -> None:
        """Cancellation is terminal; re-enabling must not replay historical work."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT e.event_id,e.series_id,e.kind FROM alert_events e "
                "JOIN alert_deliveries d ON d.event_id=e.event_id "
                "WHERE d.state IN ('pending','error','delivering')",
            ).fetchall()
            cancelled = []
            for event_id, series_id, kind in rows:
                settings = {**config["defaults"], **config["series"].get(series_id, {})}
                enabled = settings["enabled"]
                if kind == "range":
                    enabled = enabled and settings["range_enabled"]
                elif kind == "reversal":
                    enabled = enabled and settings["reversal_enabled"]
                if not enabled:
                    cancelled.append((event_id,))
            self.conn.executemany(
                "UPDATE alert_deliveries SET state='cancelled',last_error=NULL "
                "WHERE event_id=? AND state IN ('pending','error','delivering')", cancelled,
            )
            self.conn.commit()

    def finish_alert_delivery(self, event_id: str, channel: str, *, now: str,
                              retry_at: str, error: str | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE alert_deliveries SET state=?,retry_at=?,sent_at=?,last_error=? "
                "WHERE event_id=? AND channel=? AND state='delivering'",
                ("error" if error else "sent", retry_at, None if error else now,
                 error, event_id, channel),
            )
            self.conn.commit()

    def statuses(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT name, last_success, last_error, last_error_at, active_source "
                "FROM fetcher_status ORDER BY name"
            ).fetchall()
        cols = ["name", "last_success", "last_error", "last_error_at", "active_source"]
        return [dict(zip(cols, r)) for r in rows]
