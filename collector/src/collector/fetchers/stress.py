"""Stress Monitor v2 — Phase 1: Tier-1 indicator registry + missing-source fetches.

Spec: ~/workspace/user/files/os-bloom-stress-monitor-prompt.md (sections 2, 3,
11, 13, 16.4). Recon: ~/workspace/osb-stressv2-recon/RECON.md (sections 2, 5, 7).

The ONE source of truth for the Tier-1 registry is INDICATORS below.
config.yaml only carries the `cadences: stress` entry.

Every live indicator's series_id was verified against config.yaml or a fetcher
in this repo before being registered (never invented). Indicators with no
reachable free source are tagged "n/a" with a reason and surface in
build_gaps() (spec section 2 ladder, rung 6).

Scoring coordination (Worker A): collector.stress_score.refresh_stress(store,
registry, weights=STRESS_WEIGHTS), where registry is the INDICATORS list and
weights is the spec-16.5 scheme keyed by this registry's category names
(Worker A's DEFAULT_WEIGHTS keys are a different, lowercase set and would
zero out the gauge). refresh_stress_full() fetches the missing sources and
materializes DERIVED/PROXY rows as stress:<id> series first, then calls the
scoring refresh when the module exists; it degrades cleanly when it does not
(no crash, gap logged).

Phase 2 (spec 16.4/16.5, WS4): auction detail per bucket (bid-to-cover,
indirect/direct/dealer takedown; tail is n/a — no free when-issued feed),
put/call total/equity/index, ETF 1d/5d momentum as DERIVED rows, the full OFR
HF Monitor fpf set that is actually configured (mnemonics verified against the
hf/v1 metadata endpoint), and Market-activity rows re-registered as DERIVED
log-volume series (|z| of log volume per 16.4 — the stress job log-transforms,
the engine scores; stress_score.py untouched). Licensing (16.9): every row
carries `redistributable` (yes/no/unknown). Rates levels carry
`abs_velocity=True` (16.4 flight-to-quality rule; the engine's velocity layer
already ranks |raw| percentiles, so the flag is a registry declaration).
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

from collector.fetchers import fred as _fred
from collector.http import GetBytes, GetText, get_bytes as _default_get_bytes, get_text as _default_get_text
from collector.store import Store

# ---------------------------------------------------------------------------
# Tier-1 series the repo does not fetch anywhere else yet (FRED id -> store id).
# All verified to exist on FRED; fetched here because the stress job is the
# only consumer and the spec's ladder rung 1 is the primary source.
# ---------------------------------------------------------------------------
MISSING_FRED: dict[str, str] = {
    "VXVCLS": "cycle:vix3m",            # 3M VIX — vix_term DERIVED leg
    "OVXCLS": "cycle:ovx",              # Cboe oil vol index
    "GVZCLS": "cycle:gvz",              # Cboe gold vol index
    "BAMLEMCBPIOAS": "cycle:em-corp-oas",  # EM corporate OAS
    "ANFCI": "cycle:anfci",             # Chicago Fed adjusted NFCI (weekly)
    "KCFSI": "cycle:kcfsi",             # Kansas City Fed FSI (monthly)
    "USEPUINDXD": "cycle:epu",          # Economic Policy Uncertainty (daily)
    "WLCFLPCL": "cycle:disc-window",    # Primary credit borrowing ($m, weekly)
    "SWPT": "cycle:swap-lines",          # Central bank liquidity swaps ($m, weekly)
    "RIFSPPFAAD90D": "cycle:cp-fin-aa90",      # AA financial 90d CP — cp_spread leg
    "RIFSPPNAAD90D": "cycle:cp-nonfin-aa90",   # AA nonfinancial 90d CP — cp_spread leg
}

# NY Fed ACM term-premium interactive data feed. The datasets page itself is
# JS-rendered, but its own app fetches this static CSV (verified 2026-10-08):
#   RunDates,TERMYld,ACMFITYld,GSWYld
# TERMYld == ACMTP10 (10Y term premium), monthly end-of-month, back to 1961-06.
# Cross-checked against the ACMTermPremium.xls workbook: the 2026-09-30 row
# matches ACMTP10 exactly (0.885008256163907). Daily data exists only in the
# 10MB workbook — the CSV is the polite fetch, so this indicator is monthly.
ACM_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/acmPlot_data.csv"

# OFR Financial Stress Index daily CSV (verified 2026-10-08):
#   Date,OFR FSI,Credit,Equity valuation,Safe assets,Funding,Volatility,...
# daily from 2000-01-03, latest 2026-10-05 at check time.
OFR_FSI_URL = "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv"

# ---------------------------------------------------------------------------
# Freshness policy (spec 16.6): an observation is stale only when its age
# exceeds expected_lag_days + tolerance. max_age_days is the hard fail line.
# ---------------------------------------------------------------------------
LAG_D = {"expected_lag_days": 1, "max_age_days": 4}      # daily, T-1 + weekend
LAG_W = {"expected_lag_days": 7, "max_age_days": 14}     # weekly
LAG_M = {"expected_lag_days": 35, "max_age_days": 70}   # monthly
LAG_Q = {"expected_lag_days": 120, "max_age_days": 240}  # quarterly (OFR, Form PF lag)
LAG_E = {"expected_lag_days": 10, "max_age_days": 45}    # event series (auctions)


# ---------------------------------------------------------------------------
# Licensing (spec 16.9): every indicator carries `redistributable` —
# yes / no / unknown — auto-derived from its source unless explicitly
# overridden. Honest mapping: FRED / OFR (financialresearch.gov) / Treasury
# FiscalData / CFTC / NY Fed research data and os-bloom's own engine outputs
# are public and redistributable; AAII's survey is not (16.9 names it); the
# daybook CDX capture is not (passed explicitly per row); TRACE, STAR, Cboe,
# Yahoo and ICE terms were not checked, so they stay "unknown".
# ---------------------------------------------------------------------------
def _redist_from_source(source: str) -> str:
    s = (source or "").lower()
    if "aaii" in s:
        return "no"
    if "fred" in s:
        return "yes"
    if "financialresearch.gov" in s or "ofr" in s:
        return "yes"
    if "fiscaldata" in s:
        return "yes"
    if "cftc" in s:
        return "yes"
    if "ny fed" in s or "new york fed" in s:
        return "yes"
    if "os-bloom" in s:
        return "yes"
    return "unknown"


def _ind(id, category, name, unit, direction, freq, tier, series_id, source, tag,
         inputs=None, formula=None, reason=None, rung=None, note=None,
         composite_excluded=False, redistributable=None, abs_velocity=False,
         **lag):
    d = {
        "id": id, "category": category, "name": name, "unit": unit,
        "direction": direction, "freq": freq, "tier": tier,
        "series_id": series_id, "source": source, "tag": tag,
        "inputs": inputs or [], "formula": formula or "",
        "reason": reason or "", "rung": rung or "",
        "ladder_rung": rung or "",  # alias: collector.stress_score reads this key
        "note": note or "", "composite_excluded": composite_excluded,
        # 16.9 licensing; 16.4: rates levels flag abs_velocity=True
        "redistributable": redistributable or _redist_from_source(source),
        "abs_velocity": bool(abs_velocity),
    }
    d.update(lag or LAG_D)
    return d


def _na(id, category, name, unit, direction, freq, reason,
        rung="rung 6: n/a — no free or reachable source", redistributable=None):
    return _ind(id, category, name, unit, direction, freq, "extended", None,
                "n/a", "n/a", reason=reason, rung=rung,
                redistributable=redistributable)


# Auction buckets straight from config.yaml `auctions.buckets` (13 configured;
# the prompt's 16.4 says 11 — config is the source of truth here).
AUCTION_BUCKETS = [
    "Bill-4W", "Bill-8W", "Bill-13W", "Bill-17W", "Bill-26W", "Bill-52W",
    "Note-2Y", "Note-3Y", "Note-5Y", "Note-7Y", "Note-10Y",
    "Bond-20Y", "Bond-30Y",
]

# OFR Hedge Fund Monitor rows (config.yaml `ofr_series`; stored as
# ofr:<mnemonic> by fetchers/ofr.py — every mnemonic verified against the
# hf/v1 metadata endpoint 2026-10-08).
OFR_HF = [
    ("hf-gav", "HF gross assets", "$", "FPF-ALLQHF_GAV_SUM", "+"),
    ("hf-count", "Qualifying hedge funds", "count", "FPF-ALLQHF_COUNT", "±"),
    ("hf-top10-share", "Top-10 share of HF gross assets", "%", "FPF-ALLQHF_GAVN10_GAV_SHARE", "+"),
    ("hf-top10-lev", "Top-10 HF leverage ratio", "x", "FPF-ALLQHF_GAVN10_LEVERAGERATIO_AVERAGE", "+"),
    ("hf-top10-borrow", "Top-10 HF borrowing % of total", "%", "FPF-ALLQHF_GAVN10_BORROWING_PERCENT", "+"),
    ("hf-fin-liq-7d", "HF financing maturing <=7d", "$", "FPF-ALLQHF_FINANCINGLIQUIDITYLE7_SUM", "+"),
    ("hf-cash-ratio", "Top-10 HF unencumbered cash", "%", "FPF-ALLQHF_GAVN10_CASHRATIO_AVERAGE", "-"),
    ("hf-fin-90d-plus", "HF financing maturing 90d+ share", "%", "FPF-ALLQHF_FINANCINGLIQUIDTYGT90_PERCENT", "-"),
    ("hf-stress-eq-p5", "HF -20% equity shock, 5th pct", "%", "FPF-ALLQHF_EQDOWN20P_P5", "+"),
    ("hf-stress-eq-p50", "HF -20% equity shock, median", "%", "FPF-ALLQHF_EQDOWN20P_P50", "+"),
    ("hf-stress-cds-p5", "HF -250bp credit shock, 5th pct", "%", "FPF-ALLQHF_CDSDOWN250BPS_P5", "+"),
    ("hf-gates", "HF net assets gated", "%", "FPF-ALLQHF_CURRENTLYGATES_PERCENT", "+"),
    ("hf-suspensions", "HF net assets suspended", "%", "FPF-ALLQHF_CURRENTLYSUSPENSIONS_PERCENT", "+"),
]

# FINRA TRACE monthly/treasury par series (all verified in
# fetchers/trace_monthly.py and fetchers/trace_treasury.py; stored cycle:<key>).
# Treasury rows are daily; monthly-product rows are monthly.
TRACE_ROWS = [
    # (registry id, name, store key, freq-lag)
    ("trace_ust_total", "Treasury volume (total)", "trace-ust-par", LAG_D),
    ("trace_ust_bills", "Treasury volume: bills", "trace-ust-bills-par", LAG_D),
    ("trace_ust_coupons", "Treasury volume: nominal coupons", "trace-ust-coupons-par", LAG_D),
    ("trace_ust_tips", "Treasury volume: TIPS", "trace-ust-tips-par", LAG_D),
    ("trace_ust_frns", "Treasury volume: FRNs", "trace-ust-frns-par", LAG_D),
    ("trace_ust_onrun", "Treasury volume: on-the-run", "trace-ust-onrun-par", LAG_D),
    ("trace_ust_offrun", "Treasury volume: off-the-run", "trace-ust-offrun-par", LAG_D),
    ("trace_ust_ats", "Treasury volume: ATS", "trace-ust-par-ats", LAG_D),
    ("trace_ust_d2c", "Treasury volume: dealer-to-customer", "trace-ust-par-d2c", LAG_D),
    ("trace_corp", "Corporate volume", "trace-corp-par", LAG_M),
    ("trace_tba", "TBA volume", "trace-tba-par", LAG_M),
    ("trace_mbs", "Agency MBS (specified pools) volume", "trace-mbs-par", LAG_M),
    ("trace_agcy", "Agency debenture volume", "trace-agcy-par", LAG_M),
    ("trace_abs", "ABS (consumer) volume", "trace-abs-par", LAG_M),
    ("trace_cmo", "CMO volume", "trace-cmo-par", LAG_M),
    ("trace_absx", "ABSX (CDO/CLO/non-agency CMBS) volume", "trace-absx-par", LAG_M),
    ("trace_conv", "Convertibles volume", "trace-conv-par", LAG_M),
    ("trace_eln", "ELN volume", "trace-eln-par", LAG_M),
    ("trace_chrc", "Church plans volume", "trace-chrc-par", LAG_M),
]

# FINRA IDS STAR daily section totals (fetchers/finra_ids_star.py SECTIONS +
# SINGLE_ROWS; stored cycle:star-<section>-par). 7-day publication lag.
STAR_SECTIONS = [
    ("star_tba", "STAR: TBA", "star-tba-par"),
    ("star_spec", "STAR: specified pools", "star-spec-par"),
    ("star_agcmo", "STAR: agency CMO", "star-agcmo-par"),
    ("star_nagcmo", "STAR: non-agency CMO", "star-nagcmo-par"),
    ("star_nagcmbs", "STAR: non-agency CMBS", "star-nagcmbs-par"),
    ("star_agcmbs", "STAR: agency CMBS", "star-agcmbs-par"),
    ("star_abs", "STAR: ABS", "star-abs-par"),
    ("star_clo", "STAR: CLO", "star-clo-par"),
]
LAG_STAR = {"expected_lag_days": 7, "max_age_days": 21}

# Additional OFR Hedge Fund Monitor rows from config.yaml `cycle_series`
# (batch 13; stored as cycle:<id> by the cycle job — same fpf dataset).
# Mnemonics verified against the hf/v1 metadata endpoint 2026-10-08.
# (registry id, name, unit, mnemonic, direction)
HF_CYCLE = [
    ("hf-treasury-long", "HF long Treasury cash holdings", "$",
     "FPF-ASSETCLASS_LTREASURY_SUM", "+"),
    ("hf-ust-short", "HF short Treasury exposure", "$",
     "FPF-ASSETCLASS_STREASURY_SUM", "+"),
    ("hf-nav", "Qualifying HF net assets", "$", "FPF-ALLQHF_NAV_SUM", "+"),
    ("hf-ird-gne", "HF rates-derivatives gross notional", "$",
     "FPF-ASSETCLASS_IRD_GNE_SUM", "+"),
    ("hf-rv-lev", "Relative-value HF leverage ratio", "x",
     "FPF-STRATEGY_RV_LEVERAGERATIO_NAVWMEAN", "+"),
    ("hf-multi-lev", "Multi-strategy HF leverage ratio", "x",
     "FPF-STRATEGY_MULTI_LEVERAGERATIO_NAVWMEAN", "+"),
    ("hf-macro-lev", "Macro HF leverage ratio", "x",
     "FPF-STRATEGY_MACRO_LEVERAGERATIO_NAVWMEAN", "+"),
    ("hf-credit-gne", "HF credit gross notional", "$",
     "FPF-ASSETCLASS_CREDIT_GNE_SUM", "+"),
    ("hf-credit-long", "HF credit long notional", "$",
     "FPF-ASSETCLASS_CREDIT_LGNE_SUM", "+"),
    ("hf-credit-short", "HF credit short notional", "$",
     "FPF-ASSETCLASS_CREDIT_SGNE_SUM", "+"),
    ("hf-eq-gne", "HF equity gross notional", "$",
     "FPF-ASSETCLASS_EQUITIES_GNE_SUM", "+"),
    ("hf-eq-long", "HF equity long notional", "$",
     "FPF-ASSETCLASS_EQUITIES_LGNE_SUM", "+"),
    ("hf-eq-short", "HF equity short notional", "$",
     "FPF-ASSETCLASS_EQUITIES_SGNE_SUM", "+"),
    ("hf-fx-gne", "HF FX gross notional", "$",
     "FPF-ASSETCLASS_FX_GNE_SUM", "+"),
    ("hf-pb-borrow", "HF prime-brokerage borrowing", "$",
     "FPF-BORROW_PRIMEBROKER_SUM", "+"),
    ("hf-repo-repo", "HF repo exposure", "$",
     "FPF-ASSETCLASS_REPO_REPO_SUM", "+"),
    ("hf-repo-rrepo", "HF reverse-repo exposure", "$",
     "FPF-ASSETCLASS_REPO_REVERSEREPO_SUM", "+"),
    ("hf-lev-11to50", "HF leverage: funds 11-50", "x",
     "FPF-ALLQHF_GAVN11TO50_LEVERAGERATIO_AVERAGE", "+"),
    ("hf-lev-51p", "HF leverage: funds 51+", "x",
     "FPF-ALLQHF_GAVN51_LEVERAGERATIO_AVERAGE", "+"),
    ("hf-rv-cash", "RV HF unencumbered cash % of NAV", "%",
     "FPF-STRATEGY_RV_CASHRATIO_NAVWMEAN", "-"),
    ("hf-multi-cash", "Multi-strat HF unencumbered cash % of NAV", "%",
     "FPF-STRATEGY_MULTI_CASHRATIO_NAVWMEAN", "-"),
    ("hf-cds-stress-p5", "HF +250bp credit shock: 5th pct", "%",
     "FPF-ALLQHF_CDSUP250BPS_P5", "+"),
    ("hf-cds-stress-p50", "HF +250bp credit shock: median", "%",
     "FPF-ALLQHF_CDSUP250BPS_P50", "+"),
]

# 16.4 ETF momentum set. Price closes live at cycle:etf-<T>-price (Yahoo, 1Y)
# from fetchers/ishares_etf.py for these 13 (verified in ETF_UNIVERSE).
ETF_MOM_TICKERS = ["SGOV", "BIL", "VGSH", "VGIT", "GOVT", "EDV", "EMB", "XLK",
                   "XLF", "XLE", "GLD", "HYG", "LQD", "KRE"]
# Rest of the 18-ETF set: no cycle:etf-<T>-price series (not in ETF_UNIVERSE,
# no free verified feed wired) — registered n/a.
ETF_MOM_MISSING = ["XLI", "XLV", "SMH", "LEMB"]
ETF_MOM_LAGS = (1, 5)  # 1d and 5d momentum

# 3.6b flows basket extension (16.4). No etf_flow_<ticker> series exist for
# any of these (the 3.6b DERIVED shares-outstanding x NAV self-logging is not
# built; etf.com tables are partial; vendor feeds are bot-walled) — all n/a.
ETF_FLOW_TICKERS = ["SGOV", "BIL", "EMB", "GLD", "XLK", "XLE", "XLI", "XLV",
                    "SMH", "VGSH", "VGIT", "GOVT", "EDV", "LEMB"]
# Cash-like / Treasury ETFs are context only (inflows can mean flight to cash;
# shown, excluded from composites). Risk ETFs score outflow = stress (-).
ETF_FLOW_CONTEXT = {"SGOV", "BIL", "VGSH", "VGIT", "GOVT", "EDV"}

# Market-activity log pre-transform map: registry id -> raw input series.
# The stress job log-transforms each volume series in materialize_derived();
# the engine's ± branch then scores |z| of log volume (16.4). Worker A's
# stress_score.py is not touched.
ACTIVITY_LOG_INPUTS: dict[str, str] = (
    {rid: f"cycle:{skey}" for rid, _nm, skey, _lag in TRACE_ROWS}
    | {rid: f"cycle:{skey}" for rid, _nm, skey in STAR_SECTIONS}
)


def _build_indicators() -> list[dict]:
    I = []

    # ---- Volatility -------------------------------------------------------
    I.append(_ind("vix", "Volatility", "VIX", "idx", "+", "D", "core",
                  "cycle:vix", "FRED VIXCLS", "primary"))
    I.append(_ind("vix_term", "Volatility", "VIX 3M minus VIX (term structure)",
                  "pts", "-", "D", "core", None, "FRED VXVCLS − VIXCLS", "derived",
                  inputs=["cycle:vix3m", "cycle:vix"], formula="VXVCLS − VIXCLS",
                  note="cycle:vix3m is fetched by the stress job (FRED VXVCLS); "
                       "VIX/VIX3M ratio shown on tap-through only"))
    I.append(_ind("spx_rv21", "Volatility", "SPX 21d realized vol", "%", "+", "D",
                  "core", None, "computed from SPX closes", "derived",
                  inputs=["idx:SPX"], formula="ann(std(21d log returns)) * sqrt(252)"))
    I.append(_ind("move", "Volatility", "Bond vol (MOVE proxy)", "%", "+", "D",
                  "core", None, "PROXY: realized vol of 10Y yield changes", "proxy",
                  inputs=["cycle:us10y"],
                  formula="21d realized vol of FRED DGS10 daily changes",
                  redistributable="yes",  # pure transform of FRED DGS10
                  note="PROXY for ICE BofA MOVE: Yahoo ^MOVE is dead (voldash.py:4-9). "
                       "Hatched border; excluded from composites unless include-proxies is on"))
    I.append(_ind("ovx", "Volatility", "OVX (oil vol)", "idx", "+", "D", "extended",
                  "cycle:ovx", "FRED OVXCLS", "primary",
                  note="fetched by the stress job (FRED OVXCLS)"))
    I.append(_ind("gvz", "Volatility", "GVZ (gold vol)", "idx", "+", "D", "extended",
                  "cycle:gvz", "FRED GVZCLS", "primary",
                  note="fetched by the stress job (FRED GVZCLS)"))
    I.append(_ind("vvix", "Volatility", "VVIX (vol of VIX)", "idx", "+", "D", "extended",
                  "cycle:vvix", "Yahoo ^VVIX", "primary"))
    I.append(_na("skew", "Volatility", "SKEW", "idx", "+", "D",
                 "no free daily feed: Cboe historical CSV URLs need confirmation and "
                 "Yahoo ^SKEW coverage is unverified"))
    I.append(_na("vix9d", "Volatility", "VIX9D", "idx", "+", "D",
                 "no free daily feed: Cboe CSV URLs need confirmation"))
    I.append(_na("vix1d", "Volatility", "VIX1D", "idx", "+", "D",
                 "no free daily feed: Cboe CSV URLs need confirmation"))

    # ---- Credit ------------------------------------------------------------
    I.append(_ind("hy_oas", "Credit", "US HY OAS", "%", "+", "D", "core",
                  "cycle:hy-oas", "FRED BAMLH0A0HYM2", "primary"))
    I.append(_ind("ig_oas", "Credit", "US IG OAS", "%", "+", "D", "core",
                  "cycle:ig-oas", "FRED BAMLC0A0CM", "primary"))
    I.append(_ind("ccc_oas", "Credit", "CCC and lower OAS", "%", "+", "D", "core",
                  "cycle:ccc-oas", "FRED BAMLH0A3HYC", "primary"))
    I.append(_ind("hy_ig_gap", "Credit", "HY OAS minus IG OAS", "pp", "+", "D",
                  "extended", None, "DERIVED: FRED BAMLH0A0HYM2 − BAMLC0A0CM", "derived",
                  inputs=["cycle:hy-oas", "cycle:ig-oas"], formula="HY OAS − IG OAS"))
    I.append(_ind("em_corp_oas", "Credit", "EM corporate OAS", "%", "+", "D", "extended",
                  "cycle:em-corp-oas", "FRED BAMLEMCBPIOAS", "primary",
                  note="fetched by the stress job; PROXY for EMBI-type sovereign spreads per spec 3.2"))

    # ---- Official composite stress indexes (validation refs; excluded from
    # ---- OSM composites per spec 3.3 — avoids circularity)
    I.append(_ind("ofr_fsi", "Official refs", "OFR Financial Stress Index", "idx",
                  "+", "D", "extended", "cycle:ofr-fsi",
                  "financialresearch.gov FSI daily CSV", "primary",
                  composite_excluded=True,
                  note="fetched by the stress job; full component columns also in the CSV"))
    I.append(_ind("stlfsi4", "Official refs", "St. Louis Fed FSI", "idx", "+", "W",
                  "extended", "cycle:stlfsi", "FRED STLFSI4", "primary",
                  composite_excluded=True, **LAG_W,
                  note="negative = below-average stress"))
    I.append(_ind("nfci", "Official refs", "Chicago Fed NFCI", "idx", "+", "W", "core",
                  "cycle:nfci", "FRED NFCI", "primary",
                  composite_excluded=True, **LAG_W))
    I.append(_ind("anfci", "Official refs", "Chicago Fed adjusted NFCI", "idx", "+", "W",
                  "extended", "cycle:anfci", "FRED ANFCI", "primary",
                  composite_excluded=True, **LAG_W,
                  note="fetched by the stress job (FRED ANFCI)"))
    I.append(_ind("kcfsi", "Official refs", "Kansas City Fed FSI", "idx", "+", "M",
                  "extended", "cycle:kcfsi", "FRED KCFSI", "primary",
                  composite_excluded=True, **LAG_M,
                  note="fetched by the stress job (FRED KCFSI)"))

    # ---- Funding ------------------------------------------------------------
    I.append(_ind("sofr_iorb", "Funding", "SOFR minus IORB", "pp", "+", "D", "core",
                  None, "DERIVED: FRED SOFR − IORB", "derived",
                  inputs=["cycle:sofr", "cycle:iorb"], formula="SOFR − IORB"))
    I.append(_ind("on_rrp", "Funding", "ON RRP take-up", "$bn", "±", "D", "core",
                  "cycle:rrp-on", "FRED RRPONTSYD", "primary"))
    I.append(_ind("tga", "Funding", "Treasury General Account", "$m", "+", "W",
                  "extended", "cycle:tga", "FRED WTREGEN", "primary", **LAG_W))
    I.append(_ind("reserves", "Funding", "Reserve balances", "$m", "-", "W", "extended",
                  "macro:fed-reserve-balances", "FRED WRESBAL", "primary", **LAG_W,
                  note="stored as macro:fed-reserve-balances by the FRED job "
                       "(config `series:` section)"))
    I.append(_ind("disc_window", "Funding", "Primary credit (discount window) borrowing",
                  "$m", "+", "W", "extended", "cycle:disc-window", "FRED WLCFLPCL",
                  "primary", **LAG_W,
                  note="fetched by the stress job (FRED WLCFLPCL)"))
    I.append(_ind("swap_lines", "Funding", "Central bank liquidity swaps outstanding",
                  "$m", "+", "W", "extended", "cycle:swap-lines", "FRED SWPT",
                  "primary", **LAG_W,
                  note="fetched by the stress job (FRED SWPT)"))
    I.append(_ind("cp_spread", "Funding", "AA financial − AA nonfinancial 90d CP", "pp",
                  "+", "D", "extended", None, "DERIVED: FRED RIFSPPFAAD90D − RIFSPPNAAD90D",
                  "derived",
                  inputs=["cycle:cp-fin-aa90", "cycle:cp-nonfin-aa90"],
                  formula="RIFSPPFAAD90D − RIFSPPNAAD90D",
                  note="both legs fetched by the stress job"))

    # ---- Treasury plumbing ---------------------------------------------------
    I.append(_ind("curve_2s10s", "Treasury plumbing", "10Y minus 2Y", "%", "-", "D",
                  "extended", "cycle:t10y2y", "FRED T10Y2Y", "primary"))
    I.append(_ind("term_prem", "Treasury plumbing", "10Y term premium (ACM model)", "%",
                  "+", "M", "extended", "cycle:term-prem",
                  "NY Fed ACM model (monthly CSV)", "primary", **LAG_M,
                  note="fetched by the stress job; TERMYld == ACMTP10 verified against "
                       "the ACM workbook. Kim-Wright THREEFYTP10 exists on FRED but is a "
                       "different model — never mixed in. Monthly: no daily velocity"))
    I.append(_ind("real_10y", "Treasury plumbing", "10Y real yield", "%", "+", "D",
                  "core", "cycle:real-10y", "FRED DFII10", "primary"))
    I.append(_ind("bei_10y", "Treasury plumbing", "10Y breakeven", "%", "+", "D", "core",
                  "cycle:breakeven-10y", "FRED T10YIE", "primary"))
    I.append(_ind("fwd_5y5y", "Treasury plumbing", "5y5y forward inflation", "%", "+",
                  "D", "extended", "cycle:infl-5y5y", "FRED T5YIFR", "primary"))
    I.append(_ind("basis_stress", "Treasury plumbing", "Basis-trade stress", "0-100", "+",
                  "D", "core", "risk:basis_stress", "os-bloom risk engine (recomputed daily)",
                  "primary",
                  note="mean of SOFR-IORB / RRP-drain / HF repo / dealer-fails sub-scores (risk.py)"))
    I.append(_ind("auction_stress", "Treasury plumbing", "Auction stress", "0-100", "+",
                  "E", "core", "risk:auction_stress", "os-bloom risk engine (recomputed daily)",
                  "primary", **LAG_E,
                  note="mean over Note-2Y..Bond-30Y buckets of bid-to-cover softness + "
                       "dealer takedown (risk.py)"))
    I.append(_ind("lev_fund_short", "Treasury plumbing",
                  "Leveraged-fund 10Y futures net (TFF)", "contracts", "+", "W",
                  "extended", "cftc:tff:043602:lev_money",
                  "CFTC Traders in Financial Futures", "primary", **LAG_W,
                  note="fallback series cycle:cot-ust-10y (legacy COT) also exists"))
    for b in AUCTION_BUCKETS:
        I.append(_ind(f"auction_btc_{b.lower().replace('-', '_')}", "Treasury plumbing",
                      f"Auction bid-to-cover: {b}", "x", "-", "E", "extended",
                      f"auction:{b}:bid_to_cover", "Treasury FiscalData auctions API",
                      "primary", **LAG_E,
                      note="FiscalData backfills to 1979 (bid-to-cover published only "
                           "from ~2000); sibling series auction:<bucket>:{indirect_pct,"
                           "direct_pct,dealer_pct,offering,high_yield} are stored by "
                           "the auctions job"))

    # ---- Auction detail (16.4): takedown splits per bucket -----------------------
    # All stored by the auctions job from FiscalData (deep backfill to 1979; the
    # "about 5 weeks" gap in 16.8 is closed — full history is pulled). Tail has
    # no free source: FiscalData publishes high yield but no when-issued
    # benchmark, so tail = high yield − WI cannot be computed or backfilled.
    for b in AUCTION_BUCKETS:
        bl = b.lower().replace("-", "_")
        I.append(_ind(f"auction_ind_{bl}", "Treasury plumbing",
                      f"Auction indirect takedown: {b}", "%", "-", "E", "extended",
                      f"auction:{b}:indirect_pct", "Treasury FiscalData auctions API",
                      "primary", **LAG_E,
                      note="share of accepted going to indirect bidders (foreign + "
                           "domestic end-users); a falling share is stress"))
        I.append(_ind(f"auction_dir_{bl}", "Treasury plumbing",
                      f"Auction direct takedown: {b}", "%", "±", "E", "extended",
                      f"auction:{b}:direct_pct", "Treasury FiscalData auctions API",
                      "primary", **LAG_E,
                      note="share of accepted going to direct bidders (domestic "
                           "end-users); two-sided — both collapse and surge are "
                           "informative about demand composition"))
        I.append(_ind(f"auction_dlr_{bl}", "Treasury plumbing",
                      f"Auction dealer takedown: {b}", "%", "+", "E", "extended",
                      f"auction:{b}:dealer_pct", "Treasury FiscalData auctions API",
                      "primary", **LAG_E,
                      note="share of accepted going to primary dealers; a high "
                           "dealer share means weak real-money demand"))
        I.append(_na(f"auction_tail_{bl}", "Treasury plumbing",
                     f"Auction tail: {b}", "bp", "+", "E",
                     "no free when-issued yield feed: Treasury FiscalData publishes "
                     "high yield (high_investment_rate for bills) but no WI benchmark, "
                     "so tail = high yield − WI cannot be computed from any free "
                     "source; no backfill possible"))

    # ---- Equity internals (+ sentiment folded in per 16.5) --------------------
    I.append(_ind("spx_vs_high", "Equity internals", "SPX vs 52w high", "%", "-", "D",
                  "core", None, "computed from SPX closes", "derived",
                  inputs=["idx:SPX"], formula="last / trailing-252d max − 1"))
    I.append(_ind("tlt_mom60", "Equity internals", "TLT 60d momentum", "%", "-", "D",
                  "core", None, "computed from TLT closes", "derived",
                  inputs=["cycle:tlt"], formula="last / 60-obs-ago − 1",
                  note="home_radar computes this on the fly from cycle:tlt; no stored series"))
    I.append(_ind("ew_vs_spx", "Equity internals", "Equal-weight vs SPX", "ratio", "-",
                  "D", "core", "cycle:spw-spx", "Yahoo RSP/SPY ratio", "primary"))
    I.append(_ind("dispersion5", "Equity internals", "Cross-sectional dispersion (5d)",
                  "%", "+", "D", "core", "movers:dispersion-5d",
                  "os-bloom movers job (std of 5d log-returns)", "primary"))
    for t in ("2y", "5y", "10y", "30y"):
        I.append(_ind(f"cta_crowd_{t}", "Equity internals", f"CTA crowdedness |z| ({t})",
                      "z", "+", "D", "core" if t == "10y" else "extended", f"risk:cta_z_{t}",
                      "os-bloom risk engine (TFF leveraged funds)", "primary",
                      note="score uses |z|; 10y is the headline leg"))
    I.append(_na("sox_spx", "Equity internals", "SOX relative to SPX", "ratio", "-",
                 "D", "no ^SOX series in the store (no free verified feed wired)"))
    I.append(_ind("aaii_bull_bear", "Equity internals", "AAII bull-bear spread", "pts",
                  "±", "W", "extended", "cycle:aaii-spread", "AAII sentiment survey (.xls)",
                  "primary", **LAG_W))
    I.append(_ind("put_call", "Equity internals", "Put/call SPX+SPXW", "ratio", "+", "D",
                  "extended", "cycle:pc-spx", "Cboe", "primary",
                  note="history building (~22 obs; percentile needs 60+, confidence 252)"))
    for pid, nm, skey in (
        ("put_call_total", "Put/call total", "cycle:pc-total"),
        ("put_call_equity", "Put/call equity", "cycle:pc-equity"),
        ("put_call_index", "Put/call index", "cycle:pc-index"),
    ):
        I.append(_ind(pid, "Equity internals", nm, "ratio", "+", "D", "extended",
                      skey, "Cboe", "primary",
                      note="history building (~22 obs; percentile needs 60+, confidence 252)"))
    I.append(_ind("umich", "Equity internals", "UMich consumer sentiment", "idx", "-",
                  "M", "extended", "cycle:umich", "FRED UMCSENT", "primary", **LAG_M,
                  note="sentiment row (16.4); folded into Equity internals per 16.5"))

    # ---- ETF 1d/5d price momentum (16.4) -------------------------------------------
    # DERIVED from Yahoo daily closes (cycle:etf-<T>-price, 1Y history); the
    # stress job computes them in materialize_derived(). Cash-like SGOV/BIL are
    # context only (inflows can mean flight to cash). Direction −: falling
    # momentum is stress.
    for t in ETF_MOM_TICKERS:
        for lag in ETF_MOM_LAGS:
            I.append(_ind(f"etf_mom{lag}d_{t.lower()}", "Equity internals",
                          f"{t} {lag}d price momentum", "%", "-", "D", "extended",
                          None, f"DERIVED: Yahoo {t} closes", "derived",
                          inputs=[f"cycle:etf-{t}-price"],
                          formula=f"last / {lag}-obs-ago − 1",
                          note="computed by the stress job from Yahoo daily closes" +
                               ("; cash-like: context only, inflows can mean flight "
                                "to cash" if t in {"SGOV", "BIL"} else "")))
    for t in ETF_MOM_MISSING:
        for lag in ETF_MOM_LAGS:
            I.append(_na(f"etf_mom{lag}d_{t.lower()}", "Equity internals",
                         f"{t} {lag}d price momentum", "%", "-", "D",
                         f"no cycle:etf-{t}-price series: {t} is not in the ETF price "
                         f"universe (fetchers/ishares_etf.py ETF_UNIVERSE); no free "
                         f"verified feed wired"))

    # ---- ETF flows basket extension (16.4 / 3.6b) -----------------------------------
    # No etf_flow_<ticker> series exist for any of these: the 3.6b DERIVED
    # shares-outstanding x NAV self-logging is not built; etf.com daily tables
    # are partial (a ticker not listed that day is "not listed", never zero);
    # stockanalysis / iShares / etfdb are bot-walled. All n/a, tracked in the
    # Data Gaps list until a feed exists.
    for t in ETF_FLOW_TICKERS:
        ctx = t in ETF_FLOW_CONTEXT
        I.append(_na(f"etf_flow_{t.lower()}", "Equity internals",
                     f"{t} ETF net creations (est.)", "$", "±" if ctx else "-",
                     "D",
                     ("context only (cash-like/Treasury ETF; inflows can mean "
                      "flight to cash — shown, excluded from composites). "
                      if ctx else "outflow = stress. ") +
                     "no free daily flows feed wired: 3.6b DERIVED "
                     "shares-outstanding x NAV self-logging is not built; "
                     "etf.com daily tables are partial; vendor feeds are bot-walled"))

    # ---- Banks ----------------------------------------------------------------
    I.append(_ind("kre_dd", "Banks", "KRE drawdown from 52w high", "%", "+", "D",
                  "extended", None, "computed from KRE closes", "derived",
                  inputs=["cycle:etf-kre"], formula="last / trailing-252d max − 1"))
    I.append(_ind("mtg_spread", "Banks", "30Y mortgage rate minus 10Y UST", "pp", "+",
                  "W", "extended", None, "DERIVED: FRED MORTGAGE30US − DGS10", "derived",
                  inputs=["macro:us-mortgage-30y", "cycle:us10y"],
                  formula="MORTGAGE30US − DGS10", **LAG_W,
                  note="MORTGAGE30US stored as macro:us-mortgage-30y by the FRED job"))
    I.append(_ind("claims", "Banks", "Initial jobless claims", "k", "+", "W", "core",
                  "cycle:claims", "FRED ICSA", "primary", **LAG_W))

    # ---- Global -----------------------------------------------------------------
    I.append(_ind("usd_broad", "Global", "Broad dollar index", "idx", "+", "D",
                  "extended", "cycle:usd-broad", "FRED DTWEXBGS", "primary"))
    I.append(_ind("brent", "Global", "Brent crude", "$", "±", "D", "extended",
                  "macro:brent-crude", "FRED DCOILBRENTEU", "primary",
                  note="stored as macro:brent-crude by the FRED job (config `series:` section)"))
    I.append(_ind("epu", "Global", "Economic Policy Uncertainty (daily)", "idx", "+",
                  "D", "extended", "cycle:epu", "FRED USEPUINDXD", "primary",
                  note="fetched by the stress job (FRED USEPUINDXD)"))

    # ---- Rates levels (16.4) -----------------------------------------------------
    # abs_velocity=True: velocity alerts use the absolute value (a fast fall is
    # a flight-to-quality shock, not calm). The engine's velocity layer already
    # ranks |raw| change percentiles (stress_score.velocity); the flag is the
    # registry-level declaration of the 16.4 rule — no engine change.
    for rid, nm, sid, frd in (
        ("us2y", "US 2Y yield", "cycle:us2y", "FRED DGS2"),
        ("us10y", "US 10Y yield", "cycle:us10y", "FRED DGS10"),
        ("us30y", "US 30Y yield", "cycle:ust30y", "FRED DGS30"),
        ("sofr", "SOFR", "cycle:sofr", "FRED SOFR"),
    ):
        I.append(_ind(rid, "Treasury plumbing", nm, "%", "+", "D", "core", sid, frd,
                      "primary", abs_velocity=True,
                      note="16.4 rate level: velocity alerts use the absolute value "
                           "(a fast fall = flight-to-quality shock, not calm)"))

    # ---- CDX (16.4) — pending daybook handoff ------------------------------------
    I.append(_na("cdx_ig_spread", "Credit", "CDX IG spread", "bp", "+", "D",
                 "awaiting daybook handoff: ~/workspace/cdx-capture/cdx_ig.csv lives on "
                 "Harry's machine; publish path undecided (spec 16.9)",
                 rung="rung 5/6: MANUAL/self-logged once the handoff lands — no backfill, no proxy",
                 redistributable="no"))  # 16.9: the daybook capture stays non-redistributable
    I.append(_na("cdx_hy_price", "Credit", "CDX HY price", "pts", "-", "D",
                 "awaiting daybook handoff (spec 16.9); keep as price, direction −, "
                 "never convert to spread without a documented formula",
                 rung="rung 5/6: MANUAL/self-logged once the handoff lands — no backfill, no proxy",
                 redistributable="no"))

    # ---- Market activity & liquidity (16.4) — two-sided ±, |z| of log volume ----
    # The engine's ± branch scores |z| but cannot log-transform (stress_score.py
    # is owned by Worker A), so every activity series is registered DERIVED: the
    # stress job log-transforms the raw $m volume in materialize_derived() and
    # the engine scores |z| of log volume against its window. No engine change.
    for rid, nm, skey, lag in TRACE_ROWS:
        I.append(_ind(rid, "Market activity", nm, "log($m)", "±",
                      "D" if lag is LAG_D else "M", "extended", None,
                      "DERIVED: log of FINRA TRACE volume", "derived",
                      inputs=[f"cycle:{skey}"], formula="log(volume)", **lag,
                      note="log pre-transform by the stress job; engine scores |z| "
                           "of log volume (16.4). Monthly-product rows lag; history "
                           "from Oct 2023 (~3Y window max, later episodes only). "
                           "Treasury venue is ATS vs dealer-to-customer only: no "
                           "interdealer split exists in the file"))
    for rid, nm, skey in STAR_SECTIONS:
        I.append(_ind(rid, "Market activity", nm, "log($m)", "±", "D", "extended",
                      None, "DERIVED: log of FINRA STAR volume", "derived",
                      inputs=[f"cycle:{skey}"], formula="log(volume)", **LAG_STAR,
                      note="log pre-transform by the stress job; engine scores |z| "
                           "of log volume (16.4). 7-day publication lag (16.6)"))
    I.append(_ind("offrun_share", "Market activity",
                  "Off-the-run share of Treasury volume", "%", "+", "D", "extended",
                  None, "DERIVED: FINRA TRACE", "derived",
                  inputs=["cycle:trace-ust-offrun-par", "cycle:trace-ust-onrun-par"],
                  formula="off-run / (on-run + off-run)",
                  note="liquidity-preference proxy, not an on/off-the-run spread"))

    # ---- Hedge fund leverage (16.4; Q tag, no velocity) ----------------------------
    # 40 indicators, all mnemonics verified against the hf/v1 metadata endpoint
    # 2026-10-08. Q-tag: the engine skips velocity for freq "Q"
    # (stress_score.FREQ_NO_VELOCITY). Source table on every cell: OFR Hedge
    # Fund Monitor, dataset=fpf (SEC Form PF aggregates); the cycle:<id> rows
    # are the same dataset via the cycle job's ofr: source key.
    for hid, nm, unit, mn, dirc in OFR_HF:
        I.append(_ind(hid, "Hedge fund leverage", nm, unit, dirc, "Q", "extended",
                      f"ofr:{mn}", "OFR Hedge Fund Monitor (SEC Form PF aggregates)",
                      "primary", **LAG_Q,
                      note=f"mnemonic {mn}; OFR Hedge Fund Monitor dataset=fpf (SEC "
                           f"Form PF aggregates); quarterly, published with a lag; "
                           f"Q tag, no velocity; 4-quarter change on tap"))
    for hid, nm, unit, mn, dirc in (
        ("hf-repo-borrow", "HF repo borrowing", "$", "FPF-BORROW_REPO_SUM", "+"),
        ("hf-repo-share-top10", "HF repo borrowing: top-10 share", "%",
         "FPF-BORROW_REPO_GAVN10_PERCENT", "+"),
        ("hf-repo-share-11to50", "HF repo borrowing: 11–50 share", "%",
         "FPF-BORROW_REPO_GAVN11TO50_PERCENT", "+"),
        ("hf-repo-share-51p", "HF repo borrowing: 51+ share", "%",
         "FPF-BORROW_REPO_GAVN51_PERCENT", "+"),
    ):
        I.append(_ind(hid, "Hedge fund leverage", nm, unit, dirc, "Q", "extended",
                      f"cycle:{hid}", "OFR Hedge Fund Monitor (SEC Form PF aggregates)",
                      "primary", **LAG_Q,
                      note=f"mnemonic {mn}; OFR Hedge Fund Monitor dataset=fpf via "
                           f"the cycle job; Q tag, no velocity; 4-quarter change "
                           f"on tap"))
    for hid, nm, unit, mn, dirc in HF_CYCLE:
        I.append(_ind(hid, "Hedge fund leverage", nm, unit, dirc, "Q", "extended",
                      f"cycle:{hid}", "OFR Hedge Fund Monitor (SEC Form PF aggregates)",
                      "primary", **LAG_Q,
                      note=f"mnemonic {mn}; OFR Hedge Fund Monitor dataset=fpf via "
                           f"the cycle job; Q tag, no velocity; 4-quarter change "
                           f"on tap"))

    # DERIVED/PROXY rows are materialized by materialize_derived() into
    # stress:<id> series so the scoring engine can read them like primaries.
    # n/a rows keep series_id=None (they surface in build_gaps()).
    for i in I:
        if i["tag"] in {"derived", "proxy"}:
            i["series_id"] = f"stress:{i['id']}"

    return I


INDICATORS: list[dict] = _build_indicators()


# Overall Gauge weights (spec 16.5), keyed by THIS registry's category names.
# Worker A's engine groups composites by the category string verbatim and
# looks weights up with weights.get(category); these keys must match exactly.
# "Official refs" gets weight 0.0: shown as its own validation panel,
# excluded from the gauge (spec 3.3, avoids circularity).
STRESS_WEIGHTS: dict[str, float] = {
    "Volatility": 12.0,
    "Credit": 15.0,
    "Funding": 15.0,
    "Treasury plumbing": 15.0,
    "Equity internals": 10.0,
    "Banks": 8.0,
    "Global": 5.0,
    "Market activity": 10.0,
    "Hedge fund leverage": 10.0,
    "Official refs": 0.0,
}


# ---------------------------------------------------------------------------
# DERIVED series materialization. The scoring engine (collector.stress_score)
# reads store.points(series_id) per indicator; DERIVED/PROXY rows therefore get
# their values computed here from real input series and upserted as
# stress:<indicator-id>. Pure data computation — no scoring logic.
# Alignment rules: inner join on common dates, or carry-forward for
# cross-frequency pairs (never interpolate across a gap).
# ---------------------------------------------------------------------------
def _inner(*hists: dict) -> list:
    dates = set(hists[0])
    for h in hists[1:]:
        dates &= set(h)
    return sorted(dates)


def _carry_forward(a: dict, b: dict) -> dict:
    """Align B onto A's dates, carrying the last B observation forward."""
    b_dates = sorted(b)
    out = {}
    for d in sorted(a):
        prior = [bd for bd in b_dates if bd <= d]
        if prior:
            out[d] = b[prior[-1]]
    return out


