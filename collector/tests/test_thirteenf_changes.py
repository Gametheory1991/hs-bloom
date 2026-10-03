"""Tests for collector.fetchers.thirteenf.diff_holdings (13F change detection).

diff_holdings is a pure function: no HTTP, no store, no EDGAR. All holdings
lists below are mocked; values are USD (as produced by parse_holdings_xml).
"""
from __future__ import annotations

import unittest

from collector.fetchers.thirteenf import TOP_N, diff_holdings


def h(issuer: str, value_usd: int, cusip: str = "000000000") -> dict:
    return {"issuer": issuer, "cusip": cusip, "value_usd": value_usd,
            "shares": "0", "share_class": "COM"}


class DiffHoldingsTest(unittest.TestCase):
    def test_new_position_counts_as_positive_flow(self):
        prev = [h("AAPL", 1_000_000)]
        curr = [h("AAPL", 1_000_000), h("NVDA", 500_000)]
        d = diff_holdings(prev, curr)
        self.assertEqual(d["net_flow_usd"], 500_000)
        self.assertEqual(d["n_new"], 1)
        self.assertEqual(d["n_closed"], 0)
        new = [c for c in d["changes"] if c["kind"] == "new"]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["issuer"], "NVDA")
        self.assertEqual(new[0]["delta_usd"], 500_000)
        self.assertEqual(new[0]["value_usd"], 500_000)

    def test_closed_position_counts_as_negative_flow(self):
        prev = [h("AAPL", 1_000_000), h("TSLA", 250_000)]
        curr = [h("AAPL", 1_000_000)]
        d = diff_holdings(prev, curr)
        self.assertEqual(d["net_flow_usd"], -250_000)
        self.assertEqual(d["n_closed"], 1)
        closed = [c for c in d["changes"] if c["kind"] == "closed"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["issuer"], "TSLA")
        self.assertEqual(closed[0]["delta_usd"], -250_000)
        self.assertEqual(closed[0]["value_usd"], 0)

    def test_increased_and_decreased(self):
        prev = [h("AAPL", 1_000_000), h("MSFT", 800_000)]
        curr = [h("AAPL", 1_200_000), h("MSFT", 700_000)]
        d = diff_holdings(prev, curr)
        self.assertEqual(d["n_increased"], 1)
        self.assertEqual(d["n_decreased"], 1)
        self.assertEqual(d["net_flow_usd"], 200_000 - 100_000)
        kinds = {c["issuer"]: c["kind"] for c in d["changes"]}
        self.assertEqual(kinds, {"AAPL": "increased", "MSFT": "decreased"})
        deltas = {c["issuer"]: c["delta_usd"] for c in d["changes"]}
        self.assertEqual(deltas, {"AAPL": 200_000, "MSFT": -100_000})

    def test_net_flow_math_mixed(self):
        # +300k new, -100k closed, +50k add, -25k trim => +225k
        prev = [h("A", 1_000_000), h("B", 100_000), h("C", 500_000)]
        curr = [h("A", 1_050_000), h("C", 475_000), h("D", 300_000)]
        d = diff_holdings(prev, curr)
        self.assertEqual(d["net_flow_usd"], 225_000)
        self.assertEqual((d["n_new"], d["n_closed"], d["n_increased"], d["n_decreased"]),
                         (1, 1, 1, 1))

    def test_no_changes(self):
        prev = [h("AAPL", 1_000_000), h("MSFT", 800_000)]
        curr = [h("AAPL", 1_000_000), h("MSFT", 800_000)]
        d = diff_holdings(prev, curr)
        self.assertEqual(d["net_flow_usd"], 0)
        self.assertEqual(d["changes"], [])
        self.assertEqual((d["n_new"], d["n_closed"], d["n_increased"], d["n_decreased"]),
                         (0, 0, 0, 0))

    def test_changes_sorted_by_abs_delta_and_capped(self):
        prev = [h(f"P{i:02d}", 1_000_000) for i in range(20)]
        curr = [h(f"P{i:02d}", 1_000_000 + (i + 1) * 10_000) for i in range(20)]
        d = diff_holdings(prev, curr)
        self.assertEqual(len(d["changes"]), TOP_N)
        deltas = [abs(c["delta_usd"]) for c in d["changes"]]
        self.assertEqual(deltas, sorted(deltas, reverse=True))
        # biggest mover first: P19 delta = 200k
        self.assertEqual(d["changes"][0]["issuer"], "P19")

    def test_empty_issuer_falls_back_to_cusip_key(self):
        prev = [h("", 400_000, cusip="111111111")]
        curr = [h("", 450_000, cusip="111111111")]
        d = diff_holdings(prev, curr)
        self.assertEqual(d["n_increased"], 1)
        self.assertEqual(d["net_flow_usd"], 50_000)

    def test_unchanged_position_not_listed(self):
        prev = [h("AAPL", 1_000_000)]
        curr = [h("AAPL", 1_000_000)]
        d = diff_holdings(prev, curr)
        self.assertNotIn("AAPL", [c["issuer"] for c in d["changes"]])


if __name__ == "__main__":
    unittest.main()
