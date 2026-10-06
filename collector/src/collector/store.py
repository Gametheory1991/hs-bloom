"""Persistence: SQLite (default) or Postgres when DATABASE_URL is set.

Three tables: time series, JSON docs, fetcher health. The public API is
identical on both backends; DATABASE_URL only changes the driver and the
parameter placeholder style. SQLite stays the zero-dependency default —
psycopg is imported lazily and only required when DATABASE_URL is set
(install the `postgres` extra: pip install -e ".[postgres]").
"""
from __future__ import annotations

import json
import logging
import os
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
CREATE TABLE IF NOT EXISTS visits(
  ts         TEXT NOT NULL,
  day        TEXT NOT NULL,
  path       TEXT NOT NULL,
  method     TEXT NOT NULL,
  ip_hash    TEXT NOT NULL,
  user_agent TEXT NOT NULL,
  referrer   TEXT NOT NULL,
  device     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
  ts      TEXT NOT NULL,
  day     TEXT NOT NULL,
  session TEXT NOT NULL,
  type    TEXT NOT NULL,
  hub     TEXT,
  subtab  TEXT,
  detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_visits_day ON visits(day);
CREATE INDEX IF NOT EXISTS idx_visits_ip_ts ON visits(ip_hash, ts);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(day);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session, ts);
"""

try:  # optional: only needed when DATABASE_URL is set
    import psycopg  # type: ignore
except ImportError:  # pragma: no cover — exercised via monkeypatched fake
    psycopg = None  # type: ignore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Doc:
    payload: Any
    updated_at: str
    source: str


class Store:
    def __init__(self, path: str | Path):
        url = os.environ.get("DATABASE_URL")
        self._lock = threading.Lock()
        if url:
            if psycopg is None:
                raise RuntimeError(
                    "DATABASE_URL is set but psycopg is not installed; "
                    'run: pip install -e ".[postgres]"'
                )
            # autocommit off; every method commits explicitly like the
            # sqlite path. One connection shared under the lock — psycopg
            # connections are safe for sequential use across threads.
            self.conn = psycopg.connect(url)
            self._pg = True
            for stmt in (s.strip() for s in SCHEMA.split(";")):
                if stmt:
                    self.conn.execute(stmt)
            self.conn.commit()
        else:
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self._pg = False
            self.conn.executescript(SCHEMA)

    # -- internals ------------------------------------------------------
    def _q(self, sql: str) -> str:
        """Translate ? placeholders to the driver's paramstyle."""
        return sql.replace("?", "%s") if self._pg else sql

    def _executemany(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self.conn.executemany(self._q(sql), rows)
            self.conn.commit()

    def _execute(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self.conn.execute(self._q(sql), args)
            try:
                rows = cur.fetchall()
            except Exception:  # noqa: BLE001 — no result set (DDL)
                rows = []
            self.conn.commit()
        return rows

    # -- time series ----------------------------------------------------
    def upsert_points(self, series_id: str, points: Iterable[tuple[date, float]]) -> None:
        self._executemany(
            "INSERT INTO series_points(series_id, d, value) VALUES(?,?,?) "
            "ON CONFLICT(series_id, d) DO UPDATE SET value=excluded.value",
            [(series_id, dt.isoformat(), v) for dt, v in points],
        )

    def points(self, series_id: str, since: date | None = None) -> dict[date, float]:
        q = "SELECT d, value FROM series_points WHERE series_id=?"
        args: list[Any] = [series_id]
        if since is not None:
            q += " AND d >= ?"
            args.append(since.isoformat())
        rows = self._execute(q, tuple(args))
        out: dict[date, float] = {}
        for d, v in rows:
            if not isinstance(v, (int, float)):
                continue  # dynamic typing: skip corrupt rows
            try:
                out[date.fromisoformat(d) if isinstance(d, str) else d] = v
            except (ValueError, TypeError):
                continue
        return out

    # -- docs -----------------------------------------------------------
    def put_doc(self, key: str, payload: Any, source: str) -> None:
        self._execute(
            "INSERT INTO docs(key, payload, updated_at, source) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at, source=excluded.source",
            (key, json.dumps(payload), _now(), source),
        )

    def doc(self, key: str) -> Doc | None:
        rows = self._execute(
            "SELECT payload, updated_at, source FROM docs WHERE key=?", (key,)
        )
        if not rows:
            return None
        row = rows[0]
        try:
            payload = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            # a corrupted doc must behave like a missing doc, never 500 the API
            log.warning("dropping corrupted doc %r", key)
            return None
        return Doc(payload=payload, updated_at=row[1], source=row[2])

    # -- fetcher health --------------------------------------------------
    def record_success(self, name: str, active_source: str) -> None:
        self._execute(
            "INSERT INTO fetcher_status(name, last_success, active_source) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET last_success=excluded.last_success, "
            "active_source=excluded.active_source",
            (name, _now(), active_source),
        )

    def record_error(self, name: str, error: str) -> None:
        self._execute(
            "INSERT INTO fetcher_status(name, last_error, last_error_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET last_error=excluded.last_error, "
            "last_error_at=excluded.last_error_at",
            (name, error, _now()),
        )

    def prune_outside_range(self, series_id: str, lo: float, hi: float) -> None:
        """Delete stored points outside [lo, hi] — cleanup for feeds that once
        served corrupt values (upsert alone never removes them)."""
        self._execute(
            "DELETE FROM series_points WHERE series_id=? AND (value < ? OR value > ?)",
            (series_id, lo, hi),
        )

    def status(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.statuses() if s["name"] == name), None)

    def statuses(self) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT name, last_success, last_error, last_error_at, active_source "
            "FROM fetcher_status ORDER BY name"
        )
        cols = ["name", "last_success", "last_error", "last_error_at", "active_source"]
        return [dict(zip(cols, r)) for r in rows]
