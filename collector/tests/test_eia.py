"""Tests for collector.fetchers.eia (EIA WPSR table1.csv crude stocks).

HTTP is never touched: parse_table1 takes raw bytes (cp1252, as served),
and fetch_wpsr takes an injected get_bytes callable.
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import date

from collector.fetchers.eia import fetch_wpsr, parse_table1

SAMPLE = (
    '"STUB_1","9/25/26","9/18/26","Difference","Percent Change","9/26/25","Difference","Percent Change"\r\n'
    '"Crude Oil","711.087","710.950","0.137","0.000","823.246","-112.159","-13.600"\r\n'
    '"Commercial (Excluding SPR)","427.320","426.398","0.922","0.200","416.546","10.774","2.600"\r\n'
    '"Strategic Petroleum Reserve (SPR)","283.767","284.552","-0.785","-0.300","406.700","-122.933","-30.200"\r\n'
).encode("cp1252")


def _get_bytes(payload: bytes):
    async def fake(url: str) -> bytes:
        _get_bytes.url = url
        return payload
    _get_bytes.url = None
    return fake


class ParseTable1Test(unittest.TestCase):
    def test_spr_and_commercial_values(self):
        out = parse_table1(SAMPLE)
        self.assertEqual(out["spr"], [(date(2026, 9, 25), 283.767)])
        self.assertEqual(out["commercial"], [(date(2026, 9, 25), 427.320)])

    def test_values_are_mmbbls_as_printed(self):
        # No scaling: the CSV prints millions of barrels directly.
        out = parse_table1(SAMPLE)
        self.assertGreater(out["spr"][0][1], 200)
        self.assertLess(out["spr"][0][1], 1000)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_table1(b"")

    def test_missing_rows_raise(self):
        bad = '"STUB_1","9/25/26"\r\n"Crude Oil","711.087","710.950"\r\n'.encode("cp1252")
        with self.assertRaises(ValueError):
            parse_table1(bad)

    def test_bad_header_raises(self):
        bad = '"foo","bar"\r\n'.encode("cp1252")
        with self.assertRaises(ValueError):
            parse_table1(bad)


class FetchWpsrTest(unittest.TestCase):
    def test_spr_dispatch(self):
        pts = asyncio.run(fetch_wpsr("spr", _get_bytes(SAMPLE)))
        self.assertEqual(pts, [(date(2026, 9, 25), 283.767)])
        self.assertIn("table1.csv", _get_bytes.url)

    def test_commercial_dispatch(self):
        pts = asyncio.run(fetch_wpsr("commercial", _get_bytes(SAMPLE)))
        self.assertEqual(pts, [(date(2026, 9, 25), 427.320)])

    def test_unknown_which_raises(self):
        with self.assertRaises(ValueError):
            asyncio.run(fetch_wpsr("opec", _get_bytes(SAMPLE)))


if __name__ == "__main__":
    unittest.main()
