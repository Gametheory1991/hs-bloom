#!/usr/bin/env python3
"""briefcheck.py — cross-check the daily briefing email against the terminal.

Parses the 8 data tables of the "Daily Chief of Staff & Multi-Asset
Briefing" email (generic header-row parsing, no hardcoded email layout),
pulls matching series from the terminal's /api/scorecard and /api/series,
and reports any metric whose absolute deviation exceeds tolerance.

Standalone: stdlib only. Runs on an operator machine, OFF Render.

Usage:
    python briefcheck.py --briefing-html PATH \
        --base-url https://os-bloom.onrender.com [--tolerance-pct N]

Exit code 0 when every mapped metric agrees within tolerance, 1 otherwise.
Stdout carries the JSON report:
    {checked_at, checked_count, mismatches: [
        {table, metric, briefing_value, terminal_value, deviation}]}
Human-readable mismatch lines go to stderr.

The JSON report can be posted to the terminal for the record:
    curl -X POST <base-url>/api/briefcheck -H 'Content-Type: application/json' -d @report.json
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser


# --------------------------------------------------------------------------
# HTML table extraction (generic: first row = headers, rest = data rows)
# --------------------------------------------------------------------------
class BriefingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict] = []
        self._heading = ""
        self._in_heading = False
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell = ""
        self._row: list[str] = []
        self._headers: list[str] | None = None
        self._rows: list[list[str]] = []
        self._table_heading = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("h1", "h2", "h3", "h4"):
            self._in_heading = True
            self._heading = ""
        elif tag == "table":
            self._in_table = True
            self._headers = None
            self._rows = []
            self._table_heading = self._heading.strip()
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell = ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3", "h4"):
            self._in_heading = False
        elif tag == "table" and self._in_table:
            self._in_table = False
            if self._headers or self._rows:
                self.tables.append(
                    {"heading": self._table_heading,
                     "headers": self._headers or [], "rows": self._rows}
                )
        elif tag == "tr" and self._in_row:
            self._in_row = False
            cells = [c.strip() for c in self._row]
            if any(cells):
                if self._headers is None:
                    self._headers = cells
                else:
                    self._rows.append(cells)
        elif tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._row.append(html.unescape(re.sub(r"\s+", " ", self._cell)).strip())

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading += data
        if self._in_cell:
            self._cell += data


# --------------------------------------------------------------------------
# Value parsing: briefing cell text -> (number, kind)
# --------------------------------------------------------------------------
_MISSING = {"", "—", "--", "-", "N/A", "n/a", "…", "...", "N/A "}


def parse_level(text: str) -> tuple[float | None, str | None]:
    """Parse a briefing 'Level' cell. Kinds: pct, bps, usd_m, mmbbl, usd, num."""
    t = re.sub(r"\(.*?\)", "", text or "").strip()
    if t in _MISSING:
        return None, None
    m = re.search(r"([+-]?[\d,]*\.?\d+)\s*bps?", t, re.I)
    if m:
        return float(m.group(1).replace(",", "")), "bps"
    m = re.search(r"([+-]?[\d,]*\.?\d+)\s*%", t)
    if m:
        return float(m.group(1).replace(",", "")), "pct"
    m = re.search(r"\$\s*([+-]?[\d,]*\.?\d+)\s*([BMK])\b", t, re.I)
    if m:
        v = float(m.group(1).replace(",", ""))
        suf = m.group(2).upper()
        return (v * 1000 if suf == "B" else v / 1000 if suf == "K" else v), "usd_m"
    m = re.search(r"\$\s*([+-]?[\d,]*\.?\d+)", t)
    if m:
        return float(m.group(1).replace(",", "")), "usd"
    m = re.search(r"([+-]?[\d,]*\.?\d+)\s*MMBbls", t, re.I)
    if m:
        return float(m.group(1).replace(",", "")), "mmbbl"
    m = re.search(r"([+-]?[\d,]*\.?\d+)", t)
    if m:
        try:
            return float(m.group(1).replace(",", "")), "num"
        except ValueError:
            return None, None
    return None, None


def normalize_label(text: str) -> str:
    t = re.sub(r"\(.*?\)", "", text or "").strip().lower()
    return re.sub(r"\s+", " ", t)


# --------------------------------------------------------------------------
# Metric mapping: briefing label -> (source, key, expect_unit, tol_mode, tol)
#   source: "scorecard" (row id -> last) or "series" (/api/series/<id> last)
#   expect_unit: pct | bps | usd_m | mmbbl | num   (briefing value converted)
#   tol_mode: "abs" | "rel"
# --------------------------------------------------------------------------
# fmt: (source, key, expect, tol_mode, tol)
_M = {
    # Table 1: rates & curve
    "ust 1m": ("scorecard", "us-1m-bill", "pct", "abs", 0.10),
    "ust 6m": ("scorecard", "us-6m-bill", "pct", "abs", 0.10),
    "ust 1y": ("scorecard", "us-1y-bill", "pct", "abs", 0.10),
    "ust 10y": ("scorecard", "us10y", "pct", "abs", 0.10),
    "10y-2y spread": ("scorecard", "t10y2y", "pct", "abs", 0.10),
    "10y-3m spread": ("scorecard", "t10y3m", "pct", "abs", 0.10),
    "us ig oas": ("scorecard", "ig-oas", "bps", "abs", 5.0),
    "us hy oas": ("scorecard", "hy-oas", "bps", "abs", 5.0),
    "5y breakeven": ("series", "us-5y-breakeven", "pct", "abs", 0.10),
    "10y breakeven": ("series", "breakeven-10y", "pct", "abs", 0.10),
    "sofr": ("series", "sofr", "pct", "abs", 0.10),
    # Table 2: plumbing
    "treasury general account": ("scorecard", "tga", "usd_m", "rel", 0.05),
    "tga": ("scorecard", "tga", "usd_m", "rel", 0.05),
    "fed on rrp facility take-up": ("series", "rrp-on", "num", "rel", 0.05),
    "on rrp take-up": ("series", "rrp-on", "num", "rel", 0.05),
    "foreign official custody": ("series", "cust-ust", "usd_m", "rel", 0.05),
    "spr crude stocks": ("scorecard", "spr-stocks", "mmbbl", "rel", 0.05),
    "strategic petroleum reserve": ("scorecard", "spr-stocks", "mmbbl", "rel", 0.05),
    "commercial crude oil": ("scorecard", "comm-crude-stocks", "mmbbl", "rel", 0.05),
    # Table 3: vol surface
    "cboe vix index": ("scorecard", "vix", "num", "rel", 0.01),
    "vix": ("scorecard", "vix", "num", "rel", 0.01),
    # Table 4: financial conditions
    "chicago fed nfci": ("series", "nfci", "num", "abs", 0.10),
    "nfci": ("series", "nfci", "num", "abs", 0.10),
    "st. louis fed stress": ("scorecard", "stlfsi", "num", "abs", 0.10),
    "stlfs": ("scorecard", "stlfsi", "num", "abs", 0.10),
    "ny fed weekly economic index": ("scorecard", "wei", "num", "rel", 0.05),
    "weekly economic index": ("scorecard", "wei", "num", "rel", 0.05),
    "us 10y - german bund 10y spread": ("scorecard", "spr-us-de", "pct", "abs", 0.10),
    "us 10y - japan jgb 10y spread": ("scorecard", "spr-us-jp", "pct", "abs", 0.10),
    "5y5y forward inflation expectation": ("series", "infl-5y5y", "pct", "abs", 0.10),
    "copper / gold ratio": ("scorecard", "cu-au-ratio", "num", "rel", 0.02),
    "hy oas / ig oas ratio": ("scorecard", "hy-ig-ratio", "num", "rel", 0.02),
    "30-year mortgage rate": ("series", "us-mortgage-30y", "pct", "abs", 0.10),
    # Table 5: ETFs (scorecard rows are etf-*; a few via /api/series)
    "sgov": ("scorecard", "etf-sgov", "num", "rel", 0.01),
    "bil": ("scorecard", "etf-bil", "num", "rel", 0.01),
    "vgsh": ("scorecard", "etf-vgsh", "num", "rel", 0.01),
    "vgit": ("scorecard", "etf-vgit", "num", "rel", 0.01),
    "govt": ("scorecard", "etf-govt", "num", "rel", 0.01),
    "edv": ("scorecard", "etf-edv", "num", "rel", 0.01),
    "tlt": ("series", "tlt", "num", "rel", 0.01),
    "mbb": ("series", "mbb-us", "num", "rel", 0.01),
    "lqd": ("scorecard", "lqd", "num", "rel", 0.01),
    "hyg": ("scorecard", "hyg", "num", "rel", 0.01),
    "emb": ("scorecard", "etf-emb", "num", "rel", 0.01),
    "lemb": ("scorecard", "etf-lemb", "num", "rel", 0.01),
    "xlk": ("scorecard", "etf-xlk", "num", "rel", 0.01),
    "xlf": ("scorecard", "etf-xlf", "num", "rel", 0.01),
    "xle": ("scorecard", "etf-xle", "num", "rel", 0.01),
    "xli": ("scorecard", "etf-xli", "num", "rel", 0.01),
    "xlv": ("scorecard", "etf-xlv", "num", "rel", 0.01),
    "kre": ("scorecard", "etf-kre", "num", "rel", 0.01),
    "smh": ("scorecard", "etf-smh", "num", "rel", 0.01),
    "emb / lemb ratio": ("scorecard", "emb-lemb-ratio", "num", "rel", 0.02),
}

# Tables 6-8 are intentionally unmapped: futures positioning z-scores have no
# terminal equivalent, auction dynamics are event-specific (not current
# levels), and the econ calendar is events, not values.
SKIP_NOTE = "no terminal equivalent (documented skip)"


def to_canonical(value: float, kind: str | None, expect: str) -> float | None:
    """Convert a briefing value to the mapping's expected unit."""
    if value is None or kind is None:
        return None
    if expect == "pct":
        return value / 100.0 if kind == "bps" else value if kind == "pct" else None
    if expect == "bps":
        return value * 100.0 if kind == "pct" else value if kind == "bps" else None
    if expect == "usd_m":
        return value if kind in ("usd_m",) else None
    if expect == "mmbbl":
        return value if kind == "mmbbl" else None
    if expect == "num":
        # usd levels (ETF prices) compare as plain numbers
        return value if kind in ("num", "usd") else None
    return None


