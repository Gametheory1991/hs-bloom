"""SEC fails-to-deliver fetcher tests: parse, publication windows, tracking."""
import io
import zipfile
from datetime import date

import pytest

from collector.config import SecDataCfg
from collector.fetchers.sec_ftd import (
    FILES_DOC,
    SERIES_DOLLAR,
    SERIES_SHARES,
    fetch_sec_ftd,
    file_key,
    parse_ftd_text,
    periods_expected,
    read_ftd_zip,
)
from collector.store import Store

FIXTURE = """SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE
20260817|B5950S113|MDXH|1206826|MDXHEALTH SA SHS NEW(BELGIUM) |0.81
20260817|B9151N105|TTAM|362|TITAN AMER SA COM|16.04
20260817|D18190898|DB|3382|DEUTSCHE BANK AG NAMEN AKT (DE|38.56
20260818|B5950S113|MDXH|500000|MDXHEALTH SA SHS NEW(BELGIUM) |1.00
20260818|XXXX|BAD|notanint|BROKEN ROW|1.00
20260818|Q3972M127|FYIRF|98|CADOUX LTD SHS (AUSTRALIA)|.
Trailer record count 5
Trailer total quantity of shares 1710570
"""


def _zip_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("cnsfails202608b.txt", text)
    return buf.getvalue()


CFG = SecDataCfg(user_agent="hs-bloom research contact harrysugamakc@gmail.com")


def test_parse_aggregates_by_settlement_date():
    agg = parse_ftd_text(FIXTURE)
    # 2026-08-17: 1206826*0.81 + 362*16.04 + 3382*38.56 ; shares 1206826+362+3382
    assert agg[date(2026, 8, 17)][0] == pytest.approx(
        1206826 * 0.81 + 362 * 16.04 + 3382 * 38.56)
    assert agg[date(2026, 8, 17)][1] == 1206826 + 362 + 3382
    # 2026-08-18: 500000*1.00 ; shares 500000 + 98 (the '.'-price row counts
    # shares but contributes no $; malformed row + trailers skipped)
    assert agg[date(2026, 8, 18)][0] == pytest.approx(500000.0)
    assert agg[date(2026, 8, 18)][1] == 500000 + 98
    assert len(agg) == 2


def test_parse_price_reads_last_field_when_description_has_pipe():
    # The DB row's description contains a pipe -> 7 fields; price is last.
    agg = parse_ftd_text(
        "SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE\n"
        "20260817|D18190898|DB|3382|DEUTSCHE BANK AG NAMEN AKT (DE|38.56\n")
    assert agg[date(2026, 8, 17)][0] == pytest.approx(3382 * 38.56)


def test_read_ftd_zip_extracts_txt():
    text = read_ftd_zip(_zip_bytes(FIXTURE))
    assert text.startswith("SETTLEMENT DATE|")


def test_periods_expected_publication_windows():
    # today 2026-10-08: Aug-2026 'a' + 'b' (2nd half avail ~Sep 15),
    # Sep-2026 'a' (avail ~end Sep), but NOT Sep 'b' (avail ~Oct 15) nor Oct 'a'.
    got = periods_expected(date(2026, 10, 8))
    assert (2026, 8, "a") in got and (2026, 8, "b") in got
    assert (2026, 9, "a") in got
    assert (2026, 9, "b") not in got
    assert (2026, 10, "a") not in got
    # first period ever
    assert (2004, 1, "a") in got


def test_periods_expected_before_second_half_publication():
    got = periods_expected(date(2026, 9, 10))
    assert (2026, 8, "a") in got
    assert (2026, 8, "b") not in got  # avail ~Sep 15


async def _run_fetch(store, fake_bytes, today, delay_s=0.0):
    async def fake_get_bytes(url, params=None, headers=None):
        assert headers and "User-Agent" in headers
        assert "harrysugamakc@gmail.com" in headers["User-Agent"]
        key = url.rsplit("/", 1)[-1].replace(".zip", "")
        if key in fake_bytes:
            return fake_bytes[key]
        raise RuntimeError(f"HTTP 404 for {url}")

    return await fetch_sec_ftd(CFG, store, fake_get_bytes, today=today,
                               delay_s=delay_s)


async def test_fetch_upserts_and_tracks(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    z = _zip_bytes(FIXTURE)
    today = date(2026, 9, 10)  # only up to 2026-07b expected (see window test)
    res = await _run_fetch(store, {
        file_key(2026, 7, "a"): z, file_key(2026, 7, "b"): z,
    }, today)
    assert "fetched 2 files" in res

    dollar = store.points(SERIES_DOLLAR)
    shares = store.points(SERIES_SHARES)
    exp_d = 1206826 * 0.81 + 362 * 16.04 + 3382 * 38.56
    exp_s = 1206826 + 362 + 3382
    # both half-month files cover the same settlement dates -> sums doubled
    assert dollar[date(2026, 8, 17)] == pytest.approx(2 * exp_d)
    assert shares[date(2026, 8, 17)] == 2 * exp_s

    doc = store.doc(FILES_DOC)
    assert file_key(2026, 7, "a") in doc.payload["files"]
    assert file_key(2026, 7, "b") in doc.payload["files"]

    # idempotent: second run fetches nothing new and leaves points unchanged
    # (recent-window 404s retry each run by design, so pending is not empty)
    res2 = await _run_fetch(store, {}, today)
    assert "fetched 0 files" in res2
    assert store.points(SERIES_DOLLAR)[date(2026, 8, 17)] == pytest.approx(2 * exp_d)


async def test_fetch_404_old_marked_never_published_but_recent_retried(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    # 2004-01a is decades outside the recent window -> never_published.
    # The most recent expected period 404s -> retried next run (not tracked).
    expected = periods_expected(date(2026, 9, 10))
    recent_key = file_key(*expected[-1])
    res = await _run_fetch(store, {}, date(2026, 9, 10))
    assert "skipped/failed" in res
    doc = store.doc(FILES_DOC)
    assert file_key(2004, 1, "a") in doc.payload["files"]  # marked done
    assert file_key(2004, 1, "a") in doc.payload["never_published"]
    assert recent_key not in doc.payload["files"]
    assert recent_key not in doc.payload["never_published"]
