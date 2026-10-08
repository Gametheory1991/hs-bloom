"""AAII investor sentiment survey — weekly bull-bear spread.

The survey ships as a legacy .xls with a header row, weekly data rows
(date, bullish, neutral, bearish as fractions) and footer summary rows.
Any row whose first cell isn't an Excel date is skipped; values <= 1.5 are
treated as fractions and scaled to percentage points.
"""
from __future__ import annotations

from datetime import date

import xlrd

from collector.http import USER_AGENT, GetBytes

URL = "https://www.aaii.com/files/surveys/sentiment.xls"
# aaii.com's WAF 403s a bare product token (e.g. "hs-bloom/0.1") but serves the
# file to the standard `product/version (comment)` form. Honest, not disguised.
HEADERS = {"User-Agent": USER_AGENT}


def parse_sentiment(content: bytes) -> list[tuple[date, float]]:
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    out: list[tuple[date, float]] = []
    for i in range(sheet.nrows):
        row = sheet.row(i)
        if len(row) < 4 or row[0].ctype != xlrd.XL_CELL_DATE:
            continue  # header, footer, or blank spacer row
        try:
            d = xlrd.xldate_as_datetime(row[0].value, book.datemode).date()
            bull, bear = float(row[1].value), float(row[3].value)
        except (TypeError, ValueError):
            continue
        if abs(bull) <= 1.5 and abs(bear) <= 1.5:  # fractions -> percentage points
            bull, bear = bull * 100, bear * 100
        out.append((d, round(bull - bear, 2)))
    if not out:
        raise ValueError("aaii sentiment sheet contained no usable rows")
    return out


async def fetch_spread(get_bytes: GetBytes) -> list[tuple[date, float]]:
    return parse_sentiment(await get_bytes(URL, headers=HEADERS))