def _spread(a: dict, b: dict, carry_b: bool = False) -> dict:
    b_al = _carry_forward(a, b) if carry_b else b
    return {d: a[d] - b_al[d] for d in _inner(a, b_al)}


def _realized_vol(cl: dict, window: int = 21, of_changes: bool = False) -> dict:
    """Annualized realized vol: of log returns (prices) or of daily changes."""
    import math
    ds = sorted(cl)
    out = {}
    for n in range(len(ds)):
        if n + 1 < window + 1:
            continue
        seg = [cl[ds[k]] for k in range(n - window, n + 1)]
        if of_changes:
            rets = [seg[k] - seg[k - 1] for k in range(1, len(seg))]
        else:
            if any(p <= 0 for p in seg):
                continue
            rets = [math.log(seg[k] / seg[k - 1]) for k in range(1, len(seg))]
        if len(rets) < 2:
            continue
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        out[ds[n]] = math.sqrt(var) * math.sqrt(252) * 100.0
    return out


def _drawdown(cl: dict, window: int = 252, min_obs: int = 5) -> dict:
    """Percent drawdown from the trailing-window max (positive = deeper)."""
    ds = sorted(cl)
    out = {}
    for n in range(len(ds)):
        if n + 1 < min_obs:
            continue
        seg = [cl[ds[k]] for k in range(max(0, n - window + 1), n + 1)]
        peak = max(seg)
        if peak > 0:
            out[ds[n]] = (1.0 - cl[ds[n]] / peak) * 100.0
    return out


