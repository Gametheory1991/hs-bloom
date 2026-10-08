from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from collector.config import load_config
from collector.newsletter import load_smtp_cfg
from collector.scheduler import register_jobs
from collector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]


async def fake_get(url, params=None):
    raise AssertionError("no network in tests")


async def fake_post(url, json=None, headers=None):
    raise AssertionError("no network in tests")


async def fake_bytes(url, params=None, headers=None):
    raise AssertionError("no network in tests")


def test_register_jobs_creates_all_jobs_with_config_cadences(tmp_path):
    cfg = load_config(REPO_ROOT / "config.yaml")
    store = Store(tmp_path / "t.db")
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_jobs(
        scheduler, cfg, store, get_text=fake_get, post_json=fake_post,
        get_bytes=fake_bytes, fred_api_key="k", smtp_cfg=load_smtp_cfg({"SMTP_PORT": "465"})
    )
    jobs = {j.id: j for j in scheduler.get_jobs()}
    assert set(jobs) == {
        "equity", "bonds", "macro", "news", "macro_history", "defi", "midnight",
        "refs", "refs_history", "morpho", "cycle", "ofr", "cftc_pos", "tic",
        "thirteenf", "auctions", "dealer", "insights", "risk", "stress", "country_risk",
        "gse", "xcorr", "voldash", "movers", "home_radar", "hyperscaler",
        "ai_capex", "ai_graph", "ms_capex", "ms_graph", "trace_treasury", "trace_monthly", "ice_star",
        "finra_short", "finra_margin", "tsv_capex", "tsv_graph", "tsv_watch", "newsletter",
        "ofr_stfm", "frb_ddp", "sec_ncen", "sec_nport", "sec_pfs",
        "z1_holdings", "mspd", "soma_cusip", "debt_cube",
        "worldbank", "usaspending", "coingecko", "defillama_rwa", "openfigi", "finnhub",
        "polymarket", "kalshi", "pred_edge", "fed_meetings", "finra_breadth", "finra_corp", "finra_regsho",
        "finra_capped", "finra_ids_star", "finra_factbook", "finra_factbook_annual",
        "finra_ats",
        "finra_otc",
        "ticker_stats", "bank_capex", "bank_graph",
        "bank_financials", "finance_dirs",
        "tech_capex", "tech_graph", "vendor_capex", "vendor_graph",
        "etf_capex", "etf_graph", "crypto_capex", "crypto_graph",
        "regwatch",
        "cboe_options", "ishares_etf",
        "nyfed_cmdi", "nasdaq_tape", "sec_xbrl_etf", "ofr_tff",
        "ici_flows", "nport_flows",
        "etf_holders_13f",
        "bdc_financials", "bdc_universe",
    }
    assert jobs["equity"].trigger.interval.total_seconds() == 300
    assert jobs["news"].trigger.interval.total_seconds() == 600
    assert jobs["macro"].trigger.interval.total_seconds() == 3600
    assert jobs["macro_history"].trigger.interval.total_seconds() == 86400
    assert jobs["defi"].trigger.interval.total_seconds() == 900
    assert jobs["midnight"].trigger.interval.total_seconds() == 900
    assert jobs["refs"].trigger.interval.total_seconds() == 900
    assert jobs["refs_history"].trigger.interval.total_seconds() == 86400
    assert jobs["morpho"].trigger.interval.total_seconds() == 900
    assert jobs["cycle"].trigger.interval.total_seconds() == 86400
    assert jobs["insights"].trigger.interval.total_seconds() == 1800
    assert jobs["newsletter"].trigger.interval.total_seconds() == 1800
    assert jobs["worldbank"].trigger.interval.total_seconds() == 604800
    assert jobs["usaspending"].trigger.interval.total_seconds() == 604800
    assert jobs["coingecko"].trigger.interval.total_seconds() == 86400
    assert jobs["openfigi"].trigger.interval.total_seconds() == 604800
    assert jobs["finnhub"].trigger.interval.total_seconds() == 86400
    assert jobs["finra_breadth"].trigger.interval.total_seconds() == 86400
    assert jobs["polymarket"].trigger.interval.total_seconds() == 1800
    assert jobs["kalshi"].trigger.interval.total_seconds() == 1800
    assert jobs["pred_edge"].trigger.interval.total_seconds() == 3600
    assert jobs["finra_capped"].trigger.interval.total_seconds() == 2592000
    assert jobs["bank_capex"].trigger.interval.total_seconds() == 604800
    assert jobs["bank_graph"].trigger.interval.total_seconds() == 604800
    assert jobs["bank_financials"].trigger.interval.total_seconds() == 604800
    assert jobs["finance_dirs"].trigger.interval.total_seconds() == 2592000
    assert jobs["tech_capex"].trigger.interval.total_seconds() == 604800
    assert jobs["tech_graph"].trigger.interval.total_seconds() == 604800
    assert jobs["vendor_capex"].trigger.interval.total_seconds() == 604800
    assert jobs["vendor_graph"].trigger.interval.total_seconds() == 604800
    assert jobs["crypto_capex"].trigger.interval.total_seconds() == 604800
    assert jobs["crypto_graph"].trigger.interval.total_seconds() == 604800
    assert jobs["cboe_options"].trigger.interval.total_seconds() == 86400
    assert jobs["ishares_etf"].trigger.interval.total_seconds() == 86400
    assert jobs["ofr_tff"].trigger.interval.total_seconds() == 86400
    assert jobs["sec_xbrl_etf"].trigger.interval.total_seconds() == 604800
    assert all(j.misfire_grace_time == 30 for j in jobs.values())


