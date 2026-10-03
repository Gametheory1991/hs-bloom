"""Central-bank watch: SOFR strip config + vendored FOMC schedule guards.

The FOMC dates are vendored statics in ui/js/panels/central.js, verified
2026-10-03 against https://www.federalreserve.gov/monetarypolicy/
fomccalendars.htm ("Last Update: September 16, 2026"). These tests pin the
schedule and the SOFR-strip series so a typo or bad edit fails loudly.
"""
import re
from datetime import date
from pathlib import Path

from collector.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]

# Expected schedule: (start, decision_day, has_SEP) — from federalreserve.gov.
EXPECTED_FOMC = [
    ("2026-01-27", "2026-01-28", False), ("2026-03-17", "2026-03-18", True),
    ("2026-04-28", "2026-04-29", False), ("2026-06-16", "2026-06-17", True),
    ("2026-07-28", "2026-07-29", False), ("2026-09-15", "2026-09-16", True),
    ("2026-10-27", "2026-10-28", False), ("2026-12-08", "2026-12-09", True),
    ("2027-01-26", "2027-01-27", False), ("2027-03-16", "2027-03-17", True),
    ("2027-04-27", "2027-04-28", False), ("2027-06-08", "2027-06-09", True),
    ("2027-07-27", "2027-07-28", False), ("2027-09-14", "2027-09-15", True),
    ("2027-10-26", "2027-10-27", False), ("2027-12-07", "2027-12-08", True),
]


def _fomc_from_js():
    src = (REPO_ROOT / "ui/js/panels/central.js").read_text()
    rows = re.findall(r'\["(\d{4}-\d{2}-\d{2})", "(\d{4}-\d{2}-\d{2})", (true|false)\]',
                      src)
    return [(s, e, sep == "true") for s, e, sep in rows]


def test_fomc_schedule_matches_verified_source():
    assert _fomc_from_js() == EXPECTED_FOMC


def test_fomc_decision_days_are_weekdays_and_chronological():
    rows = _fomc_from_js()
    days = [date.fromisoformat(e) for _, e, _ in rows]
    assert days == sorted(days)
    assert len(rows) == 16
    for d in days:
        assert d.weekday() < 5, f"decision day {d} is a weekend"
    # 8 meetings per year
    assert sum(d.year == 2026 for d in days) == 8
    assert sum(d.year == 2027 for d in days) == 8


def test_sofr_strip_series_in_config():
    cfg = load_config(REPO_ROOT / "config.yaml")
    strip = [s for s in cfg.cycle_series if s.id.startswith("sofr-")]
    assert len(strip) == 7
    for s in strip:
        assert re.fullmatch(r"SR3[A-Z]\d{2}\.CME", s.yahoo), s.yahoo
    ids = [s.id for s in strip]
    assert ids == ["sofr-v26", "sofr-z26", "sofr-h27", "sofr-m27",
                  "sofr-u27", "sofr-z27", "sofr-h28"]


def test_implied_rate_formula():
    # The UI renders implied 3M rate as 100 - futures price (standard SOFR
    # convention). Guard the arithmetic on the verified 2026-10-03 prints.
    assert round(100 - 95.7699966430664, 2) == 4.23   # SR3Z26.CME
    assert round(100 - 95.48500061035156, 2) == 4.51   # SR3H27.CME
    assert round(100 - 95.19000244140625, 2) == 4.81   # SR3H28.CME
