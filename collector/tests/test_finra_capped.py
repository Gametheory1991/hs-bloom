"""Tests for the FINRA capped volume report fetcher (monthly corporate/agency
capped trade volume, keyless CSV).

Inline samples shaped like the real CA_CTA.csv — no binary fixtures, no
network. Fakes are injected for get_text.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from collector.fetchers import finra_capped
from collector.fetchers.finra_capped import (
    GRADE_SLUGS,
    SERIES_IDS,
    fetch_finra_capped,
    parse_capped,
    parse_month,
    parse_num,
)
from collector.store import Store


def _csv(rows: list[tuple[str, str, str, str]]) -> str:
    """Build a CA_CTA-shaped CSV: two header rows then data rows.

    Each row: (MONTH, Grade, AVG Size (000s), Total). Values may carry
    comma formatting inside quotes, exactly like the real file.
    """
    lines = [
        ",,,,Par Value %,Par Value %,Par Value %,Par Value %",
        "MONTH,Grade,AVG Size (000s),Total,<5mm,>5mm,>=10mm,>=25mm",
    ]
    for month, grade, avgsize, total in rows:
        lines.append(f'{month},{grade},"{avgsize}","{total}",0.00%,0.00%,0.00%,0.00%')
    return "\n".join(lines) + "\n"


SAMPLE_ROWS = [
    ("Sep-2026", "Investment Grade", "12,158.84", "343,499,264.21"),
    ("Sep-2026", "High Yield", "4,226.56", "104,987,829.11"),
    ("Sep-2026", "Agency", "44,504.24", "76,324,778.35"),
    ("Sep-2026", "144A - IG", "9,999.99", "50,000,000.00"),
    ("Sep-2026", "144A - HY", "1,111.11", "10,000,000.00"),
    ("Aug-2026", "Investment Grade", "11,000.00", "300,000,000.00"),
    ("Aug-2026", "High Yield", "4,000.00", "100,000,000.00"),
    ("Aug-2026", "Agency", "40,000.00", "70,000,000.00"),
    ("Aug-2026", "144A - IG", "8,000.00", "40,000,000.00"),
    ("Aug-2026", "144A - HY", "1,000.00", "9,000,000.00"),
]


def test_parse_num_comma_formatting():
    assert parse_num("12,158.84") == pytest.approx(12158.84)
    assert parse_num("343,499,264.21") == pytest.approx(343499264.21)
    assert parse_num("0") == 0.0
    assert parse_num("") is None
    assert parse_num(None) is None
    assert parse_num("abc") is None


def test_parse_month_to_month_end():
    assert parse_month("Sep-2026") == date(2026, 9, 30)
    assert parse_month("Feb-2026") == date(2026, 2, 28)
    assert parse_month("Feb-2024") == date(2024, 2, 29)  # leap year
    assert parse_month("Dec-2025") == date(2025, 12, 31)
    assert parse_month("Oct-2025") == date(2025, 10, 31)


def test_parse_month_bad():
    with pytest.raises(ValueError):
        parse_month("2026-09")
    with pytest.raises(ValueError):
        parse_month("Foo-2026")


def test_grade_slugs_cover_all_grades():
    assert set(GRADE_SLUGS) == {
        "Investment Grade", "High Yield", "Agency", "144A - IG", "144A - HY",
    }
    assert set(GRADE_SLUGS.values()) == {"ig", "hy", "agcy", "144a-ig", "144a-hy"}


def test_series_ids_match_spec():
    assert SERIES_IDS == [
        "finra-cap-ig-avgsize",
        "finra-cap-ig-total",
        "finra-cap-hy-avgsize",
        "finra-cap-hy-total",
        "finra-cap-agcy-avgsize",
        "finra-cap-agcy-total",
        "finra-cap-144a-ig-total",
        "finra-cap-144a-hy-total",
    ]


def test_parse_capped_values():
    parsed = parse_capped(_csv(SAMPLE_ROWS))
    assert len(parsed) == 2  # two months
    sep = parsed[date(2026, 9, 30)]
    assert sep["ig"]["avgsize"] == pytest.approx(12158.84)
    assert sep["ig"]["total"] == pytest.approx(343499264.21)
    assert sep["hy"]["avgsize"] == pytest.approx(4226.56)
    assert sep["agcy"]["total"] == pytest.approx(76324778.35)
    assert sep["144a-ig"]["total"] == pytest.approx(50000000.00)
    assert sep["144a-hy"]["total"] == pytest.approx(10000000.00)
    aug = parsed[date(2026, 8, 31)]
    assert aug["ig"]["total"] == pytest.approx(300000000.00)


def test_parse_capped_skips_unknown_grade():
    rows = SAMPLE_ROWS + [("Sep-2026", "Mystery Grade", "1.0", "2.0")]
    parsed = parse_capped(_csv(rows))
    assert "mystery" not in parsed[date(2026, 9, 30)]


def test_parse_capped_bad_header():
    with pytest.raises(ValueError):
        parse_capped("Foo,Bar\n1,2\n")


class _Fakes:
    def __init__(self, text: str):
        self.text = text
        self.urls: list[str] = []

    async def get_text(self, url, params=None, headers=None):
        self.urls.append(url)
        return self.text


def _run(store, fakes, **kw):
    return asyncio.run(fetch_finra_capped(store, fakes.get_text, **kw))


def test_job_stores_all_series_and_snapshot(tmp_path):
    store = Store(tmp_path / "t.db")
    fakes = _Fakes(_csv(SAMPLE_ROWS))
    assert _run(store, fakes) == "finra-capped"
    assert fakes.urls == [finra_capped.CAPPED_URL]

    ig_total = store.points("cycle:finra-cap-ig-total")
    assert ig_total[date(2026, 9, 30)] == pytest.approx(343499264.21)
    assert ig_total[date(2026, 8, 31)] == pytest.approx(300000000.00)

    ig_avg = store.points("cycle:finra-cap-ig-avgsize")
    assert ig_avg[date(2026, 9, 30)] == pytest.approx(12158.84)

    hy_total = store.points("cycle:finra-cap-hy-total")
    assert hy_total[date(2026, 9, 30)] == pytest.approx(104987829.11)

    agcy_total = store.points("cycle:finra-cap-agcy-total")
    assert agcy_total[date(2026, 9, 30)] == pytest.approx(76324778.35)

    assert store.points("cycle:finra-cap-144a-ig-total")[date(2026, 9, 30)] == pytest.approx(50000000.00)
    assert store.points("cycle:finra-cap-144a-hy-total")[date(2026, 9, 30)] == pytest.approx(10000000.00)

    # every declared series got points for both months
    for sid in SERIES_IDS:
        assert len(store.points(f"cycle:{sid}")) == 2, sid

    doc = store.doc("finra_capped")
    assert doc is not None
    assert doc.payload["as_of"] == "2026-09-30"
    assert doc.payload["grades"]["ig"]["total"] == pytest.approx(343499264.21)


def test_job_idempotent_second_run(tmp_path):
    store = Store(tmp_path / "t.db")
    fakes = _Fakes(_csv(SAMPLE_ROWS))
    _run(store, fakes)
    _run(store, fakes)  # same file again: upsert, no duplicates
    for sid in SERIES_IDS:
        assert len(store.points(f"cycle:{sid}")) == 2, sid


def test_job_empty_csv_raises(tmp_path):
    store = Store(tmp_path / "t.db")
    fakes = _Fakes("MONTH,Grade\n")
    with pytest.raises(ValueError):
        _run(store, fakes)