def _momentum(cl: dict, lag: int = 60) -> dict:
    ds = sorted(cl)
    out = {}
    for n in range(len(ds)):
        if n < lag or cl[ds[n - lag]] == 0:
            continue
        out[ds[n]] = (cl[ds[n]] / cl[ds[n - lag]] - 1.0) * 100.0
    return out


def _log(cl: dict) -> dict:
    """Natural log of positive values — the 16.4 pre-transform for Market
    activity rows. Non-positive points are dropped (volumes are $m > 0)."""
    import math
    return {d: math.log(v) for d, v in cl.items() if v and v > 0}


def materialize_derived(store: Store) -> dict[str, str]:
    """Compute every DERIVED/PROXY indicator from its input series.

    Returns {indicator_id: "ok (N pts)" | "skipped: ..."}; per-indicator
    isolation — one bad input degrades only that row. Includes the 16.4
    log-volume pre-transforms (Market activity) and the ETF 1d/5d momentum
    rows; the engine then scores them with its existing ±/direction logic.
    """
    results: dict[str, str] = {}
    get = store.points

    def put(iid: str, pts: dict) -> None:
        if not pts:
            results[iid] = "skipped: no aligned input points"
            return
        store.upsert_points(f"stress:{iid}", sorted(pts.items()))
        results[iid] = f"ok ({len(pts)} pts)"

    builders = {
        "vix_term": lambda: _spread(get("cycle:vix3m"), get("cycle:vix")),
        "spx_rv21": lambda: _realized_vol(get("idx:SPX")),
        "move": lambda: _realized_vol(get("cycle:us10y"), of_changes=True),
        "hy_ig_gap": lambda: _spread(get("cycle:hy-oas"), get("cycle:ig-oas")),
        "sofr_iorb": lambda: _spread(get("cycle:sofr"), get("cycle:iorb")),
        "cp_spread": lambda: _spread(get("cycle:cp-fin-aa90"), get("cycle:cp-nonfin-aa90")),
        "spx_vs_high": lambda: _drawdown(get("idx:SPX")),
        "tlt_mom60": lambda: _momentum(get("cycle:tlt"), 60),
        "kre_dd": lambda: _drawdown(get("cycle:etf-kre")),
        "mtg_spread": lambda: _spread(get("cycle:us-mortgage-30y"), get("cycle:us10y"),
                                      carry_b=True),
        "offrun_share": lambda: (
            lambda off, on: {d: 100.0 * off[d] / (off[d] + on[d])
                             for d in _inner(off, on) if off[d] + on[d] > 0}
        )(get("cycle:trace-ust-offrun-par"), get("cycle:trace-ust-onrun-par")),
    }

    def _activity_log(key: str):
        return lambda: _log(get(key))

    def _etf_mom(ticker: str, lag: int):
        return lambda: _momentum(get(f"cycle:etf-{ticker}-price"), lag)

    # 16.4: log-volume pre-transforms for Market activity; 1d/5d ETF momentum.
    for _aid, _akey in ACTIVITY_LOG_INPUTS.items():
        builders.setdefault(_aid, _activity_log(_akey))
    for _t in ETF_MOM_TICKERS:
        for _lag in ETF_MOM_LAGS:
            builders.setdefault(f"etf_mom{_lag}d_{_t.lower()}", _etf_mom(_t, _lag))
    for entry in INDICATORS:
        if entry["tag"] not in {"derived", "proxy"}:
            continue
        iid = entry["id"]
        try:
            put(iid, builders[iid]())
        except KeyError:
            results[iid] = "skipped: no builder defined"
            log.warning("stress: no derived builder for %s", iid)
        except Exception as exc:  # noqa: BLE001 — per-indicator isolation
            results[iid] = f"error: {type(exc).__name__}: {exc}"
            log.warning("stress derived %s failed: %s", iid, exc)
    return results
