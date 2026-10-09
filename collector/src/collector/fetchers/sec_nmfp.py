"""SEC N-MFP Money Market Fund holdings (monthly, keyless bulk download).

Source: SEC DERA Form N-MFP Data Sets
  https://www.sec.gov/data-research/sec-markets-data/dera-form-n-mfp-data-sets
  Monthly ZIPs (~10MB) with TSV flat files extracted from EDGAR N-MFP XML.

This is the ideal source: bulk download, no per-filing EDGAR parsing needed.
Covers every registered money market fund (~$6T+ total as of 2026).

What we compute:
  - Total MMF net assets (all funds, by category: Government / Prime / Tax-Exempt)
  - Portfolio allocation: % in repo, % in Treasury debt, % in agency debt,
    % in commercial paper, % in CDs — the funding-market footprint
  - Average WAM / WAL (weighted average maturity / life)
  - Top funds by assets

Time series (monthly):
  cycle:mmf-total-assets      — total MMF net assets ($bn)
  cycle:mmf-govt-assets       — government MMF assets ($bn)
  cycle:mmf-prime-assets       — prime MMF assets ($bn)
  cycle:mmf-repo-pct          — % of holdings in repurchase agreements
  cycle:mmf-treasury-pct      — % in U.S. Treasury debt
  cycle:mmf-agency-pct        — % in agency debt
  cycle:mmf-cp-pct            — % in commercial paper (all types)
  cycle:mmf-wam               — asset-weighted average WAM (days)
  cycle:mmf-wal               — asset-weighted average WAL (days)

Docs:
  nmfp:{YYYYMM}               — full monthly aggregate + top funds

Cadence: monthly. N-MFP is filed by the 5th business day; public after 60 days.
So the "latest" ZIP lags ~2 months (e.g., Oct 2026 ZIP covers Aug 2026 data).

SEC fair-access: descriptive User-Agent, single ZIP download per run (~11MB).
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from datetime import date
from typing import NamedTuple

from collector.http import GetText
from collector.store import Store

log = logging.getLogger(__name__)

DATASETS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/dera-form-n-mfp-data-sets"
ZIP_BASE = "https://www.sec.gov/files/dera/data/form-n-mfp-data-sets/"
SOURCE = "sec-nmfp"

# Investment categories -> our buckets (from N-MFP INVESTMENTCATEGORY values)
REPO_CATEGORIES = {
    "U.S. Treasury Repurchase Agreement, if collateralized only by U.S. Treasuries (including Strips) and cash",
    "U.S. Government Agency Repurchase Agreement, collateralized only by U.S. Government Agency securities, U.S. Treasuries, and cash",
    "Other Repurchase Agreement, if collateral falls outside Treasury, Government Agency and cash",
}
TREASURY_CATEGORIES = {
    "U.S. Treasury Debt",
}
AGENCY_CATEGORIES = {
    "U.S. Government Agency Debt (if categorized as coupon-paying notes)",
    "U.S. Government Agency Debt (if categorized as no-coupon discount notes)",
}
CP_CATEGORIES = {
    "Financial Company Commercial Paper",
    "Non-Financial Company Commercial Paper",
    "Asset Backed Commercial Paper",
}


class FundInfo(NamedTuple):
    accession: str
    category: str  # Government / Prime / Tax Exempt / etc.
    net_assets: float  # USD
    wam: float | None  # days
    wal: float | None  # days
    is_govt: bool
    is_retail: bool


def _fnum(s: str | None) -> float | None:
    if not s or not s.strip():
        return None
    try:
        return float(s.strip().replace(",", ""))
    except ValueError:
        return None


def discover_latest_zip(page_html: str) -> str | None:
    """Extract the most recent N-MFP ZIP URL from the datasets page."""
    # hrefs look like: /files/dera/data/form-n-mfp-data-sets/20260909-20261007_nmfp.zip
    hrefs = re.findall(r'href="(/files/dera/data/form-n-mfp-data-sets/[^"]+_nmfp\.zip)"', page_html)
    if not hrefs:
        return None
    # First href is the most recent (page lists newest first)
    return "https://www.sec.gov" + hrefs[0]


def zip_month_from_name(zip_url: str) -> str:
    """Extract YYYYMM from ZIP filename like 20260909-20261007_nmfp.zip.

    The date range covers the filing window; the data month is ~2 months
    before the end date (60-day public delay). We use the start date's
    month minus 2 as an approximation, refined by actual filing dates
    in the TSV.
    """
    m = re.search(r"(\d{8})-(\d{8})_nmfp\.zip", zip_url)
    if not m:
        return "unknown"
    end = m.group(2)  # YYYYMMDD
    # Data is ~60 days before the posting window end
    y, mo = int(end[:4]), int(end[4:6])
    mo -= 2
    if mo <= 0:
        mo += 12
        y -= 1
    return f"{y:04d}{mo:02d}"


def parse_series_tsv(text: str) -> list[FundInfo]:
    """Parse NMFP_SERIESLEVELINFO.tsv into fund records."""
    funds = []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    for row in reader:
        net_assets = _fnum(row.get("NETASSETOFSERIES"))
        if not net_assets:
            continue
        funds.append(FundInfo(
            accession=row.get("ACCESSION_NUMBER", ""),
            category=(row.get("MONEYMARKETFUNDCATEGORY") or "").strip(),
            net_assets=net_assets,
            wam=_fnum(row.get("AVERAGEPORTFOLIOMATURITY")),
            wal=_fnum(row.get("AVERAGELIFEMATURITY")),
            is_govt=(row.get("GOVMONEYMRKTFUNDFLAG") or "").strip().upper() == "Y",
            is_retail=(row.get("FUNDRETAILMONEYMARKETFLAG") or "").strip().upper() == "Y",
        ))
    return funds


def parse_holdings_tsv(text: str) -> dict[str, float]:
    """Parse NMFP_SCHPORTFOLIOSECURITIES.tsv into category -> total value (USD).

    Uses EXCLUDINGVALUEOFANYSPONSORSUPP (market value excluding sponsor support).
    """
    totals: dict[str, float] = {}
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    for row in reader:
        cat = (row.get("INVESTMENTCATEGORY") or "").strip()
        val = _fnum(row.get("EXCLUDINGVALUEOFANYSPONSORSUPP"))
        if not cat or not val:
            continue
        totals[cat] = totals.get(cat, 0.0) + val
    return totals


def bucket_allocation(cat_totals: dict[str, float]) -> dict[str, float]:
    """Aggregate category totals into our buckets, returning percentages."""
    grand = sum(cat_totals.values())
    if grand <= 0:
        return {}
    def pct(cats: set[str]) -> float:
        return 100.0 * sum(cat_totals.get(c, 0.0) for c in cats) / grand
    return {
        "repo": pct(REPO_CATEGORIES),
        "treasury": pct(TREASURY_CATEGORIES),
        "agency": pct(AGENCY_CATEGORIES),
        "cp": pct(CP_CATEGORIES),
    }


async def fetch_nmfp(
    store: Store,
    get_text: GetText,
    get_bytes=None,
    today: date | None = None,
) -> str:
    """Monthly N-MFP pull: download latest ZIP, parse, store aggregates.

    get_bytes: async callable for binary download (defaults to httpx via
    collector.http.get_bytes if not provided).
    """
    from collector.http import get_bytes as _get_bytes
    gb = get_bytes or _get_bytes

    today = today or date.today()

    # 1. Discover latest ZIP
    page = await get_text(DATASETS_PAGE)
    zip_url = discover_latest_zip(page)
    if not zip_url:
        return "nmfp: no ZIP found on datasets page"
    yyyymm = zip_month_from_name(zip_url)

    # 2. Skip if already done
    doc_key = f"nmfp:{yyyymm}"
    if store.doc(doc_key) is not None:
        return f"nmfp: {yyyymm} already stored, skipping"

    # 3. Download ZIP (~11MB)
    log.info("nmfp: downloading %s", zip_url)
    zip_data = await gb(zip_url)

    # 4. Parse TSVs
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        series_text = zf.read("NMFP_SERIESLEVELINFO.tsv").decode("utf-8", errors="replace")
        holdings_text = zf.read("NMFP_SCHPORTFOLIOSECURITIES.tsv").decode("utf-8", errors="replace")

    funds = parse_series_tsv(series_text)
    cat_totals = parse_holdings_tsv(holdings_text)
    alloc = bucket_allocation(cat_totals)

    if not funds:
        return f"nmfp: {yyyymm} parsed 0 funds, aborting"

    # 5. Aggregates
    total_assets = sum(f.net_assets for f in funds)
    govt_assets = sum(f.net_assets for f in funds if f.is_govt)
    prime_assets = sum(
        f.net_assets for f in funds
        if "prime" in f.category.lower()
    )
    # Asset-weighted WAM/WAL
    wam_num = sum(f.net_assets * f.wam for f in funds if f.wam is not None)
    wam_den = sum(f.net_assets for f in funds if f.wam is not None)
    wal_num = sum(f.net_assets * f.wal for f in funds if f.wal is not None)
    wal_den = sum(f.net_assets for f in funds if f.wal is not None)
    avg_wam = wam_num / wam_den if wam_den else None
    avg_wal = wal_num / wal_den if wal_den else None

    # Top 10 funds by assets
    top_funds = sorted(funds, key=lambda f: -f.net_assets)[:10]

    asof = date(int(yyyymm[:4]), int(yyyymm[4:6]), 1)

    # 6. Store time series (monthly points)
    total_bn = total_assets / 1e9
    store.upsert_points("cycle:mmf-total-assets", [(asof, total_bn)])
    store.upsert_points("cycle:mmf-govt-assets", [(asof, govt_assets / 1e9)])
    store.upsert_points("cycle:mmf-prime-assets", [(asof, prime_assets / 1e9)])
    for key, series_id in [
        ("repo", "cycle:mmf-repo-pct"),
        ("treasury", "cycle:mmf-treasury-pct"),
        ("agency", "cycle:mmf-agency-pct"),
        ("cp", "cycle:mmf-cp-pct"),
    ]:
        if key in alloc:
            store.upsert_points(series_id, [(asof, alloc[key])])
    if avg_wam is not None:
        store.upsert_points("cycle:mmf-wam", [(asof, avg_wam)])
    if avg_wal is not None:
        store.upsert_points("cycle:mmf-wal", [(asof, avg_wal)])

    # 7. Store doc
    payload = {
        "yyyymm": yyyymm,
        "asof": asof.isoformat(),
        "n_funds": len(funds),
        "total_assets_usd": total_assets,
        "govt_assets_usd": govt_assets,
        "prime_assets_usd": prime_assets,
        "allocation_pct": alloc,
        "avg_wam_days": avg_wam,
        "avg_wal_days": avg_wal,
        "top_funds": [
            {"category": f.category, "net_assets_usd": f.net_assets,
             "wam": f.wam, "wal": f.wal}
            for f in top_funds
        ],
        "category_totals_usd": cat_totals,
        "zip_url": zip_url,
        "source": "sec-dera-nmfp",
    }
    store.put_doc(doc_key, payload, SOURCE)
    log.info(
        "nmfp: %s stored — %d funds, $%.1fB total, repo %.1f%%, treasury %.1f%%",
        yyyymm, len(funds), total_bn,
        alloc.get("repo", 0), alloc.get("treasury", 0),
    )
    return f"nmfp: {yyyymm} OK — {len(funds)} funds, ${total_bn:.1f}B"
