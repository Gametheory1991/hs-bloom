from datetime import date

from collector.store import Store


def make_store(tmp_path):
    return Store(tmp_path / "test.db")


def test_upsert_and_read_points(tmp_path):
    s = make_store(tmp_path)
    s.upsert_points("idx:SPX", [(date(2026, 7, 7), 6200.0), (date(2026, 7, 8), 6234.5)])
    s.upsert_points("idx:SPX", [(date(2026, 7, 8), 6240.0)])  # upsert overwrites
    pts = s.points("idx:SPX")
    assert pts == {date(2026, 7, 7): 6200.0, date(2026, 7, 8): 6240.0}


def test_points_since_filters(tmp_path):
    s = make_store(tmp_path)
    s.upsert_points("m", [(date(2025, 1, 1), 1.0), (date(2026, 1, 1), 2.0)])
    assert s.points("m", since=date(2025, 6, 1)) == {date(2026, 1, 1): 2.0}


def test_points_unknown_series_empty(tmp_path):
    assert make_store(tmp_path).points("nope") == {}


def test_doc_roundtrip_and_overwrite(tmp_path):
    s = make_store(tmp_path)
    s.put_doc("news", {"items": [1]}, source="rss")
    s.put_doc("news", {"items": [1, 2]}, source="rss")
    doc = s.doc("news")
    assert doc.payload == {"items": [1, 2]}
    assert doc.source == "rss"
    assert doc.updated_at.endswith("Z") or "+" in doc.updated_at
    assert s.doc("missing") is None


def test_fetcher_status(tmp_path):
    s = make_store(tmp_path)
    s.record_error("equity", "boom")
    s.record_success("equity", active_source="yahoo")
    s.record_error("news", "feed died")
    by_name = {st["name"]: st for st in s.statuses()}
    assert by_name["equity"]["active_source"] == "yahoo"
    assert by_name["equity"]["last_success"] is not None
    assert by_name["equity"]["last_error"] == "boom"  # error history kept
    assert by_name["news"]["last_success"] is None


def test_prune_stale_fetcher_status(tmp_path):
    s = make_store(tmp_path)
    s.record_error("ici_flows", "HTTP 403")  # renamed -> mf_flows
    s.record_success("mf_flows", active_source="fred-z1-mf-flows")
    s.record_success("equity", active_source="yahoo")
    n = s.prune_stale_fetcher_status(["mf_flows", "equity"])
    assert n == 1
    names = {st["name"] for st in s.statuses()}
    assert names == {"mf_flows", "equity"}
    # second call is a no-op
    assert s.prune_stale_fetcher_status(["mf_flows", "equity"]) == 0
    # empty keep-list never wipes the table
    assert s.prune_stale_fetcher_status([]) == 0
    assert len(s.statuses()) == 2


def test_concurrent_reads_and_writes_are_serialized(tmp_path):
    import threading

    s = make_store(tmp_path)
    s.upsert_points("idx:SPX", [(date(2026, 1, 1), 1.0)])
    errors: list[Exception] = []

    def writer():
        try:
            for i in range(200):
                s.upsert_points("idx:SPX", [(date(2026, 1, 1 + i % 27), float(i))])
                s.record_success("equity", active_source="yahoo")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def reader():
        try:
            for _ in range(200):
                s.points("idx:SPX")
                s.statuses()
                s.doc("missing")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_corrupted_doc_returns_none(tmp_path):
    s = make_store(tmp_path)
    s.conn.execute(
        "INSERT INTO docs(key, payload, updated_at, source) VALUES(?,?,?,?)",
        ("news", "{not json", "2026-07-08T00:00:00Z", "rss"),
    )
    s.conn.commit()
    assert s.doc("news") is None


def test_points_skips_non_numeric_values(tmp_path):
    s = make_store(tmp_path)
    s.upsert_points("m", [(date(2026, 1, 1), 1.0)])
    s.conn.execute("INSERT INTO series_points(series_id, d, value) VALUES('m', '2026-01-02', 'oops')")
    s.conn.commit()
    assert s.points("m") == {date(2026, 1, 1): 1.0}


def test_status_by_name(tmp_path):
    store = Store(tmp_path / "t.db")
    store.record_success("cycle", "cycle")
    assert store.status("cycle")["active_source"] == "cycle"
    assert store.status("nope") is None


def test_prune_outside_range(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_points("cycle:x", [(date(2026, 1, 1), 50.0), (date(2026, 2, 1), 10.0)])
    store.prune_outside_range("cycle:x", 20.0, 80.0)
    assert store.points("cycle:x") == {date(2026, 1, 1): 50.0}
