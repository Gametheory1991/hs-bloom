"""Typed loading of config.yaml. Secrets come from env, never from YAML."""
from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml


ALERT_DEFAULTS = {
    "enabled": True,
    "z_threshold": 2.2,
    "window": 180,
    "min_history": 20,
    "min_std": 0.01,
    "range_enabled": True,
    "range_min": None,
    "range_max": None,
    "range_margin": 1.0,
    "reversal_enabled": True,
    "reversal_window": 5,
    "reversal_z": 1.15,
    "trend_z": 1.15,
}


def validate_alerting_config(raw: dict, series_ids: set[str], base: dict | None = None) -> dict:
    """Merge a threshold-only patch; destinations are deliberately not accepted."""
    if not isinstance(raw, dict) or set(raw) - {"defaults", "series"}:
        raise ValueError("alerting_config accepts only defaults and series")
    base = base or {"defaults": ALERT_DEFAULTS, "series": {}}
    defaults = raw.get("defaults", {})
    overrides = raw.get("series", {})
    if not isinstance(defaults, dict) or not isinstance(overrides, dict):
        raise ValueError("defaults and series must be objects")

    def validate(values: dict) -> None:
        if not isinstance(values, dict) or set(values) - set(ALERT_DEFAULTS):
            raise ValueError("unknown alert threshold key")
        for key, value in values.items():
            if key in {"enabled", "range_enabled", "reversal_enabled"}:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be boolean")
            elif key in {"range_min", "range_max"} and value is None:
                continue
            elif isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError(f"{key} must be numeric")
            elif abs(value) > 1e15 or not math.isfinite(value):
                raise ValueError(f"{key} must be finite and bounded")
            elif key in {"window", "min_history", "reversal_window"}:
                if not isinstance(value, int) or not 2 <= value <= 5000:
                    raise ValueError(f"{key} must be an integer between 2 and 5000")
            elif key == "min_std":
                if not 1e-9 <= value <= 100:
                    raise ValueError("min_std must be between 1e-9 and 100")
            elif key in {"z_threshold", "reversal_z", "trend_z"}:
                if not 0 < value <= 100:
                    raise ValueError(f"{key} must be greater than 0 and at most 100")
            elif key == "range_margin" and not 0 <= value <= 100:
                raise ValueError("range_margin must be between 0 and 100")

    def validate_combined(values: dict) -> None:
        validate(values)
        if values["min_history"] > values["window"]:
            raise ValueError("min_history must not exceed window")
        if values["reversal_enabled"] and 2 * values["reversal_window"] > values["window"]:
            raise ValueError("window must contain two reversal windows")
        lo, hi = values["range_min"], values["range_max"]
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError("range_min must be less than range_max")

    validate(defaults)
    result = {
        "defaults": {**ALERT_DEFAULTS, **base["defaults"], **defaults},
        "series": {key: dict(value) for key, value in base["series"].items()},
    }
    validate_combined(result["defaults"])
    for series_id, values in overrides.items():
        if series_id not in series_ids:
            raise ValueError("unknown alert series")
        validate(values)
        if values:
            result["series"][series_id] = {**result["series"].get(series_id, {}), **values}
        else:
            result["series"].pop(series_id, None)
    for series_id, values in result["series"].items():
        if series_id not in series_ids:
            raise ValueError("unknown alert series")
        validate_combined({**result["defaults"], **values})
    return result


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
    """One market-cycle series; exactly one source field is set per entry."""
    id: str
    name: str
    unit: str
    transform: str = "none"
    hidden: bool = False           # fetched + chartable but never a panel row (usrec)
    valid_range: list[float] | None = None  # drop points outside [min, max] (corrupt feeds)
    fred: str | None = None
    dbnomics: str | None = None    # "PROVIDER/dataset/series"
    oecd: str | None = None        # "{flow}/{key}" under the OECD rest/data base
    cftc: str | None = None        # CFTC contract market code
    cboe: str | None = None        # exact ratio name in the CBOE daily JSON
    aaii: str | None = None        # "bull_bear_spread"
    yahoo_ratio: list[str] | None = None  # [numerator, denominator] yahoo symbols


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
    alerting_config: dict = field(default_factory=lambda: {
        "defaults": dict(ALERT_DEFAULTS), "series": {},
    })


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    cfg = Config(
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
    from collector.anomalies import series_catalog

    env_defaults = {}
    for key, default in ALERT_DEFAULTS.items():
        value = os.environ.get(f"ALERT_{key.upper()}")
        if value is None or value == "":
            continue
        if isinstance(default, bool):
            if value.lower() not in {"1", "0", "true", "false"}:
                raise ValueError(f"ALERT_{key.upper()} must be boolean")
            env_defaults[key] = value.lower() in {"1", "true"}
        elif isinstance(default, int):
            env_defaults[key] = int(value)
        else:
            env_defaults[key] = float(value)
    ids = {s.series_id for s in series_catalog(cfg)}
    configured = validate_alerting_config(raw.get("alerting_config", {}), ids)
    configured = validate_alerting_config({"defaults": env_defaults}, ids, configured)
    return Config(**{**cfg.__dict__, "alerting_config": configured})
