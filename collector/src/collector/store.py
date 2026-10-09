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
CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);
-- FINRA equity completeness (2026-10-06): full-universe detail tables.
-- One row per (settlement, ticker), ~22.6k tickers x ~160 settlements.
-- Never put a semicolon in these comments: the Postgres path splits SCHEMA on it.
CREATE TABLE IF NOT EXISTS short_interest(
  settlement_date TEXT NOT NULL,
  symbol          TEXT NOT NULL,
  name            TEXT,
  exchange        TEXT,
  market_class    TEXT,
  short           REAL NOT NULL,
  prev            REAL,
  chg_nom         REAL,
  chg_pct         REAL,
  adv             REAL,
  dtc             REAL,
  split_flag      TEXT,
  revision        TEXT,
  PRIMARY KEY(settlement_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_short_interest_settle ON short_interest(settlement_date);
CREATE INDEX IF NOT EXISTS idx_short_interest_symbol ON short_interest(symbol);
-- Reg SHO top-500 daily shorted tickers (consolidated, CNMS-based).
CREATE TABLE IF NOT EXISTS regsho_top(
  d      TEXT NOT NULL,
  symbol TEXT NOT NULL,
  market TEXT NOT NULL,
  short  REAL,
  exempt REAL,
  total  REAL,
  ratio  REAL,
  PRIMARY KEY(d, symbol, market)
);
CREATE INDEX IF NOT EXISTS idx_regsho_top_d ON regsho_top(d);
CREATE INDEX IF NOT EXISTS idx_regsho_top_symbol ON regsho_top(symbol);
-- OTC Reg SHO threshold list history (weekly snapshots).
CREATE TABLE IF NOT EXISTS regsho_threshold_hist(
  trade_date TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  name       TEXT,
  category   TEXT,
  reg_sho    TEXT,
  rule4320   TEXT,
  PRIMARY KEY(trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_threshold_hist_date ON regsho_threshold_hist(trade_date);
CREATE INDEX IF NOT EXISTS idx_threshold_hist_symbol ON regsho_threshold_hist(symbol);
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
            # Don't fail fast on a transient write lock (a concurrent reader
            # or a second process's bulk upsert): retry inside SQLite for up
            # to 30s before surfacing OperationalError. The in-process
            # threading lock still serializes our own access first.
            self.conn.execute("PRAGMA busy_timeout=30000")
            # WAL: readers stop blocking the writer at the SQLite level.
            # Without this, dashboard SELECTs and fetcher INSERTs serialize
            # on the database file itself, on top of the threading lock.
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
            except Exception:  # noqa: BLE001 — read-only FS etc.
                pass
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
        # Reads skip commit(): an fsync on every SELECT doubles I/O and,
        # under the shared lock, is what stalled the event loop during
        # dashboard rebuilds (Oct 8 health-check crash loop).
        is_read = sql.lstrip()[:6].upper() == "SELECT"
        with self._lock:
            cur = self.conn.execute(self._q(sql), args)
            try:
                rows = cur.fetchall()
            except Exception:  # noqa: BLE001 — no result set (DDL)
                rows = []
            if not is_read:
                self.conn.commit()
        return rows

    # -- time series ----------------------------------------------------
    def upsert_points(self, series_id: str, points: Iterable[tuple[date, float]]) -> None:
        self._executemany(
            "INSERT INTO series_points(series_id, d, value) VALUES(?,?,?) "
            "ON CONFLICT(series_id, d) DO UPDATE SET value=excluded.value",
            [(series_id, dt.isoformat(), v) for dt, v in points],
        )
        self._bump_data_version()

    def upsert_points_batch(self, items: Iterable[tuple[str, date, float]]) -> None:
        """Many (series_id, date, value) triples in ONE executemany + commit.

        For bulk backfills: N per-series upsert_points calls cost N fsyncs;
        this costs one. Idempotent like upsert_points.
        """
        self._executemany(
            "INSERT INTO series_points(series_id, d, value) VALUES(?,?,?) "
            "ON CONFLICT(series_id, d) DO UPDATE SET value=excluded.value",
            [(sid, dt.isoformat(), v) for sid, dt, v in items],
        )
        self._bump_data_version()

    def delete_points(self, series_id: str, dates: Iterable[date]) -> int:
        """Delete specific dates from a series. Returns rows deleted."""
        dates = list(dates)
        if not dates:
            return 0
        cur = self._execute(
            "DELETE FROM series_points WHERE series_id=? AND d IN "
            f"({','.join('?' * len(dates))})",
            [series_id] + [d.isoformat() for d in dates],
        )
        self._bump_data_version()
        return cur.rowcount or 0

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

    def points_many(self, series_ids: list[str]) -> dict[str, dict[date, float]]:
        """Batch version of points(): one query for many series.

        For the dashboard build, which used to fan out ~470 individual
        points() calls per request.
        """
        ids = [s for s in dict.fromkeys(series_ids) if s]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._execute(
            f"SELECT series_id, d, value FROM series_points "
            f"WHERE series_id IN ({placeholders})",
            tuple(ids),
        )
        out: dict[str, dict[date, float]] = {sid: {} for sid in ids}
        for sid, d, v in rows:
            if not isinstance(v, (int, float)):
                continue  # dynamic typing: skip corrupt rows
            try:
                key = date.fromisoformat(d) if isinstance(d, str) else d
            except (ValueError, TypeError):
                continue
            out[sid][key] = v
        return out

    def _bump_data_version(self) -> None:
        """Mark cached API payloads stale. Called by every data-write path;
        status writes (record_success/record_error) are excluded on purpose —
        a fetcher heartbeat must not invalidate the dashboard cache."""
        self._execute(
            "INSERT INTO meta(key, value) VALUES('data_version', 1) "
            "ON CONFLICT(key) DO UPDATE SET value = meta.value + 1"
        )

    def data_version(self) -> int:
        """Monotonic counter of data writes; drives dashboard memoization."""
        rows = self._execute("SELECT value FROM meta WHERE key='data_version'")
        return int(rows[0][0]) if rows else 0

    # -- docs -----------------------------------------------------------
    def put_doc(self, key: str, payload: Any, source: str) -> None:
        self.put_doc_quiet(key, payload, source)
        self._bump_data_version()

    def put_doc_quiet(self, key: str, payload: Any, source: str) -> None:
        """put_doc without bumping data_version — for bookkeeping docs the
        dashboard never renders (e.g. the scheduler's job_runs heartbeat),
        so they don't invalidate the dashboard cache on every job run."""
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

    def doc_keys(self, prefix: str) -> list[str]:
        """Doc keys starting with ``prefix`` (for archival enumeration)."""
        rows = self._execute(
            "SELECT key FROM docs WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
            (prefix.replace("\\", "\\\\").replace("%", "\\%")
             .replace("_", "\\_") + "%",),
        )
        return [r[0] for r in rows]

    # -- fetcher health --------------------------------------------------
    def record_success(self, name: str, active_source: str) -> None:
        self._execute(
            "INSERT INTO fetcher_status(name, last_success, active_source) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET last_success=excluded.last_success, "
            "active_source=excluded.active_source",
            # last_error/last_error_at are kept on purpose: health is
            # last_success >= last_error_at, and the UI shows "recovered
            # after an error" (warn) from that history.
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
        self._bump_data_version()

    def status(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.statuses() if s["name"] == name), None)

    def statuses(self) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT name, last_success, last_error, last_error_at, active_source "
            "FROM fetcher_status ORDER BY name"
        )
        cols = ["name", "last_success", "last_error", "last_error_at", "active_source"]
        return [dict(zip(cols, r)) for r in rows]

    # -- FINRA short-interest full universe ---------------------------------
    def upsert_short_interest(self, rows: list[tuple]) -> None:
        """Idempotent bulk insert of full-universe rows.

        Each row: (settlement_date iso, symbol, name, exchange, market_class,
        short, prev, chg_nom, chg_pct, adv, dtc, split_flag, revision).
        """
        self._executemany(
            "INSERT INTO short_interest(settlement_date, symbol, name, exchange, "
            "market_class, short, prev, chg_nom, chg_pct, adv, dtc, split_flag, "
            "revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(settlement_date, symbol) DO UPDATE SET "
            "name=excluded.name, exchange=excluded.exchange, "
            "market_class=excluded.market_class, short=excluded.short, "
            "prev=excluded.prev, chg_nom=excluded.chg_nom, "
            "chg_pct=excluded.chg_pct, adv=excluded.adv, dtc=excluded.dtc, "
            "split_flag=excluded.split_flag, revision=excluded.revision",
            rows,
        )
        self._bump_data_version()

    def short_interest_settlements(self) -> list[tuple[date, int]]:
        """[(settlement date, ticker count)] newest first — backfill progress."""
        rows = self._execute(
            "SELECT settlement_date, COUNT(*) FROM short_interest "
            "GROUP BY settlement_date ORDER BY settlement_date DESC"
        )
        out = []
        for d, n in rows:
            try:
                out.append((date.fromisoformat(d), n))
            except (ValueError, TypeError):
                continue
        return out

    _SI_SORT = {
        "symbol": "symbol", "name": "name", "exchange": "exchange",
        "short": "short", "prev": "prev", "chg_nom": "chg_nom",
        "chg_pct": "chg_pct", "adv": "adv", "dtc": "dtc",
    }

    def short_interest_rows(self, settlement: date, search: str | None = None,
                            sort: str = "short", direction: str = "desc",
                            page: int = 1, per_page: int = 50) -> tuple[int, list[dict]]:
        """Paginated ticker table for one settlement. Sort key is allowlisted."""
        col = self._SI_SORT.get(sort or "short", "short")
        direc = "DESC" if (direction or "desc").lower() == "desc" else "ASC"
        where, args = "settlement_date=?", [settlement.isoformat()]
        if search:
            where += " AND (symbol LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')"
            esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args += [f"%{esc}%", f"%{esc}%"]
        total = self._execute(
            f"SELECT COUNT(*) FROM short_interest WHERE {where}", tuple(args))[0][0]
        page = max(1, int(page or 1))
        per_page = max(1, min(int(per_page or 50), 25000))
        rows = self._execute(
            f"SELECT symbol, name, exchange, market_class, short, prev, chg_nom, "
            f"chg_pct, adv, dtc, split_flag, revision FROM short_interest "
            f"WHERE {where} ORDER BY {col} {direc}, symbol ASC LIMIT ? OFFSET ?",
            tuple(args) + (per_page, (page - 1) * per_page),
        )
        cols = ["symbol", "name", "exchange", "market_class", "short", "prev",
                "chg_nom", "chg_pct", "adv", "dtc", "split_flag", "revision"]
        return total, [dict(zip(cols, r)) for r in rows]

    def short_interest_symbol_history(self, symbol: str,
                                      since: date | None = None) -> list[dict]:
        q = ("SELECT settlement_date, short, prev, chg_nom, chg_pct, adv, dtc "
             "FROM short_interest WHERE symbol=?")
        args: list[Any] = [symbol.upper()]
        if since is not None:
            q += " AND settlement_date >= ?"
            args.append(since.isoformat())
        rows = self._execute(q + " ORDER BY settlement_date ASC", tuple(args))
        return [{"date": r[0], "short": r[1], "prev": r[2], "chg_nom": r[3],
                 "chg_pct": r[4], "adv": r[5], "dtc": r[6]} for r in rows]

    def short_interest_scope(self) -> dict[str, Any]:
        rows = self.short_interest_settlements()
        total = self._execute("SELECT COUNT(*) FROM short_interest")[0][0]
        return {
            "settlements": len(rows),
            "first": rows[-1][0].isoformat() if rows else None,
            "last": rows[0][0].isoformat() if rows else None,
            "rows": total,
        }

    # -- Reg SHO top-500 daily --------------------------------------------
    def upsert_regsho_top(self, rows: list[tuple]) -> None:
        """Idempotent bulk insert. Each row:
        (d iso, symbol, market, short, exempt, total, ratio)."""
        self._executemany(
            "INSERT INTO regsho_top(d, symbol, market, short, exempt, total, ratio) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(d, symbol, market) DO UPDATE SET "
            "short=excluded.short, exempt=excluded.exempt, total=excluded.total, "
            "ratio=excluded.ratio",
            rows,
        )
        self._bump_data_version()

    def regsho_top_scope(self) -> dict[str, Any]:
        rows = self._execute(
            "SELECT MIN(d), MAX(d), COUNT(DISTINCT d), COUNT(*) FROM regsho_top")
        first, last, days, total = rows[0] if rows else (None, None, 0, 0)
        return {"first": first, "last": last, "days": days or 0, "rows": total or 0}

    def regsho_top_rows(self, day: date, search: str | None = None,
                        page: int = 1, per_page: int = 50) -> tuple[int, list[dict]]:
        where, args = "d=?", [day.isoformat()]
        if search:
            where += " AND symbol LIKE ? ESCAPE '\\'"
            esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.append(f"%{esc}%")
        total = self._execute(
            f"SELECT COUNT(*) FROM regsho_top WHERE {where}", tuple(args))[0][0]
        page = max(1, int(page or 1))
        per_page = max(1, min(int(per_page or 50), 25000))
        rows = self._execute(
            f"SELECT symbol, market, short, exempt, total, ratio FROM regsho_top "
            f"WHERE {where} ORDER BY short DESC, symbol ASC LIMIT ? OFFSET ?",
            tuple(args) + (per_page, (page - 1) * per_page),
        )
        cols = ["symbol", "market", "short", "exempt", "total", "ratio"]
        return total, [dict(zip(cols, r)) for r in rows]

    # -- Reg SHO threshold-list history -------------------------------------
    def upsert_threshold_hist(self, rows: list[tuple]) -> None:
        """Idempotent bulk insert. Each row:
        (trade_date iso, symbol, name, category, reg_sho, rule4320)."""
        self._executemany(
            "INSERT INTO regsho_threshold_hist(trade_date, symbol, name, category, "
            "reg_sho, rule4320) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(trade_date, symbol) DO UPDATE SET name=excluded.name, "
            "category=excluded.category, reg_sho=excluded.reg_sho, "
            "rule4320=excluded.rule4320",
            rows,
        )
        self._bump_data_version()

    def threshold_hist_dates(self) -> list[date]:
        rows = self._execute(
            "SELECT DISTINCT trade_date FROM regsho_threshold_hist "
            "ORDER BY trade_date DESC")
        out = []
        for (d,) in rows:
            try:
                out.append(date.fromisoformat(d))
            except (ValueError, TypeError):
                continue
        return out

    def threshold_hist_rows(self, day: date, search: str | None = None,
                            page: int = 1, per_page: int = 200) -> tuple[int, list[dict]]:
        where, args = "trade_date=?", [day.isoformat()]
        if search:
            where += " AND (symbol LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')"
            esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args += [f"%{esc}%", f"%{esc}%"]
        total = self._execute(
            f"SELECT COUNT(*) FROM regsho_threshold_hist WHERE {where}",
            tuple(args))[0][0]
        page = max(1, int(page or 1))
        per_page = max(1, min(int(per_page or 200), 5000))
        rows = self._execute(
            f"SELECT symbol, name, category, reg_sho, rule4320 "
            f"FROM regsho_threshold_hist WHERE {where} "
            f"ORDER BY symbol ASC LIMIT ? OFFSET ?",
            tuple(args) + (per_page, (page - 1) * per_page),
        )
        cols = ["symbol", "name", "category", "reg_sho", "rule4320"]
        return total, [dict(zip(cols, r)) for r in rows]

    def threshold_hist_symbol(self, symbol: str) -> list[dict]:
        rows = self._execute(
            "SELECT trade_date, name, category FROM regsho_threshold_hist "
            "WHERE symbol=? ORDER BY trade_date ASC", (symbol.upper(),))
        return [{"date": r[0], "name": r[1], "category": r[2]} for r in rows]
