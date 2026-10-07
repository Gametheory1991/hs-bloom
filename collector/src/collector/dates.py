"""Shared date helpers."""
from __future__ import annotations

import calendar
from datetime import date


def quarter_end(year: int, q: int) -> date:
    """Last calendar day of quarter q (1-4) in year."""
    month = q * 3
    return date(year, month, calendar.monthrange(year, month)[1])
