"""Typed loading of config.yaml. Secrets come from env, never from YAML."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class IndexCfg:
    symbol: str
    name: str
    yahoo: str | None = None


@dataclass(frozen=True)
class BondCfg:
    country: str
    tenor: str
    fred: str | None = None
    bundesbank: str | None = None
    ecb: str | None = None


@dataclass(frozen=True)
class CbRateCfg:
    country: str  # must match a bonds country so the matrix row lines up
    label: str
    fred: str | None = None


@dataclass(frozen=True)
class SeriesCfg:
    id: str
    name: str
    fred: str
    unit: str
    transform: str


@dataclass(frozen=True)
class CycleSeriesCfg:
    """One market-cycle series; exactly one source field is set per entry.

    `store: true` marks an externally maintained series (written by another
    job, e.g. the thirteenf job's net-flow points): the cycle job just
    re-upserts whatever is already in the store instead of fetching.
    """
    id: str
    name: str
    unit: str
    transform: str = "none"
    hidden: bool = False           # fetched + chartable but never a panel row (usrec)
    valid_range: list[float] | None = None  # drop points outside [min, max] (corrupt feeds)
    store: bool = False            # externally maintained; re-upsert only
    fred: str | None = None
    dbnomics: str | None = None    # "PROVIDER/dataset/series"
    oecd: str | None = None        # "{flow}/{key}" under the OECD rest/data base
    cftc: str | None = None        # CFTC contract market code
    cboe: str | None = None        # exact ratio name in the CBOE daily JSON
    cftc_oi: str | None = None    # CFTC contract market code -> total futures open interest
    aaii: str | None = None        # "bull_bear_spread"
    ofr: str | None = None         # OFR Hedge Fund Monitor mnemonic (dataset=fpf)
    yahoo: str | None = None       # single Yahoo symbol, daily closes (e.g. MBB, ^VIX3M)
    yahoo_ratio: list[str] | None = None  # [numerator, denominator] yahoo symbols
    fred_spread: list[str] | None = None  # [A, B] FRED ids -> A minus B (e.g. US10Y-DE10Y)
    fred_ratio: list[str] | None = None   # [A, B] FRED ids -> A / B (e.g. HY OAS / IG OAS)
    eia_wpsr: str | None = None    # "spr" | "commercial" — EIA WPSR table1.csv stocks
    wei: bool = False              # Dallas Fed Weekly Economic Index (.xlsx)


@dataclass(frozen=True)
class ScorecardRowCfg:
    series: str          # cycle_series id (resolves to cycle:<id>)
    kind: str = "pct"    # "pct" for prices/levels, "bp" for yields/spreads/ratios in %


@dataclass(frozen=True)
class CycleRowCfg:
    series: str
    overlay: str | None = None     # right-axis series on the click-through chart


@dataclass(frozen=True)
class CyclePanelCfg:
    title: str
    rows: list[CycleRowCfg]


@dataclass(frozen=True)
class CycleTabCfg:
    id: str
    label: str
    panels: list[CyclePanelCfg]


@dataclass(frozen=True)
class CalendarMapEntry:
    country: str
    match: str
    series: str


@dataclass(frozen=True)
class FeedCfg:
    name: str
    url: str


@dataclass(frozen=True)
class StrategyCfg:
    id: str
    label: str


@dataclass(frozen=True)
class ChainCfg:
    id: int
    name: str
    usdc: str  # USDC token address on this chain, lowercase


@dataclass(frozen=True)
class DefiCfg:
    asset: str
    strategies: list[StrategyCfg]  # config order == dedupe priority (most conservative first)
    chains: list[ChainCfg]
    midnight_chains: list[int]     # subset of chains ids that have Midnight deployments
    token_symbols: dict[str, str]  # lowercase collateral address -> display symbol
    morpho_graphql: str = "https://blue-api.morpho.org/graphql"
    morpho_first: int = 25


@dataclass(frozen=True)
class AaveRefCfg:
    chain: str   # display abbr (BASE/ETH/ARB); also the id token, so keep it stable
    rpc: str
    pool: str    # lowercase
    asset: str   # lowercase
    symbol: str  # asset display symbol (USDC/USDT); also the id token

    # Derived, not configured: the BASE/USDC ids resolve to the original
    # aave-base-usdc-* series, preserving their accumulated history.
    @property
    def supply_id(self) -> str:
        return f"aave-{self.chain.lower()}-{self.symbol.lower()}-supply"

    @property
    def borrow_id(self) -> str:
        return f"aave-{self.chain.lower()}-{self.symbol.lower()}-borrow"

    @property
    def supply_label(self) -> str:
        return f"AAVE {self.symbol} {self.chain} SUP"

    @property
    def borrow_label(self) -> str:
        return f"AAVE {self.symbol} {self.chain} BOR"


@dataclass(frozen=True)
class LlamaChartCfg:
    pool: str
    series: str


@dataclass(frozen=True)
class PendleRefCfg:
    chain_id: int
    address: str  # lowercase
    implied_id: str
    implied_label: str
    underlying_id: str
    underlying_label: str


@dataclass(frozen=True)
class FundingRefCfg:
    symbol: str
    id: str
    label: str


@dataclass(frozen=True)
class RefsCfg:
    aave: list[AaveRefCfg]
    llama_chart: list[LlamaChartCfg]
    pendle: list[PendleRefCfg]
    funding: list[FundingRefCfg]


@dataclass(frozen=True)
class OfrSeriesCfg:
    id: str       # short id for docs, e.g. "hf-gav"
    name: str
    mnemonic: str  # OFR mnemonic, e.g. FPF-ALLQHF_GAV_SUM
    unit: str


@dataclass(frozen=True)
class CftcPosCfg:
    dataset: str  # tff | cit | disagg (see fetchers/cftc_pos.py DATASETS)
    code: str     # CFTC contract market code
    label: str
    groups: list[str]  # per-dataset group keys, see fetchers/cftc_pos.py COLUMNS


@dataclass(frozen=True)
class TicCfg:
    countries: list[str]  # holder names as in TIC Table 5; Grand Total always added


@dataclass(frozen=True)
class ThirteenFWatchCfg:
    name: str
    cik: str  # zero-padded to 10 digits at use


@dataclass(frozen=True)
class ThirteenFCfg:
    user_agent: str  # SEC mandates a descriptive UA with contact; no default is safe
    watchlist: list[ThirteenFWatchCfg]


@dataclass(frozen=True)
class AuctionsCfg:
    buckets: list[str]  # e.g. ["Note-10Y", "Bill-4W"]; other auctions ignored
    lookback_days: int  # history window pulled on each run


@dataclass(frozen=True)
class DealerSeriesCfg:
    id: str       # short id, e.g. "ust-net"
    name: str
    keyid: str    # NY Fed PD timeseries keyid, e.g. PDPOSGST-TOT
    unit: str


@dataclass(frozen=True)
class Config:
    db_path: str
    calendar_url: str
    max_news: int
    cadences: dict[str, int]
    indexes: list[IndexCfg]
    bonds: list[BondCfg]
    cb_rates: list[CbRateCfg]
    series: list[SeriesCfg]
    cycle_series: list[CycleSeriesCfg]
    cycle_tabs: list[CycleTabCfg]
    calendar_map: list[CalendarMapEntry]
    feeds: list[FeedCfg]
    zyfai_base: str
    midnight_base: str
    defi: DefiCfg
    refs: RefsCfg
    ofr_series: list[OfrSeriesCfg]
    cftc_pos: list[CftcPosCfg]
    tic: TicCfg
    thirteenf: ThirteenFCfg
    auctions: AuctionsCfg
    dealer: list[DealerSeriesCfg]
    scorecard: list[ScorecardRowCfg] = field(default_factory=list)


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    return Config(
        db_path=os.environ.get("DB_PATH", raw["db_path"]),
        calendar_url=raw["calendar_url"],
        max_news=raw["max_news"],
        cadences=dict(raw["cadences"]),
        indexes=[IndexCfg(**i) for i in raw["indexes"]],
        bonds=[BondCfg(**b) for b in raw["bonds"]],
        cb_rates=[CbRateCfg(**c) for c in raw["cb_rates"]],
        series=[SeriesCfg(**s) for s in raw["series"]],
        cycle_series=[CycleSeriesCfg(**s) for s in raw["cycle_series"]],
        cycle_tabs=[
            CycleTabCfg(
                id=t["id"], label=t["label"],
                panels=[
                    CyclePanelCfg(
                        title=p["title"],
                        rows=[CycleRowCfg(**r) for r in p["rows"]],
                    )
                    for p in t["panels"]
                ],
            )
            for t in raw["cycle_tabs"]
        ],
        ofr_series=[OfrSeriesCfg(**s) for s in raw["ofr_series"]],
        cftc_pos=[CftcPosCfg(**c) for c in raw["cftc_pos"]],
        tic=TicCfg(countries=list(raw["tic"]["countries"])),
        thirteenf=ThirteenFCfg(
            user_agent=raw["thirteenf"]["user_agent"],
            watchlist=[ThirteenFWatchCfg(**w) for w in raw["thirteenf"]["watchlist"]],
        ),
        auctions=AuctionsCfg(
            buckets=list(raw["auctions"]["buckets"]),
            lookback_days=int(raw["auctions"]["lookback_days"]),
        ),
        scorecard=[ScorecardRowCfg(**r) for r in raw.get("scorecard", [])],
        dealer=[DealerSeriesCfg(**s) for s in raw["dealer"]],
        calendar_map=[CalendarMapEntry(**m) for m in raw["calendar_map"]],
        feeds=[FeedCfg(**f) for f in raw["feeds"]],
        zyfai_base=raw["zyfai_base"],
        midnight_base=raw["midnight_base"],
        defi=DefiCfg(
            asset=raw["defi"]["asset"],
            strategies=[StrategyCfg(**s) for s in raw["defi"]["strategies"]],
            chains=[
                ChainCfg(id=c["id"], name=c["name"], usdc=c["usdc"].lower())
                for c in raw["defi"]["chains"]
            ],
            midnight_chains=list(raw["defi"]["midnight_chains"]),
            token_symbols={
                k.lower(): v
                for k, v in (raw["defi"].get("token_symbols") or {}).items()
            },
            morpho_graphql=raw["defi"]["morpho_graphql"],
            morpho_first=raw["defi"]["morpho_first"],
        ),
        refs=RefsCfg(
            aave=[
                AaveRefCfg(
                    chain=a["chain"], rpc=a["rpc"], pool=a["pool"].lower(),
                    asset=a["asset"].lower(), symbol=a["symbol"],
                )
                for a in raw["refs"]["aave"]
            ],
            llama_chart=[
                LlamaChartCfg(pool=c["pool"], series=c["series"])
                for c in raw["refs"]["llama_chart"]
            ],
            pendle=[
                PendleRefCfg(
                    chain_id=p["chain_id"],
                    address=p["address"].lower(),
                    implied_id=p["implied_id"],
                    implied_label=p["implied_label"],
                    underlying_id=p["underlying_id"],
                    underlying_label=p["underlying_label"],
                )
                for p in raw["refs"]["pendle"]
            ],
            funding=[
                FundingRefCfg(symbol=f["symbol"], id=f["id"], label=f["label"])
                for f in raw["refs"]["funding"]
            ],
        ),
    )
