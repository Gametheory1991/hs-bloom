"""Postgres backend for Store, exercised through a fake psycopg module.

The fake emulates the three tables in memory and asserts the PG code path:
%S placeholders (no '?'), per-statement DDL execution, and identical
read/write semantics to SQLite.
"""
import os
import re
import sys
import types
from datetime import date

import pytest

import collector.store as store_mod
from collector.store import Store


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConn:
    """Minimal psycopg-connection stand-in with in-memory tables."""

    def __init__(self):
        self.points = {}   # (series_id, d) -> value
        self.docs = {}     # key -> (payload, updated_at, source)
        self.status = {}   # name -> dict
        self.seen_sql = []
        self.commits = 0

    def execute(self, sql, args=()):
        self.seen_sql.append(sql)
        s = " ".join(sql.split())
        if s.startswith("CREATE TABLE"):
            return FakeCursor([])
        if s.startswith("INSERT INTO series_points"):
            sid, d, v = args
            self.points[(sid, d)] = v
            return FakeCursor([])
        if s.startswith("SELECT d, value FROM series_points"):
            sid = args[0]
            rows = [(d, v) for (s2, d), v in sorted(self.points.items())
                    if s2 == sid and (len(args) < 2 or d >= args[1])]
            return FakeCursor(rows)
        if s.startswith("DELETE FROM series_points"):
            sid, lo, hi = args
            for k in [k for k in self.points if k[0] == sid
                      and (self.points[k] < lo or self.points[k] > hi)]:
                del self.points[k]
            return FakeCursor([])
        if s.startswith("INSERT INTO docs"):
            key, payload, updated_at, source = args
            self.docs[key] = (payload, updated_at, source)
            return FakeCursor([])
        if s.startswith("SELECT payload, updated_at, source FROM docs"):
            r = self.docs.get(args[0])
            return FakeCursor([r] if r else [])
        if s.startswith("INSERT INTO fetcher_status"):
            name = args[0]
            row = self.status.setdefault(
                name, {"name": name, "last_success": None, "last_error": None,
                       "last_error_at": None, "active_source": None})
            if "last_success" in s:
                row["last_success"], row["active_source"] = args[1], args[2]
            else:
                row["last_error"], row["last_error_at"] = args[1], args[2]
            return FakeCursor([])
        if s.startswith("SELECT name, last_success"):
            rows = [(r["name"], r["last_success"], r["last_error"],
                     r["last_error_at"], r["active_source"])
                    for r in sorted(self.status.values(), key=lambda r: r["name"])]
            return FakeCursor(rows)
        raise AssertionError(f"fake cannot handle SQL: {sql!r}")

    def executemany(self, sql, rows):
        for r in rows:
            self.execute(sql, r)

    def commit(self):
        self.commits += 1


@pytest.fixture()
def pg_store(monkeypatch, tmp_path):
    fake_mod = types.ModuleType("psycopg")
    conns = []
    fake_mod.connect = lambda url: conns.append(FakeConn()) or conns[-1]
    monkeypatch.setattr(store_mod, "psycopg", fake_mod)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    st = Store(tmp_path / "unused.db")
    assert st._pg is True
    return st, conns[-1]


def test_pg_uses_percent_s_placeholders(pg_store):
    st, conn = pg_store
    st.upsert_points("s1", [(date(2026, 1, 1), 1.5)])
    assert conn.seen_sql, "expected SQL to be issued"
    assert all("?" not in s for s in conn.seen_sql), conn.seen_sql
    assert any("%s" in s for s in conn.seen_sql)


def test_pg_roundtrip_matches_sqlite_semantics(pg_store, tmp_path):
    st, _conn = pg_store
    st.upsert_points("s1", [(date(2026, 1, 1), 1.0), (date(2026, 1, 2), 2.0)])
    st.upsert_points("s1", [(date(2026, 1, 2), 2.5)])  # upsert overwrite
    pts = st.points("s1")
    assert pts == {date(2026, 1, 1): 1.0, date(2026, 1, 2): 2.5}
    assert st.points("s1", since=date(2026, 1, 2)) == {date(2026, 1, 2): 2.5}
    st.put_doc("k", {"a": [1, 2]}, "src")
    doc = st.doc("k")
    assert doc.payload == {"a": [1, 2]} and doc.source == "src"
    assert st.doc("missing") is None
    st.record_success("eq", "yahoo")
    st.record_error("eq", "boom")
    s = st.status("eq")
    assert s["active_source"] == "yahoo" and s["last_error"] == "boom"
    st.prune_outside_range("s1", 0.0, 2.0)
    assert st.points("s1") == {date(2026, 1, 1): 1.0}


def test_pg_corrupt_doc_behaves_as_missing(pg_store):
    st, conn = pg_store
    conn.docs["bad"] = ("{not json", "t", "s")
    assert st.doc("bad") is None


def test_pg_missing_driver_raises_helpfully(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "psycopg", None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    with pytest.raises(RuntimeError, match="psycopg"):
        Store(tmp_path / "x.db")


def test_sqlite_default_unchanged_when_no_database_url(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    st = Store(tmp_path / "t.db")
    assert st._pg is False
    st.upsert_points("s", [(date(2026, 5, 5), 3.0)])
    assert st.points("s") == {date(2026, 5, 5): 3.0}
