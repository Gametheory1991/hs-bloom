"""Tests for TIC Table 1 foreign UST flows (collector/fetchers/tic_flows.py).

Uses the live file format captured 2026-10-05 (slt_table1.txt); key cells:
  Grand Total 2026-07: net -3560.0, hold 7783259.0 ($mn)
  Japan       2026-07: net -8846.0 ($mn)
Sign convention: net > 0 => foreigners net bought UST that month.
"""
from datetime import date

import pytest

from collector.fetchers.tic_flows import (
    derive_flow_sums,
    fetch_tic_flows,
    parse_flows_table,
)
from collector.store import Store

SAMPLE = """Table 1: U.S. Long-Term Securities Held by Foreign Residents
Millions of dollars
Country\tCountry Code\tDate\tHoldings\tNet U.S. Sales\tValuation Change\tHoldings\tNet U.S. Sales\tValuation Change
country\tcountry_code\tdate\tfor_lt_total_pos\tfor_lt_total_net\tfor_lt_total_valchg\tfor_lt_treas_pos\tfor_lt_treas_net\tfor_lt_treas_valchg
Japan\t42609\t2026-07\t2998094\t-6454\t-34194\t1023754\t-8846\t-12120
Japan\t42609\t2026-06\t3000000\t1000\t-2000\t1032600\t5000\t-3000
Grand Total\t99996\t2026-07\t38820547\t40616\t-527089\t7783259\t-3560\t-101562
Grand Total\t99996\t2026-06\t39195363\t208899\t-218034\t7872402\t6207\t-39252
"""

COUNTRIES = ["Japan"]


def test_parse_net_sign_and_values():
    data = parse_flows_table(SAMPLE, COUNTRIES)
    gt = dict(data["net"]["Grand Total"])
    jp = dict(data["net"]["Japan"])
    assert gt[date(2026, 7, 1)] == -3560.0  # net sold
    assert gt[date(2026, 6, 1)] == 6207.0  # net bought
    assert jp[date(2026, 7, 1)] == -8846.0
    assert dict(data["hold"]["Grand Total"])[date(2026, 7, 1)] == 7783259.0
    assert dict(data["valchg"]["Japan"])[date(2026, 7, 1)] == -12120.0


def test_parse_resolves_columns_from_mnemonics_not_positions():
    # swap the Treasuries triple order in BOTH the mnemonic row and the data
    # rows — parse must follow the names, not hardcoded indices
    def swap3(line):
        p = line.split("\t")
        p[6], p[7] = p[7], p[6]
        return "\t".join(p)

    lines = SAMPLE.split("\n")
    swapped = "\n".join(
        swap3(ln) if ln.startswith(("country\t", "Japan\t", "Grand Total\t")) else ln
        for ln in lines
    )
    data = parse_flows_table(swapped, COUNTRIES)
    gt = dict(data["net"]["Grand Total"])
    assert gt[date(2026, 7, 1)] == -3560.0
    assert dict(data["hold"]["Grand Total"])[date(2026, 7, 1)] == 7783259.0


def test_parse_requires_grand_total():
    with pytest.raises(ValueError, match="Grand Total"):
        parse_flows_table(
            SAMPLE.replace("Grand Total", "Somewhere Else"), COUNTRIES
        )


def test_derive_trailing_sums():
    store = Store(":memory:")
    pts = [(date(2026, m, 1), float(m * 1000)) for m in range(1, 13)]
    store.upsert_points("cycle:tic-flows-treas-net-japan", pts)
    derive_flow_sums(store, ["japan"])
    t3 = store.points("cycle:tic-flows-treas-net-t3m-japan")
    t12 = store.points("cycle:tic-flows-treas-net-t12m-japan")
    assert t3[date(2026, 12, 1)] == 10000 + 11000 + 12000
    assert t3[date(2026, 3, 1)] == 1000 + 2000 + 3000
    assert t12[date(2026, 12, 1)] == sum(m * 1000 for m in range(1, 13))
    assert date(2026, 11, 1) not in t12  # 12M needs 12 months


def test_derive_skips_short_history():
    store = Store(":memory:")
    store.upsert_points(
        "cycle:tic-flows-treas-net-japan",
        [(date(2026, 7, 1), 1.0), (date(2026, 6, 1), 2.0)],
    )
    derive_flow_sums(store, ["japan"])
    assert store.points("cycle:tic-flows-treas-net-t3m-japan") == {}


@pytest.mark.asyncio
async def test_fetch_job_glue():
    from types import SimpleNamespace

    store = Store(":memory:")
    cfg = SimpleNamespace(countries=["Japan"])

    async def fake_get_text(url):
        assert "slt_table1.txt" in url
        return SAMPLE

    result = await fetch_tic_flows(cfg, store, fake_get_text)
    assert result == "tic_flows"
    pts = store.points("cycle:tic-flows-treas-net-grand_total")
    assert pts[date(2026, 7, 1)] == -3560.0
    # derive ran inside the job
    assert store.points("cycle:tic-flows-treas-net-t3m-japan") == {}
    assert store.points("cycle:tic-flows-treas-hold-japan")