def test_main_builds_app(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_PATH", str(REPO_ROOT / "config.yaml"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from collector.main import build

    app, scheduler = build()
assert app.title == "hs-bloom collector"
    assert len(scheduler.get_jobs()) == 92  # +2 bank_financials/finance_dirs (finance batch), +2 bdc_financials/bdc_universe, +1 fed_meetings (pred batch), +1 etf_holders_13f, +2 ici_flows/nport_flows (was 84: +1 finra_otc (was 83: +1 ofr_tff (was 81: +1 sec_xbrl_etf, +1 nasdaq_tape (was 79: +3 cboe_options, ishares_etf, nyfed_cmdi (was 76: +1 defillama_rwa (was 75: +1 regwatch (was 74: +1 finra_breadth, +1 finra_corp, +1 finra_regsho, +1 finra_capped, +1 finra_ids_star, +1 ticker_stats, +2 bank universe, +4 tech/vendor universes, +2 etf universe, +2 crypto universe, +2 factbook, +9 batch13 (ofr_stfm, frb_ddp, sec_ncen, sec_nport, sec_pfs, z1_holdings, mspd, soma_cusip, debt_cube))))
assert app.title == "os-bloom collector"
    assert len(scheduler.get_jobs()) == 93  # +1 stress (stress-v2 phase1; was 92: +2 bank_financials/finance_dirs (finance batch), +2 bdc_financials/bdc_universe, +1 fed_meetings (pred batch), +1 etf_holders_13f, +2 ici_flows/nport_flows (was 84: +1 finra_otc (was 83: +1 ofr_tff (was 81: +1 sec_xbrl_etf, +1 nasdaq_tape (was 79: +3 cboe_options, ishares_etf, nyfed_cmdi (was 76: +1 defillama_rwa (was 75: +1 regwatch (was 74: +1 finra_breadth, +1 finra_corp, +1 finra_regsho, +1 finra_capped, +1 finra_ids_star, +1 ticker_stats, +2 bank universe, +4 tech/vendor universes, +2 etf universe, +2 crypto universe, +2 factbook, +9 batch13 (ofr_stfm, frb_ddp, sec_ncen, sec_nport, sec_pfs, z1_holdings, mspd, soma_cusip, debt_cube))))