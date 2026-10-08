"""IMF chart panels — prompt §15.

Reads two READ-ONLY Google Sheets via the Google Sheets API and builds five
panels from free live sources:

  * P1 Global yield curves (IMF-01) — US FRED CMT + Japan MoF JGB CSV;
    Germany 10Y point only (monthly OECD MEI via FRED; the registry's
    Banque-de-France-via-DBnomics route was verified unavailable 2026-10-08).
  * P2 Qatar LNG / Hormuz (IMF-02) — IMF PortWatch ArcGIS chokepoint series
    as a PROXY (Kpler is paid); digitized Kpler values shown grey beside it.
  * P3 AI value chain (IMF-03) — static digitized bubble data, Q tag.
  * P4 AI debt issuance (IMF-04) — digitized bars, 2026:H2 hatched estimate.
  * P5 Euro area spreads (IMF-05) — monthly OECD MEI 10Y benchmarks via FRED,
    country minus Germany, DERIVED.

Auth: a Google service-account JSON key in env var GOOGLE_SERVICE_ACCOUNT_JSON
(the two sheets must be shared read-only with the service account's
client_email). google-auth signs the OAuth2 JWT offline; the exchange and the
Sheets REST GET go through httpx. No google-api-python-client needed.

Degradation: with the credential absent (or google-auth missing), every panel
reports "source not connected — IMF panels need a Google service account" and
nothing raises.

RULE (prompt §15): DIGITIZED values are grey reference ONLY. They never enter
scores, percentiles, velocity or composites. Every panel carries source,
as-of and tag. Live-vs-digitized tolerance comparisons are reported per
reading; a miss produces a "differs from IMF chart" note and both values are
kept.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime

import httpx

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- sheets ---

REGISTRY_SHEET_ID = "1Tp0W8OnDVrp0GxmcgQBoqy73BKYK1POR3E1XxfWxsYo"
DIGITIZED_SHEET_ID = "1KF2h9Xv5-MpMG8NI5KjgHLIFnAf_uHCSiK3qapY6mkI"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SA_ENV_VAR = "GOOGLE_SERVICE_ACCOUNT_JSON"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

NOT_CONNECTED = (
    "source not connected — IMF panels need a Google service account "
    f"(env var {SA_ENV_VAR}); see the registry sheet share step"
)

DOC_KEY = "imf_panels"


class SheetsError(RuntimeError):
    """Sheets unavailable: no credential, no library, or an API failure."""


def _google_auth_module():
    try:
        from google.oauth2 import service_account  # type: ignore

        return service_account
    except ImportError:
        return None


async def _service_account_token() -> str:
    """OAuth2 access token for the Sheets readonly scope.

    Raises SheetsError with the reason when the credential is absent, the
    library is missing, the JSON is invalid, or Google rejects the exchange.
    """
    raw = os.environ.get(SA_ENV_VAR, "").strip()
    if not raw:
        raise SheetsError(f"{SA_ENV_VAR} is not set")
    sa_mod = _google_auth_module()
    if sa_mod is None:
        raise SheetsError("google-auth is not installed; cannot sign the service-account JWT")
    try:
        info = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SheetsError(f"{SA_ENV_VAR} is not valid JSON: {exc}") from exc
    try:
        creds = sa_mod.Credentials.from_service_account_info(info, scopes=[SHEETS_SCOPE])
        assertion = creds._make_authorization_grant_assertion().decode()
    except Exception as exc:  # noqa: BLE001 — malformed key material
        raise SheetsError(f"service-account key rejected locally: {type(exc).__name__}: {exc}") from exc
    token_uri = info.get("token_uri") or "https://oauth2.googleapis.com/token"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
    except Exception as exc:  # noqa: BLE001 — network failure
        raise SheetsError(f"token exchange failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise SheetsError(f"token exchange HTTP {resp.status_code}: {resp.text[:160]}")
    token = resp.json().get("access_token")
    if not token:
        raise SheetsError("token exchange returned no access_token")
    return token


async def read_sheet_values(spreadsheet_id: str, tab_range: str = "A1:Z2000") -> list[list[str]]:
    """Read a sheet tab range via the Sheets REST API (read-only)."""
    token = await _service_account_token()
    url = f"{SHEETS_API}/{spreadsheet_id}/values/{tab_range}"
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:  # noqa: BLE001 — network failure
        raise SheetsError(f"sheets read failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise SheetsError(f"sheets read HTTP {resp.status_code}: {resp.text[:160]}")
    values = resp.json().get("values") or []
    return [[str(c) for c in row] for row in values]


# ------------------------------------------------------------ parsers ------


def parse_registry_rows(rows: list[list[str]]) -> dict[str, dict]:
    """Header row + one dict per chart_id."""
    if not rows:
        return {}
    header = [h.strip() for h in rows[0]]
    out: dict[str, dict] = {}
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        rec = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(header)}
        out[rec.get("chart_id", "")] = rec
    return out


def _num(text: str):
    try:
        return float(text.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def parse_digitized_rows(rows: list[list[str]]) -> dict[str, list[dict]]:
    """Header row + readings grouped by chart_id. Numeric values stay numeric;
    non-numeric values (e.g. dates) are kept as strings."""
    if not rows:
        return {}
    header = [h.strip() for h in rows[0]]
    out: dict[str, list[dict]] = {}
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        rec = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(header)}
        num = _num(rec.get("value", ""))
        rec["value_num"] = num
        rec["kind"] = "digitized"  # invariant: digitized readings are always labelled
        out.setdefault(rec.get("chart_id", ""), []).append(rec)
    return out


# ------------------------------------------------------- live sources ------

MOF_HISTORICAL_CSV = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    "historical/jgbcme_all.csv"
)
MOF_CURRENT_CSV = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)
# Title row, then "Date,1Y,2Y,...,40Y", dates "YYYY/M/D", percent, "-" = missing.
MOF_TENORS = ("2Y", "5Y", "10Y", "30Y")


def parse_mof_csv(text: str) -> dict[date, dict[str, float]]:
    """Parse the Ministry of Finance JGB par-yield CSV.

    Skips the title row, treats row 2 as the header, stops at the footer note
    row (empty/non-date Date cell). Returns {date: {tenor: yield_pct}}.
    """
    out: dict[date, dict[str, float]] = {}
    header: list[str] | None = None
    for line in text.splitlines():
        cells = [c.strip().strip('"') for c in line.split(",")]
        if not cells or not cells[0]:
            continue
        if header is None:
            if cells[0].lower() == "date":
                header = cells
            continue  # title row
        try:
            parts = [int(p) for p in cells[0].split("/")]
            d = date(parts[0], parts[1], parts[2])
        except (ValueError, IndexError):
            continue  # footer note row and anything non-date
        row: dict[str, float] = {}
        for i, tenor in enumerate(header[1:], start=1):
            if tenor not in MOF_TENORS or i >= len(cells):
                continue
            v = _num(cells[i])
            if v is not None and cells[i] != "-":
                row[tenor] = v
        if row:
            out[d] = row
    if not out:
        raise ValueError("MoF JGB CSV contained no usable rows")
    return out


async def fetch_jgb_curves(get_text: GetText) -> dict[date, dict[str, float]]:
    """Full JGB curve history: historical file + current-month file overlaid."""
    merged: dict[date, dict[str, float]] = {}
    for url in (MOF_HISTORICAL_CSV, MOF_CURRENT_CSV):
        try:
            merged.update(parse_mof_csv(await get_text(url)))
        except Exception as exc:  # noqa: BLE001 — one file must not kill the other
            log.warning("imf: MoF JGB fetch failed for %s: %s", url.split("/")[-1], exc)
    if not merged:
        raise ValueError("MoF JGB CSVs both failed")
    return merged


# IMF PortWatch ArcGIS: daily chokepoint transit calls. Hormuz = chokepoint6.
PORTWATCH_BASE = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)
HORMUZ_PORTID = "chokepoint6"


def parse_portwatch(payload: dict) -> list[tuple[date, int, int]]:
    """ArcGIS query payload -> [(day, n_total, n_tanker)] sorted by day."""
    out = []
    for feat in payload.get("features") or []:
        a = feat.get("attributes") or {}
        ds = str(a.get("date") or "")
        try:
            d = date.fromisoformat(ds[:10])
            total = int(a.get("n_total") or 0)
            tanker = int(a.get("n_tanker") or 0)
        except (ValueError, TypeError):
            continue
        out.append((d, total, tanker))
    out.sort(key=lambda p: p[0])
    return out


async def fetch_hormuz_daily(get_text: GetText) -> list[tuple[date, int, int]]:
    """All daily Hormuz transit rows (paginated; layer caps page size)."""
    import urllib.parse  # local import: stdlib, no new dependency

    out: list[tuple[date, int, int]] = []
    offset = 0
    while True:
        params = {
            "where": f"portid='{HORMUZ_PORTID}'",
            "outFields": "date,n_total,n_tanker",
            "orderByFields": "date",
            "resultRecordCount": "2000",
            "resultOffset": str(offset),
            "f": "pjson",
        }
        text = await get_text(PORTWATCH_BASE + "?" + urllib.parse.urlencode(params))
        rows = parse_portwatch(json.loads(text))
        if not rows:
            break
        out.extend(rows)
        if len(rows) < 2000:
            break
        offset += len(rows)
    if not out:
        raise ValueError("PortWatch returned no Hormuz rows")
    return out


# FRED OECD MEI 10Y benchmarks (monthly, percent). Verified 2026-10-08 against
# the FRED series pages: OECD Main Economic Indicators, REF_AREA codes below.
EURO_10Y_FRED = {
    "DE": "IRLTLT01DEM156N",
    "FR": "IRLTLT01FRM156N",
    "IT": "IRLTLT01ITM156N",
    "PT": "IRLTLT01PTM156N",
    "IE": "IRLTLT01IEM156N",
}


async def fetch_euro_10y(get_text: GetText, fred_api_key: str) -> dict[str, list[tuple[date, float]]]:
    """Monthly 10Y benchmark yields for DE/FR/IT/PT/IE via FRED."""
    from collector.fetchers import fred as fred_mod

    out: dict[str, list[tuple[date, float]]] = {}
    for cc, fid in EURO_10Y_FRED.items():
        try:
            pts = await fred_mod.fetch_series(fid, fred_api_key, get_text)
        except Exception as exc:  # noqa: BLE001 — per-country isolation
            log.warning("imf: FRED %s failed: %s", fid, exc)
            continue
        # monthly obs dated first-of-month on FRED; keep as-is
        out[cc] = sorted(pts)
    if not out:
        raise ValueError("all euro 10Y FRED fetches failed")
    return out


# ------------------------------------------------------- comparisons -----

END_2025 = date(2025, 12, 31)


def last_on_or_before(pts: dict[date, float] | list[tuple[date, float]], cutoff: date = END_2025):
    items = pts.items() if isinstance(pts, dict) else pts
    cands = [(d, v) for d, v in items if d <= cutoff]
    return max(cands, key=lambda p: p[0]) if cands else (None, None)


def tolerance_check(live: float | None, reading: dict) -> dict:
    """Compare one live value against one digitized reading.

    Returns a dict with diff/within/note; 'not_comparable' when there is no
    live value or the units/dates do not align (reason recorded, never a
    fabricated comparison).
    """
    rid = reading.get("reading_id", "")
    dig = reading.get("value_num")
    tol_raw = _num(reading.get("tolerance", ""))
    if live is None or dig is None or tol_raw is None:
        return {
            "reading_id": rid,
            "status": "not_comparable",
            "reason": "no matching live value" if live is None else "non-numeric digitized value or tolerance",
            "digitized": reading.get("value"),
            "digitized_unit": reading.get("unit"),
            "as_of_note": reading.get("as_of_note"),
        }
    diff = live - dig
    within = abs(diff) <= tol_raw
    return {
        "reading_id": rid,
        "status": "within_tolerance" if within else "differs_from_imf_chart",
        "live": round(live, 4),
        "digitized": dig,
        "tolerance": tol_raw,
        "diff": round(diff, 4),
        "as_of_note": reading.get("as_of_note"),
        "note": None if within else "differs from IMF chart — kept both; live value is source data, digitized is an image reading",
    }


def _digitized_table(chart_id: str, digitized: dict[str, list[dict]]) -> dict:
    return {
        "label": "IMF chart, digitized (+/- tolerance)",
        "kind": "digitized",
        "readings": digitized.get(chart_id, []),
    }


# ------------------------------------------------------------- panels ------

def _base_panel(chart_id: str, registry: dict[str, dict], status: str, tag: str) -> dict:
    reg = registry.get(chart_id, {})
    return {
        "chart_id": chart_id,
        "title": reg.get("title", chart_id),
        "source": reg.get("source_on_chart", ""),
        "source_plan": reg.get("free_live_source_plan", ""),
        "as_of": reg.get("as_of_on_chart", ""),
        "units": reg.get("units", ""),
        "frequency": reg.get("frequency", ""),
        "tag": tag,
        "status": status,
        "notes": [],
        "caveats": [c for c in [reg.get("caveats", "")] if c],
    }


def build_p1(store: Store, registry: dict, digitized: dict) -> tuple[dict, list[dict]]:
    """Global yield curves: US (FRED CMT from store), Japan (MoF JGB),
    Germany 10Y point only (FRED monthly OECD MEI; BdF/DBnomics route verified
    unavailable). Returns (panel, indicator_descriptors)."""
    panel = _base_panel("IMF-01", registry, "live", "primary")
    panel["notes"].append(
        "IMF chart 'Latest' date is not stated on the chart; live 'latest' is the "
        "most recent source observation."
    )
    indicators: list[dict] = []
    curves: dict[str, dict] = {}

    def store_curve(prefix: str, tenors: dict[str, list[str]]) -> dict[str, dict[str, float]]:
        got: dict[str, dict[str, float]] = {}
        for tenor, ids in tenors.items():
            for sid in ids:
                pts = store.points(sid)
                if pts:
                    got[tenor] = dict(sorted(pts.items()))
                    got[tenor + "__series"] = sid  # which store id actually held data
                    break
        return got

    us = store_curve("us", {"2Y": ["cycle:us2y"], "5Y": ["cycle:us5y"],
                            "10Y": ["cycle:us10y"], "30Y": ["cycle:ust30y", "cycle:us30y"]})
    curves["US"] = {t: v for t, v in us.items() if not t.endswith("__series")}
    us_series_used = {t: us.get(t + "__series") for t in ("2Y", "5Y", "10Y", "30Y")}

    jp: dict[str, dict[date, float]] = {}
    for tenor in ("2Y", "5Y", "10Y", "30Y"):
        pts = store.points(f"imf:jgb-{tenor.lower()}")
        if pts:
            jp[tenor] = dict(sorted(pts.items()))
    curves["Japan"] = jp

    de_pts = store.points("imf:euro-de-10y")
    de = dict(sorted(de_pts.items())) if de_pts else {}

    def latest(pts: dict) -> tuple[date | None, float | None]:
        if not pts:
            return None, None
        d = max(pts)
        return d, pts[d]

    table = []  # bp change at 2Y/10Y/30Y, latest vs end-2025
    latest_curves: dict[str, dict] = {}
    eoy_curves: dict[str, dict] = {}
    for name, curve in (("US", curves["US"]), ("Japan", curves["Japan"])):
        lc, ec = {}, {}
        for tenor in ("2Y", "10Y", "30Y"):
            pts = curve.get(tenor, {})
            ld, lv = latest(pts)
            ed, ev = last_on_or_before(pts)
            if ld and lv is not None:
                lc[tenor] = {"date": ld.isoformat(), "value": round(lv, 3)}
            if ed and ev is not None:
                ec[tenor] = {"date": ed.isoformat(), "value": round(ev, 3)}
            if lv is not None and ev is not None:
                table.append({
                    "country": name, "tenor": tenor,
                    "latest": round(lv, 3), "latest_date": ld.isoformat() if ld else None,
                    "end_2025": round(ev, 3), "end_2025_date": ed.isoformat() if ed else None,
                    "bp_change": round((lv - ev) * 100, 1),
                    "kind": "live",
                })
        latest_curves[name] = lc
        eoy_curves[name] = ec

    # Germany: 10Y point only; other tenors n/a until a full-curve source exists.
    de_latest_d, de_latest_v = latest(de)
    de_eoy_d, de_eoy_v = last_on_or_before(de)
    if de_latest_v is not None:
        latest_curves["Germany"] = {"10Y": {"date": de_latest_d.isoformat(), "value": round(de_latest_v, 3),
                                            "note": "monthly OECD MEI benchmark; 2Y/30Y n/a"}}
        eoy_curves["Germany"] = {"10Y": {"date": de_eoy_d.isoformat(), "value": round(de_eoy_v, 3)}} if de_eoy_v is not None else {}
        if de_eoy_v is not None:
            table.append({
                "country": "Germany", "tenor": "10Y",
                "latest": round(de_latest_v, 3), "latest_date": de_latest_d.isoformat(),
                "end_2025": round(de_eoy_v, 3), "end_2025_date": de_eoy_d.isoformat(),
                "bp_change": round((de_latest_v - de_eoy_v) * 100, 1),
                "kind": "live", "note": "monthly benchmark, not daily",
            })
    else:
        panel["notes"].append("Germany 10Y point unavailable this run (FRED fetch failed); other tenors n/a")
    panel["notes"].append(
        "Germany full curve n/a: no free full-curve source confirmed. The registry's "
        "Banque-de-France-via-DBnomics route was verified unavailable 2026-10-08 "
        "(BDF provider carries zero datasets on DBnomics; BdF webstat FM foreign "
        "series have no records). DE 10Y point is the FRED OECD MEI monthly benchmark."
    )

    # Full daily history for the small multiples (latest 370d window is a UI call).
    panel["live"] = {
        "kind": "live",
        "curves_latest": latest_curves,
        "curves_end_2025": eoy_curves,
        "bp_change_table": table,
        "history": {
            "US": {t: {d.isoformat(): round(v, 3) for d, v in pts.items()}
                   for t, pts in curves["US"].items()},
            "Japan": {t: {d.isoformat(): round(v, 3) for d, v in pts.items()}
                      for t, pts in curves["Japan"].items()},
            "Germany_10Y_monthly": {d.isoformat(): round(v, 3) for d, v in de.items()},
        },
        "store_series": {"US": us_series_used, "Japan_10Y": "imf:jgb-10y", "Germany_10Y": "imf:euro-de-10y"},
    }

    # Indicator descriptors (for the stress-matrix registry follow-up; the
    # values here are live and carry kind=live — never digitized).
    us10 = curves["US"].get("10Y", {})
    _, us10_latest = latest(us10)
    _, us10_eoy = last_on_or_before(us10)
    if us10_latest is not None and us10_eoy is not None:
        indicators.append({
            "id": "us10y_chg_eoy_bp", "name": "US 10Y change vs end-2025",
            "series_id": us_series_used.get("10Y"), "source": "FRED DGS10",
            "direction": "+", "step": "daily", "unit": "bp", "tag": "primary",
            "latest_value": round((us10_latest - us10_eoy) * 100, 1), "kind": "live",
        })
    for tenor, iid in (("10Y", "jgb10y_level"), ("30Y", "jgb30y_level")):
        pts = jp.get(tenor, {})
        ld, lv = latest(pts)
        if lv is not None:
            indicators.append({
                "id": iid, "name": f"JGB {tenor} level",
                "series_id": f"imf:jgb-{tenor.lower()}", "source": "Japan Ministry of Finance JGB par-yield CSV",
                "direction": "+", "step": "daily", "unit": "percent", "tag": "primary",
                "latest_value": round(lv, 3),
                "latest_date": ld.isoformat() if ld else None, "kind": "live",
            })

    panel["digitized_reference"] = _digitized_table("IMF-01", digitized)
    panel["tolerance_comparisons"] = _compare_p1(panel, digitized)
    return panel, indicators


def _compare_p1(panel: dict, digitized: dict) -> list[dict]:
    live = panel.get("live", {})
    lc = live.get("curves_latest", {})
    ec = live.get("curves_end_2025", {})
    # map digitized series names to our live curve points
    def live_for(series: str, point: str):
        country = "US" if series.startswith("United States") else \
                  "Germany" if series.startswith("Germany") else \
                  "Japan" if series.startswith("Japan") else None
        if not country:
            return None
        tenor = {"5Y": "5Y", "10Y": "10Y", "30Y": "30Y"}.get(point[:3])
        if not tenor:
            return None
        src = lc if "latest" in series.lower() else ec
        node = src.get(country, {}).get(tenor)
        return node["value"] if node else None

    out = []
    for r in digitized.get("IMF-01", []):
        out.append(tolerance_check(live_for(r.get("series", ""), r.get("point", "")), r))
    return out


def _trailing_mean(dates_vals: list[tuple[date, float]], window: int = 7) -> list[tuple[date, float]]:
    vals = sorted(dates_vals)
    out = []
    for i, (d, _) in enumerate(vals):
        seg = [v for _, v in vals[max(0, i - window + 1): i + 1]]
        out.append((d, sum(seg) / len(seg)))
    return out


def build_p2(store: Store, registry: dict, digitized: dict) -> tuple[dict, list[dict]]:
    """Hormuz proxy panel. Baseline = trailing-year mean of daily transits to
    2026-02-28, computed from data — never hard-coded."""
    panel = _base_panel("IMF-02", registry, "proxy", "proxy")
    indicators: list[dict] = []

    total = dict(sorted(store.points("imf:hormuz-total").items()))
    tanker = dict(sorted(store.points("imf:hormuz-tanker").items()))
    if not total:
        panel["notes"].append("PortWatch fetch failed this run; proxy series unavailable")
        panel["live"] = {"kind": "proxy", "available": False}
        panel["digitized_reference"] = _digitized_table("IMF-02", digitized)
        panel["tolerance_comparisons"] = _compare_p2_proxy_only(digitized)
        return panel, indicators

    days = sorted(total)
    latest_day = days[-1]
    total_7d = _trailing_mean([(d, float(total[d])) for d in days])
    tanker_7d = _trailing_mean([(d, float(tanker.get(d, 0))) for d in days])

    base_days = [d for d in days if date(2025, 3, 1) <= d <= date(2026, 2, 28)]
    baseline = sum(total[d] for d in base_days) / len(base_days) if base_days else None

    collapse_day = None
    if baseline:
        for d, m in total_7d:
            if m < 0.25 * baseline:
                collapse_day = d
                break
    weeks_since = (latest_day - collapse_day).days // 7 if collapse_day else None

    latest_total_7d = total_7d[-1][1]
    latest_tanker_7d = tanker_7d[-1][1]

    panel["live"] = {
        "kind": "proxy",
        "proxy_note": "IMF PortWatch daily Strait of Hormuz transit calls (total and tanker, 7-day trailing mean). "
                      "Counts all vessels through the strait (AIS-visible); NOT LNG-specific and NOT Qatar-specific. "
                      "The digitized Kpler LNG series is shown beside it as grey reference only.",
        "latest_day": latest_day.isoformat(),
        "latest_total_7d": round(latest_total_7d, 2),
        "latest_tanker_7d": round(latest_tanker_7d, 2),
        "baseline_trailing_year_to_2026_02_28": round(baseline, 2) if baseline else None,
        "baseline_days": len(base_days),
        "collapse_day_7d_below_25pct_baseline": collapse_day.isoformat() if collapse_day else None,
        "weeks_since_collapse": weeks_since,
        "history_total_7d": {d.isoformat(): round(v, 2) for d, v in total_7d},
        "history_tanker_7d": {d.isoformat(): round(v, 2) for d, v in tanker_7d},
        "store_series": {"total": "imf:hormuz-total", "tanker": "imf:hormuz-tanker"},
    }
    indicators.append({
        "id": "hormuz_transits_7d",
        "name": "Strait of Hormuz transit calls, 7d mean",
        "series_id": "imf:hormuz-total", "source": "IMF PortWatch ArcGIS Daily_Chokepoints_Data (chokepoint6)",
        "direction": "-", "step": "weekly", "unit": "transits/day", "tag": "proxy",
        "proxy_excluded_from_composites": True,
        "latest_value": round(latest_total_7d, 2), "latest_date": latest_day.isoformat(),
        "kind": "proxy",
    })

    panel["digitized_reference"] = _digitized_table("IMF-02", digitized)
    panel["tolerance_comparisons"] = _compare_p2_proxy_only(digitized)
    panel["notes"].append(
        "Live-vs-digitized tolerance checks are not applicable: the proxy is in "
        "transit calls/day while the digitized Kpler readings are in million metric "
        "tons/week. Both are shown; neither overwrites the other."
    )
    return panel, indicators


def _compare_p2_proxy_only(digitized: dict) -> list[dict]:
    return [
        {
            "reading_id": r.get("reading_id", ""),
            "status": "not_comparable",
            "reason": "unit mismatch: digitized Kpler value in Mt/week; live proxy in transit calls/day",
            "digitized": r.get("value"),
            "digitized_unit": r.get("unit"),
            "as_of_note": r.get("as_of_note"),
        }
        for r in digitized.get("IMF-02", [])
    ]


def build_p3(registry: dict, digitized: dict) -> dict:
    """AI value chain: static annual bubble chart, digitized only, Q tag, no velocity."""
    panel = _base_panel("IMF-03", registry, "digitized-only", "Q")
    panel["tag"] = "Q"
    panel["notes"].append(
        "Static annual data (2025). Ranks are as printed on the chart. "
        "A UN Comtrade rebuild is optional and needs Harry's approval — it will not "
        "match the IMF's Trade Data Monitor figures."
    )
    rows = []
    for r in digitized.get("IMF-03", []):
        rows.append({
            "economy": r.get("series", ""),
            "point": r.get("point", ""),
            "balance_usd_bn": r.get("value_num"),
            "tolerance": _num(r.get("tolerance", "")),
            "kind": "digitized",
        })
    panel["live"] = {"kind": "n/a", "available": False,
                     "reason": "manual only per registry; Trade Data Monitor is paid"}
    panel["digitized_reference"] = {
        "label": "IMF chart, digitized (+/- tolerance)",
        "kind": "digitized",
        "bubble_data": rows,
    }
    panel["tolerance_comparisons"] = []  # no live series: nothing to compare
    return panel


def build_p4(registry: dict, digitized: dict) -> dict:
    """AI debt issuance: digitized bars; 2026:H2 bar hatched as IMF estimate."""
    panel = _base_panel("IMF-04", registry, "digitized-only", "manual")
    panel["notes"].append(
        "SCOPE: US AI-related NET debt issuance, six firms (Alphabet, Amazon, IBM, "
        "Meta, Microsoft, Oracle). Kept separate from the hyperscaler bond series "
        "tracked elsewhere (five companies, USD-only, LSEG/mlq.ai-based): different "
        "scope and metric — never add or compare the two without saying so."
    )
    bars = []
    for r in digitized.get("IMF-04", []):
        label = r.get("point", "")
        bars.append({
            "label": label,
            "value_usd_bn": r.get("value_num"),
            "tolerance": _num(r.get("tolerance", "")),
            "hatched_estimate": "estimate" in label.lower() or "H2" in label,
            "kind": "digitized",
        })
    panel["live"] = {"kind": "n/a", "available": False,
                     "reason": "manual only per registry; no free feed"}
    panel["digitized_reference"] = {
        "label": "IMF chart, digitized (+/- tolerance)",
        "kind": "digitized",
        "bars": bars,
    }
    panel["tolerance_comparisons"] = []
    return panel


def build_p5(store: Store, registry: dict, digitized: dict) -> tuple[dict, list[dict]]:
    """Euro area 10Y spreads vs Bunds, monthly OECD MEI benchmarks via FRED,
    DERIVED (country minus Germany, in bp)."""
    panel = _base_panel("IMF-05", registry, "live", "derived")
    indicators: list[dict] = []

    yields: dict[str, dict[date, float]] = {}
    for cc in ("DE", "FR", "IT", "PT", "IE"):
        pts = store.points(f"imf:euro-{cc.lower()}-10y")
        if pts:
            yields[cc] = dict(sorted(pts.items()))
    if "DE" not in yields:
        panel["notes"].append("German 10Y leg unavailable this run; spreads cannot be computed")
        panel["live"] = {"kind": "derived", "available": False}
        panel["digitized_reference"] = _digitized_table("IMF-05", digitized)
        panel["tolerance_comparisons"] = []
        return panel, indicators

    de = yields["DE"]
    spreads: dict[str, dict[date, float]] = {}
    for cc in ("FR", "IT", "PT", "IE"):
        y = yields.get(cc, {})
        common = sorted(set(de) & set(y))
        spreads[cc] = {d: round((y[d] - de[d]) * 100, 1) for d in common}

    latest_map = {}
    for cc, sp in spreads.items():
        if sp:
            d = max(sp)
            latest_map[cc] = {"date": d.isoformat(), "bp": sp[d]}

    # divergence pair: France-minus-Bund vs Italy-minus-Bund
    fr_it_note = None
    if "FR" in spreads and "IT" in spreads:
        common = sorted(set(spreads["FR"]) & set(spreads["IT"]))
        cross = [d for d in common if spreads["FR"][d] > spreads["IT"][d]]
        if cross:
            fr_it_note = (
                f"France has been above Italy since {cross[0].isoformat()} "
                f"(latest FR {spreads['FR'][max(common)]}bp vs IT {spreads['IT'][max(common)]}bp)"
            )

    panel["live"] = {
        "kind": "derived",
        "derived_note": "Spread = country 10Y minus German 10Y, in bp, from monthly OECD MEI "
                        "benchmark yields (FRED). Free benchmarks may differ from Bloomberg "
                        "generics by a few bp.",
        "latest": latest_map,
        "history_spreads_bp": {cc: {d.isoformat(): v for d, v in sp.items()}
                               for cc, sp in spreads.items()},
        "divergence_pair_fr_vs_it": fr_it_note,
        "store_series": {cc: f"imf:euro-{cc.lower()}-10y" for cc in ("DE", "FR", "IT", "PT", "IE")},
    }
    if fr_it_note:
        panel["notes"].append(fr_it_note)
    panel["notes"].append(
        "Monthly cadence: OECD MEI benchmarks lag ~2 months, so 'latest' is older "
        "than the IMF chart's early-Oct-2026 last point. Daily n/a until a daily "
        "free source for FR/IT/PT/IE 10Y benchmarks is confirmed."
    )

    for cc, name in (("FR", "France"), ("IT", "Italy"), ("PT", "Portugal"), ("IE", "Ireland")):
        lm = latest_map.get(cc)
        if lm:
            indicators.append({
                "id": f"euro_spread_{cc.lower()}_de_10y",
                "name": f"{name} 10Y minus Bund",
                "series_id": f"imf:euro-{cc.lower()}-10y",
                "formula": f"({EURO_10Y_FRED[cc]} − {EURO_10Y_FRED['DE']}) × 100",
                "source": "FRED OECD MEI 10Y benchmarks",
                "direction": "+", "step": "monthly", "unit": "bp",
                "tag": "derived", "category": "Credit",
                "latest_value": lm["bp"], "latest_date": lm["date"],
                "kind": "live",
            })

    panel["digitized_reference"] = _digitized_table("IMF-05", digitized)
    panel["tolerance_comparisons"] = _compare_p5(panel, digitized)
    return panel, indicators


def _compare_p5(panel: dict, digitized: dict) -> list[dict]:
    live = panel.get("live", {})
    hist = live.get("history_spreads_bp", {})
    latest = live.get("latest", {})

    def live_for(series: str, point: str, as_of_note: str):
        cc = {"France": "FR", "Italy": "IT", "Portugal": "PT", "Ireland": "IE"}.get(
            series.split(" minus ")[0])
        if not cc:
            return None
        if "Latest" in point:
            node = latest.get(cc)
            return node["bp"] if node else None
        # dated points: match by month where the note names one
        for token, key in (("Jan 2026", "2026-01"), ("Sep 1 2026", "2026-09"),
                           ("Sep 2026", "2026-09"), ("Mar 2026", "2026-03")):
            if token in (point + " " + as_of_note):
                return hist.get(cc, {}).get(key)
        return None

    return [tolerance_check(live_for(r.get("series", ""), r.get("point", ""), r.get("as_of_note", "")), r)
            for r in digitized.get("IMF-05", [])]


# ------------------------------------------------------------ refresh ------

def _degraded_payload(reason: str) -> dict:
    panels = []
    for cid in ("IMF-01", "IMF-02", "IMF-03", "IMF-04", "IMF-05"):
        panels.append({
            "chart_id": cid, "title": cid, "status": "not-connected",
            "tag": "n/a", "source": "", "as_of": "",
            "message": NOT_CONNECTED, "reason": reason,
            "live": {"kind": "n/a", "available": False},
            "digitized_reference": {"label": "IMF chart, digitized (+/- tolerance)",
                                    "kind": "digitized", "readings": []},
            "tolerance_comparisons": [], "notes": [], "caveats": [],
        })
    return {
        "status": "not-connected",
        "message": NOT_CONNECTED,
        "reason": reason,
        "panels": panels,
        "indicators": [],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


async def refresh_imf(store: Store, get_text: GetText, fred_api_key: str | None = None) -> dict:
    """Fetch live sources, build all five panels, write doc ``imf_panels``.

    Never raises for missing credentials or failed upstreams: those degrade
    into per-panel not-connected/error states and are logged.
    """
    if fred_api_key is None:
        fred_api_key = os.environ.get("FRED_API_KEY", "")

    try:
        reg_rows, dig_rows = await asyncio.gather(
            read_sheet_values(REGISTRY_SHEET_ID),
            read_sheet_values(DIGITIZED_SHEET_ID),
        )
    except SheetsError as exc:
        log.warning("imf: sheets unavailable: %s", exc)
        payload = _degraded_payload(str(exc))
        store.put_doc(DOC_KEY, payload, "imf")
        return payload

    registry = parse_registry_rows(reg_rows)
    digitized = parse_digitized_rows(dig_rows)
    log.info("imf: registry=%d charts, digitized=%d readings",
             len(registry), sum(len(v) for v in digitized.values()))

    # ---- live-source fetches (each isolated; failures degrade that panel) ----
    try:
        jgb = await fetch_jgb_curves(get_text)
        for tenor in MOF_TENORS:
            pts = [(d, row[tenor]) for d, row in sorted(jgb.items()) if tenor in row]
            if pts:
                store.upsert_points(f"imf:jgb-{tenor.lower()}", pts)
        jgb_ok = True
    except Exception as exc:  # noqa: BLE001
        log.warning("imf: JGB fetch failed: %s", exc)
        jgb_ok = False

    try:
        hormuz = await fetch_hormuz_daily(get_text)
        store.upsert_points("imf:hormuz-total", [(d, t) for d, t, _ in hormuz])
        store.upsert_points("imf:hormuz-tanker", [(d, k) for d, _, k in hormuz])
        hormuz_ok = True
    except Exception as exc:  # noqa: BLE001
        log.warning("imf: PortWatch fetch failed: %s", exc)
        hormuz_ok = False

    euro_ok = False
    if fred_api_key:
        try:
            euro = await fetch_euro_10y(get_text, fred_api_key)
            for cc, pts in euro.items():
                store.upsert_points(f"imf:euro-{cc.lower()}-10y", pts)
            euro_ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("imf: euro 10Y fetch failed: %s", exc)
    else:
        log.warning("imf: FRED_API_KEY unset; euro 10Y benchmarks skipped")

    # ---- panels ----
    p1, ind1 = build_p1(store, registry, digitized)
    if not jgb_ok:
        p1["notes"].append("Japan curve unavailable this run (MoF fetch failed)")
    p2, ind2 = build_p2(store, registry, digitized)
    if not hormuz_ok and p2["live"].get("available", True) is False:
        pass  # build_p2 already noted it
    p3 = build_p3(registry, digitized)
    p4 = build_p4(registry, digitized)
    p5, ind5 = build_p5(store, registry, digitized)
    if not euro_ok:
        p5["notes"].append("Euro 10Y benchmarks unavailable this run (FRED fetch failed or key unset)")

    payload = {
        "status": "ok",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sheets": {
            "registry_id": REGISTRY_SHEET_ID,
            "digitized_id": DIGITIZED_SHEET_ID,
            "registry_charts": len(registry),
            "digitized_readings": sum(len(v) for v in digitized.values()),
        },
        "fetch_status": {"jgb": jgb_ok, "hormuz": hormuz_ok, "euro_10y": euro_ok},
        "panels": [p1, p2, p3, p4, p5],
        # Indicator DESCRIPTORS for the stress-matrix registry follow-up.
        # Values are live/proxy only; digitized values never appear here.
        "indicators": ind1 + ind2 + ind5,
    }
    store.put_doc(DOC_KEY, payload, "imf")
    log.info("imf: wrote doc %s (%d panels, %d indicators)",
             DOC_KEY, len(payload["panels"]), len(payload["indicators"]))
    return payload


def imf_payload(store: Store) -> dict:
    """Read the ``imf_panels`` doc (written by refresh_imf).

    Returns the degraded skeleton when the doc is missing — the coordinator's
    API route can serve this directly.
    """
    doc = store.doc(DOC_KEY)
    if doc is None:
        return _degraded_payload("imf_panels doc not yet written (refresh_imf has not run)")
    return doc.payload