# ---------------------------------------------------------------------------
# Missing-source fetches. Per-source try/except: one dead source degrades that
# series only and keeps its last value (flagged stale by the scoring layer).
# ---------------------------------------------------------------------------
async def _fetch_fred(store: Store, fred_id: str, sid: str, api_key: str,
                      get_text: GetText) -> int:
    pts = await _fred.fetch_series(fred_id, api_key, get_text)
    if not pts:
        raise ValueError(f"FRED {fred_id} returned no points")
    store.upsert_points(sid, pts)
    return len(pts)


async def _fetch_acm(store: Store, get_text: GetText) -> int:
    """NY Fed ACM 10Y term premium, monthly. TERMYld == ACMTP10 (verified)."""
    text = await get_text(ACM_URL)
    pts = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        d = (row.get("RunDates") or "").strip()
        v = (row.get("TERMYld") or "").strip()
        if not d or not v:
            continue
        try:
            pts.append((datetime.strptime(d, "%d-%b-%Y").date(), float(v)))
        except ValueError:
            continue  # never interpolate across a bad row; skip it
    if not pts:
        raise ValueError("ACM CSV parsed to zero rows")
    store.upsert_points("cycle:term-prem", sorted(pts))
    return len(pts)


async def _fetch_ofr_fsi(store: Store, get_text: GetText) -> int:
    """OFR Financial Stress Index, daily. Column 'OFR FSI'."""
    text = await get_text(OFR_FSI_URL)
    pts = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        d = (row.get("Date") or "").strip()
        v = (row.get("OFR FSI") or "").strip()
        if not d or not v:
            continue
        try:
            pts.append((datetime.strptime(d, "%Y-%m-%d").date(), float(v)))
        except ValueError:
            continue
    if not pts:
        raise ValueError("OFR FSI CSV parsed to zero rows")
    store.upsert_points("cycle:ofr-fsi", sorted(pts))
    return len(pts)