# --------------------------------------------------------------------------
# Terminal fetching
# --------------------------------------------------------------------------
def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "os-bloom-briefcheck/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def terminal_values(base_url: str) -> dict[tuple[str, str], float | None]:
    """{(source, key): latest native value} for every mapped metric."""
    base = base_url.rstrip("/")
    out: dict[tuple[str, str], float | None] = {}
    scorecard = fetch_json(f"{base}/api/scorecard")
    rows = {r["id"]: r.get("last") for r in scorecard.get("rows", [])}
    for _label, (source, key, _e, _m, _t) in _M.items():
        if source != "scorecard" or (source, key) in out:
            continue
        v = rows.get(key)
        out[(source, key)] = float(v) if isinstance(v, (int, float)) else None
    for _label, (source, key, _e, _m, _t) in _M.items():
        if source != "series" or (source, key) in out:
            continue
        try:
            data = fetch_json(f"{base}/api/series/{key}")
            pts = data.get("points", [])
            out[(source, key)] = float(pts[-1][1]) if pts else None
        except Exception:  # noqa: BLE001 — one bad series must not kill the run
            out[(source, key)] = None
    return out


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------
def check_table(table: dict, tvals: dict, rel_override: float | None) -> tuple[int, list[dict]]:
    headers = [h.lower() for h in table["headers"]]
    try:
        level_idx = next(i for i, h in enumerate(headers) if "level" in h)
    except StopIteration:
        level_idx = 1 if len(headers) > 1 else 0
    checked, mismatches = 0, []
    for row in table["rows"]:
        if not row or level_idx >= len(row):
            continue
        label = normalize_label(row[0])
        mapping = _M.get(label)
        if mapping is None:
            continue
        source, key, expect, tol_mode, tol = mapping
        bval, bkind = parse_level(row[level_idx])
        bcanon = to_canonical(bval, bkind, expect)
        tval = tvals.get((source, key))
        if bcanon is None or tval is None:
            continue
        # terminal native -> expected unit
        if expect == "bps" and source == "scorecard" and key in ("ig-oas", "hy-oas"):
            tcanon = tval * 100.0  # terminal stores OAS in %
        else:
            tcanon = tval
        if tol_mode == "abs":
            dev = abs(bcanon - tcanon)
            bad = dev > tol
            dev_repr = round(dev, 4)
        else:
            rtol = rel_override if rel_override is not None else tol
            denom = abs(tcanon) if abs(tcanon) > 1e-9 else 1e-9
            dev = abs(bcanon - tcanon) / denom
            bad = dev > rtol
            dev_repr = round(dev * 100, 3)  # percent
        checked += 1
        if bad:
            mismatches.append({
                "table": table["heading"] or "unnamed table",
                "metric": row[0].strip(),
                "briefing_value": round(bcanon, 4),
                "terminal_value": round(tcanon, 4),
                "deviation": dev_repr,
                "unit": expect,
            })
    return checked, mismatches


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-check the daily briefing email against the os-bloom terminal.")
    ap.add_argument("--briefing-html", required=True, help="path to the briefing email HTML file")
    ap.add_argument("--base-url", required=True, help="terminal base URL, e.g. https://os-bloom.onrender.com")
    ap.add_argument("--tolerance-pct", type=float, default=None,
                    help="override relative tolerance (percent) for rel-mode checks")
    args = ap.parse_args(argv)

    with open(args.briefing_html, encoding="utf-8", errors="replace") as f:
        parser = BriefingParser()
        parser.feed(f.read())

    rel_override = args.tolerance_pct / 100.0 if args.tolerance_pct is not None else None
    tvals = terminal_values(args.base_url)
    checked_count, mismatches = 0, []
    for table in parser.tables:
        c, m = check_table(table, tvals, rel_override)
        checked_count += c
        mismatches.extend(m)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "checked_count": checked_count,
        "mismatches": mismatches,
    }
    print(json.dumps(report, indent=2))
    for mm in mismatches:
        unit = {"pct": "pp", "bps": "bps"}.get(mm["unit"], "")
        print(f"MISMATCH [{mm['table']}] {mm['metric']}: briefing={mm['briefing_value']} "
              f"terminal={mm['terminal_value']} dev={mm['deviation']}{unit}", file=sys.stderr)
    if not mismatches:
        print(f"OK: {checked_count} metrics agree within tolerance.", file=sys.stderr)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
