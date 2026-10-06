# os-bloom

Customized fork of the upstream self-hosted macro and markets terminal, with
mobile/PWA support and automated anomaly/trend digesting.

<div align="center">

**A self-hosted macro and markets terminal that runs entirely on free data
sources — built AI-first.**

No Bloomberg seat, no paid vendors, no brokerage account —
one free FRED API key is the only credential you need.

Written largely by AI agents, directed and reviewed by a human. That is the
point rather than a disclaimer: os-bloom is both a working terminal and an
experiment in how far AI-assisted development carries a real system — one with
live upstreams, awkward data, and decisions that have to be defended.

[![License](https://img.shields.io/badge/license-MIT-f5a623?style=flat-square)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12+-5f9ea0?style=flat-square)
![Tests](https://img.shields.io/badge/tests-195%20passing-4c9a2a?style=flat-square)
![Paid data sources](https://img.shields.io/badge/paid%20data%20sources-0-f5a623?style=flat-square)
![Built AI-first](https://img.shields.io/badge/built-AI--first-8a63d2?style=flat-square)

<img src="docs/screenshot-mkt.png" alt="os-bloom MKT tab: equity indexes, world bonds, macro calendar and headlines" width="900">

</div>

---

It collects ~60 series on a schedule into a local SQLite file and serves them
as a dense, keyboard-driven terminal UI: macro calendar, world equity indexes,
government bond yields and policy rates, headlines, DeFi yields, and a
five-tab market-cycle chart pack.

This fork also generates an automated digest from the collected data:

- anomaly alerts based on unusually large moves versus recent history
- cross-series trend summaries using rolling 1M moves
- a newsletter-style panel/API payload covering macro, news, DeFi, and rates
- installable mobile/PWA support with browser notifications for fresh digests

## Tabs

Press `1`–`7`, or use `#/mkt`-style URL fragments.

| Tab | Contents |
| --- | --- |
| **MKT** | Macro release calendar (past 7 days + upcoming), 10 world equity indexes, a bond matrix (10Y / 3M / central bank rate, for the US and Germany), and top headlines. Every row opens a click-through chart. |
| **DEFI** | Zyfai decentralized-finance USDC yield tiers, Morpho Midnight fixed-term structure with a hover-readout curve, Morpho markets, and a RATE REFS panel (Aave, Pendle implied APY, BTC perp funding) with history charts. |
| **RISK** | Volatility and hedging (VIX, VXN, put/call), sentiment and rotation (AAII spread, cyclicals/defensives, small/large, gold/silver). |
| **ECON** | ISM PMIs, OECD leading indicators, jobless claims, JOLTS, heavy truck sales, payrolls and industrial production YoY, housing starts, UMich sentiment, M2, breakevens, real rates, dollar index. |
| **CREDIT** | Yield curves (10Y-3M, 10Y-2Y), IG/HY/BBB/CCC option-adjusted spreads, the Chicago Fed NFCI, the St. Louis Fed stress index, and bank lending growth. |
| **PROFIT** | Corporate profits growth. |
| **POS** | CFTC Commitments of Traders net non-commercial positioning (VIX, crude, USD index, GBP). |

The 43 market-cycle series across RISK/ECON/CREDIT/PROFIT/POS refresh daily.

## Every row opens a chart

Click any series and it opens over the dashboard with ten years of history,
NBER recession shading, and an optional second series on a right-hand axis
(marked `⇄` in the tables).

<div align="center">
<img src="docs/screenshot-chart.png" alt="Click-through chart: VIX against the US 10Y-2Y curve, with NBER recession shading" width="900">
</div>

## Quickstart

You need Docker and a free [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html)
(instant, email only). Gmail delivery is optional and uses an app password.

```bash
git clone https://github.com/Gametheory1991/os-bloom.git && cd os-bloom
cp .env.example .env        # then set FRED_API_KEY
docker compose up --build
```

Open <http://localhost:8080>. Panels fill in as the scheduler's first fetches
land — most within a minute, the daily cycle job on its first tick.

To open it from your phone on the same Wi‑Fi, use your computer's LAN IP:

```bash
hostname -I
```

Then open `http://<that-ip>:8080` on your phone. If you want the digest email
to include a clickable mobile-safe link, set `DASHBOARD_URL` in `.env`.

Exact local mobile URL formats:

- dashboard home: `http://<your-lan-ip>:8080/`
- market tab: `http://<your-lan-ip>:8080/#/mkt`
- DeFi tab: `http://<your-lan-ip>:8080/#/defi`

Port 8080 already taken? Set `UI_PORT`:

```bash
UI_PORT=9090 docker compose up --build
```

`GET /healthz` reports, per fetcher, its last run, the source actually used,
and any error. `GET /api/insights` returns the current automated digest and
newsletter delivery state.
`GET /api/etfs` returns the ETF catalog pulled from ETFDB (with `q` and `limit`
query params), and `GET /api/etfs/{symbol}` returns a single ETF row.

### Gmail newsletter delivery

Set these in `/home/runner/work/os-bloom/os-bloom/.env` to enable real email
delivery of the digest:

```bash
SMTP_ENABLED=1
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USE_SSL=1
SMTP_STARTTLS=0
SMTP_USERNAME=yourname@gmail.com
SMTP_PASSWORD=your_gmail_app_password
SMTP_FROM=yourname@gmail.com
SMTP_TO=recipient@example.com
DASHBOARD_URL=http://<your-lan-ip>:8080
```

The collector sends the digest over SMTP after a fresh `insights` digest is
generated and avoids duplicate sends for the same digest content.

### Deploy on Render

This repository now includes a root `Dockerfile` and `render.yaml` for a
single Render web service that serves both the API and UI from one container.

1. Open <https://dashboard.render.com>.
2. Create a new **Web Service** from this repository.
3. Render will read `render.yaml` and use the bundled root `Dockerfile`.
4. Set `FRED_API_KEY` before the first deploy.
5. Render health-checks `GET /healthz` automatically.
6. `RENDER_EXTERNAL_URL` is available automatically on Render, so newsletter links
   can work even if you do not set `DASHBOARD_URL`.
7. Optionally set `DASHBOARD_URL=https://<your-service-name>.onrender.com` if you
   prefer to override the default Render URL.
8. Optionally set the SMTP variables if you want Gmail delivery in production.

Exact hosted mobile URL formats on Render:

- dashboard home: `https://<your-service-name>.onrender.com/`
- market tab: `https://<your-service-name>.onrender.com/#/mkt`
- DeFi tab: `https://<your-service-name>.onrender.com/#/defi`

Note: the Free Render plan uses ephemeral disk, so `/tmp/bloom.db` will reset
when the service restarts or redeploys.

<details>
<summary><b>Running without Docker</b></summary>

```bash
cd collector
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cd .. && set -a && . ./.env && set +a
cd collector && SERVE_UI=1 CONFIG_PATH=../config.yaml .venv/bin/python -m collector.main
```

Serves UI and API together on <http://localhost:8000>.

</details>

## Data sources

Everything below is free. Only FRED requires registration; everything else is
keyless.

| Source | Provides | Key |
| --- | --- | :---: |
| [FRED](https://fred.stlouisfed.org/) | US/EZ macro series, Treasury yields, credit spreads, NFCI, recession bands | free key |
| [DBnomics](https://db.nomics.world/) | ISM manufacturing + services PMI | — |
| [OECD SDMX](https://sdmx.oecd.org/) | Composite leading indicators (US, G4E) | — |
| [CFTC](https://publicreporting.cftc.gov/) | Commitments of Traders positioning | — |
| [CBOE](https://www.cboe.com/) | Daily total + equity put/call ratios | — |
| [AAII](https://www.aaii.com/sentimentsurvey) | Bull-bear sentiment spread (legacy `.xls`) | — |
| [Yahoo Finance](https://finance.yahoo.com/) | Equity index closes and ratio series | — |
| [ETFDB](https://etfdb.com/) via [pyetfdb-scraper](https://github.com/lvxhnat/pyetfdb-scraper) catalog export | ETF universe symbols, names, URLs, and trailing return fields | — |
| [ECB Data Portal](https://data.ecb.europa.eu/) | Euro-area AAA yield curve (3M) | — |
| [Bundesbank](https://www.bundesbank.de/) | German 10Y benchmark | — |
| ForexFactory mirror | Macro release calendar | — |
| FT, CNBC, MarketWatch, ECB, Fed | Headlines, via public RSS | — |
| [Morpho](https://morpho.org/) | Morpho Blue markets, Midnight fixed-term book | — |
| [Pendle](https://www.pendle.finance/) | Implied APY and expiry | — |
| [Binance](https://www.binance.com/) | BTC perpetual funding rate | — |
| [DefiLlama](https://defillama.com/) | APY history backfill | — |
| [Zyfai](https://zyf.ai/) | USDC opportunity tiers | — |
| Public RPCs (Base, Ethereum, Arbitrum) | Aave reserve data, read-only `eth_call` | — |

## Development

```bash
make test    # 195 passing, 1 skipped
make run
make smoke
```