async def refresh_stress_sources(store: Store, cfg, fred_api_key: str,
                                 get_text: GetText | None = None,
                                 get_bytes: GetBytes | None = None) -> dict:
    """Fetch genuinely missing Tier-1 sources and upsert them.

    Per-source isolation: a failure degrades only that series (last value
    stays, flagged stale downstream); the error is recorded in the result map
    and logged. Returns {source_id: "ok (N pts)" | "error: ..."}.
    """
    get_text = get_text or _default_get_text
    results: dict[str, str] = {}
    if fred_api_key:
        for fred_id, sid in MISSING_FRED.items():
            try:
                n = await _fetch_fred(store, fred_id, sid, fred_api_key, get_text)
                results[sid] = f"ok ({n} pts)"
            except Exception as exc:  # noqa: BLE001 — per-source isolation
                results[sid] = f"error: {type(exc).__name__}: {exc}"
                log.warning("stress source %s (%s) failed: %s", sid, fred_id, exc)
    else:
        for sid in MISSING_FRED.values():
            results[sid] = "error: FRED_API_KEY unset"
        log.warning("stress: FRED_API_KEY unset; %d FRED series skipped", len(MISSING_FRED))
    for label, fn in (("cycle:term-prem", _fetch_acm), ("cycle:ofr-fsi", _fetch_ofr_fsi)):
        try:
            n = await fn(store, get_text)
            results[label] = f"ok ({n} pts)"
        except Exception as exc:  # noqa: BLE001 — per-source isolation
            results[label] = f"error: {type(exc).__name__}: {exc}"
            log.warning("stress source %s failed: %s", label, exc)
    return results


