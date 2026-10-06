"""Treasury International Capital — foreign FLOWS in U.S. Treasuries (Table 1).

Source (verified live 2026-10-05, current through 2026-07):
  https://ticdata.treasury.gov/Publish/slt_table1.txt
Tab-delimited, monthly, ~6-week lag, values in $millions, one row per
(country, YYYY-MM). Each security type has a (holdings, net, valchg) triple;
we take the U.S. Treasuries triple:
  for_lt_treas_pos    — holdings (position), $mn
  for_lt_treas_net    — NET U.S. sales to foreigners, $mn  <-- the flow
  for_lt_treas_valchg — valuation change, $mn

SIGN CONVENTION (per TIC: "A positive number for net U.S. sales to foreigners
denotes an increase in a foreign position"):
  net > 0  =>  foreigners were NET BUYERS of UST that month
  net < 0  =>  foreigners were NET SELLERS of UST that month

Why this instead of differencing Table 5 holdings: month-to-month holdings
changes mix true transactions with price (valuation) changes. Table 1 splits
the two, so `net` is the actual buying/selling flow Harry asked for.

METHODOLOGY (reportable):
  - monthly net per country: for_lt_treas_net as published, $mn
  - trailing 3M / 12M net: sum of the last 3 / 12 monthly nets (flow sums,
    not averages), $mn. A negative T12M means foreigners sold more UST than
    they bought over the past year.
"""
from __future__ import annotations

from datetime import date

from collector.config import TicCfg
from collector.fetchers.tic import GRAND_TOTAL, fetch_tic, slug
from collector.http import GetText
from collector.store import Store

URL = "https://ticdata.treasury.gov/Publish/slt_table1.txt"

NET_COL = "for_lt_treas_net"
HOLD_COL = "for_lt_treas_pos"
VALCHG_COL = "for_lt_treas_valchg"


def _num(raw: str) -> float | None:
    raw = raw.strip().replace(",", "")
    if not raw or raw in ("n.a.", "--"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_flows_table(
    text: str, countries: list[str]
) -> dict[str, dict[str, list[tuple[date, float]]]]:
    """Parse TIC Table 1.

    Returns {series_kind: {country_name: [(date, value), ...]}} with kinds
    "net" | "hold" | "valchg". Dates are the first of each YYYY-MM month.
    Column positions are resolved from the mnemonic header row
    (country/country_code/date/for_lt_treas_pos/...) — never hardcoded.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    mnem_idx = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("country\tcountry_code\tdate\t")
    )
    cols = lines[mnem_idx].split("\t")
    try:
        i_net = cols.index(NET_COL)
        i_hold = cols.index(HOLD_COL)
        i_val = cols.index(VALCHG_COL)
    except ValueError as exc:
        raise ValueError(f"tic Table 1 missing expected column: {exc}") from exc
    wanted = set(countries) | {GRAND_TOTAL}
    out: dict[str, dict[str, list[tuple[date, float]]]] = {
        "net": {},
        "hold": {},
        "valchg": {},
    }
    for line in lines[mnem_idx + 1 :]:
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0].strip()
        if name not in wanted or len(parts) <= max(i_net, i_hold, i_val):
            continue
        try:
            d = date(int(parts[2][:4]), int(parts[2][5:7]), 1)
        except (ValueError, IndexError):
            continue
        for kind, idx in (("net", i_net), ("hold", i_hold), ("valchg", i_val)):
            v = _num(parts[idx])
            if v is not None:
                out[kind].setdefault(name, []).append((d, v))
    if GRAND_TOTAL not in out["net"]:
        raise ValueError("tic Table 1 contained no Grand Total net row")
    for kind in out:
        for pts in out[kind].values():
            pts.sort(key=lambda p: p[0])
    return out


def derive_flow_sums(store: Store, slugs: list[str]) -> None:
    """Trailing 3M / 12M sums of monthly net flows (compute-only, no I/O).

    Reads cycle:tic-flows-treas-net-<slug>, writes
    cycle:tic-flows-treas-net-t3m-<slug> / -t12m-<slug> ($mn).
    A trailing window is written only when all its months are present.
    """
    for s in slugs:
        pts = sorted(store.points(f"cycle:tic-flows-treas-net-{s}").items())
        if len(pts) < 3:
            continue
        vals = [v for _, v in pts]
        t3 = [(pts[i][0], sum(vals[i - 2 : i + 1])) for i in range(2, len(pts))]
        store.upsert_points(f"cycle:tic-flows-treas-net-t3m-{s}", t3)
        if len(pts) >= 12:
            t12 = [
                (pts[i][0], sum(vals[i - 11 : i + 1]))
                for i in range(11, len(pts))
            ]
            store.upsert_points(f"cycle:tic-flows-treas-net-t12m-{s}", t12)


async def fetch_tic_flows(cfg: TicCfg, store: Store, get_text: GetText) -> str:
    """Weekly job (monthly data): per-country Treasury flow triples.

    Stores cycle:tic-flows-treas-{net,hold,valchg}-<slug> ($mn) then derives
    trailing 3M/12M net sums. Runs alongside the Table 5 holdings job.
    """
    data = parse_flows_table(await get_text(URL), cfg.countries)
    slugs: list[str] = []
    for kind, prefix in (("net", "net"), ("hold", "hold"), ("valchg", "valchg")):
        for name, pts in data[kind].items():
            s = slug(name)
            store.upsert_points(f"cycle:tic-flows-treas-{prefix}-{s}", pts)
            if kind == "net":
                slugs.append(s)
    derive_flow_sums(store, slugs)
    return "tic_flows"


async def fetch_tic_all(cfg: TicCfg, store: Store, get_text: GetText) -> str:
    """Combined TIC job: Table 5 holdings (existing) + Table 1 flows."""
    await fetch_tic(cfg, store, get_text)
    return await fetch_tic_flows(cfg, store, get_text)