async def refresh_stress_full(store: Store, cfg, fred_api_key: str,
                              get_text: GetText | None = None,
                              get_bytes: GetBytes | None = None):
    """Stress v2 Phase-1 job body: fetch missing Tier-1 sources, materialize
    DERIVED/PROXY series, then score.

    Scoring coordination (Worker A): collector.stress_score.refresh_stress(
    store, registry, weights=STRESS_WEIGHTS), where registry is the INDICATORS
    list defined in this module and weights is the spec-16.5 scheme keyed by
    this registry's category names (Worker A's DEFAULT_WEIGHTS keys do not
    match these categories). The scoring module is not expected to exist yet
    on every checkout; when it is absent the data steps still complete and the
    gap is logged instead of crashing the job. Scoring logic is NOT duplicated
    here.
    """
    fetched = await refresh_stress_sources(store, cfg, fred_api_key,
                                           get_text=get_text, get_bytes=get_bytes)
    derived = materialize_derived(store)
    store.put_doc("stress_gaps", {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "gaps": build_gaps(),
        "fetched": fetched,
        "derived": derived,
    }, source="stress")
    try:
        from collector.stress_score import refresh_stress as _score_refresh
    except ImportError:
        log.warning("stress: collector.stress_score not available yet — scoring "
                    "skipped (sources ok: %d, derived ok: %d)",
                    sum(1 for v in fetched.values() if v.startswith("ok")),
                    sum(1 for v in derived.values() if v.startswith("ok")))
        return "stress-sources"
    _score_refresh(store, INDICATORS, weights=STRESS_WEIGHTS)
    return "stress"


def build_gaps() -> list[dict]:
    """Data Gaps list (spec section 2): every n/a indicator with its ladder rung."""
    return [
        {"id": i["id"], "name": i["name"], "category": i["category"],
         "reason": i["reason"], "rung": i["rung"]}
        for i in INDICATORS if i["tag"] == "n/a"
    ]
