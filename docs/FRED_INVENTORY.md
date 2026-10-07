# FRED Inventory — os-bloom

**As of 2026-10-06.** 119 unique FRED mnemonics across `config.yaml` (`series:`, `cycle_series:`, `bonds:`, `cb_rates:`).
All are pulled via the FRED observations API (`collector/src/collector/fetchers/fred.py`) with **no date bounds — full history on first run, incremental upserts after** (`store.upsert_points`).

Key: freq D=daily, W=weekly, M=monthly, Q=quarterly. "Depth" = what FRED serves (verified spot-checks 2026-10-06: CPI→1948, payrolls→1939, 30Y mortgage→1971).

## 1. Rates & Curve (18)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| DGS1MO | 1M T-bill | D | Briefing T1, curve |
| DGS3MO | 3M T-bill | D | World bonds matrix, briefing T1 |
| DGS6MO | 6M T-bill | D | Briefing T1 |
| DGS1 | 1Y T-bill | D | Briefing T1 |
| DGS2 | 2Y Treasury | D | Briefing T1, curve |
| DGS3 | 3Y Treasury | D | Curve |
| DGS5 | 5Y Treasury | D | Briefing T1, curve |
| DGS7 | 7Y Treasury | D | Curve |
| DGS10 | 10Y Treasury | D | PULSE stress, briefing T1, spreads |
| DGS20 | 20Y Treasury | D | Curve |
| DGS30 | 30Y Treasury | D | Briefing T1, curve |
| T10Y2Y | 10Y–2Y spread | D | Curve regime |
| T10Y3M | 10Y–3M spread | D | Recession watch |
| T5YIE | 5Y breakeven inflation | D | Inflation |
| T10YIE | 10Y breakeven inflation | D | Inflation |
| T5YIFR | 5Y5Y forward inflation | D | Inflation |
| DFII10 | 10Y TIPS real yield | D | Real rates |
| DFEDTARU | Fed funds target upper | D | Policy rate, briefing T1 |

## 2. Global Yields & Policy Rates (13)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| IRLTLT01FRM156N | France 10Y | M | World bonds matrix |
| IRLTLT01ITM156N | Italy 10Y | M | World bonds matrix |
| IRLTLT01ESM156N | Spain 10Y | M | World bonds matrix |
| IRLTLT01NLM156N | Netherlands 10Y | M | World bonds matrix |
| IRLTLT01BEM156N | Belgium 10Y | M | World bonds matrix |
| IRLTLT01GBM156N | UK 10Y | M | World bonds matrix |
| IRLTLT01JPM156N | Japan 10Y | M | World bonds matrix, US-JP spread |
| IRLTLT01CAM156N | Canada 10Y | M | World bonds matrix |
| IRLTLT01AUM156N | Australia 10Y | M | World bonds matrix |
| IRLTLT01CHM156N | Switzerland 10Y | M | World bonds matrix |
| IRLTLT01SEM156N | Sweden 10Y | M | World bonds matrix |
| IRLTLT01DEM156N | Germany 10Y | M | US-DE spread |
| ECBDFR | ECB deposit facility rate | D | Policy rates matrix |

Note: OECD harmonized 10Y are **monthly** — daily bp changes read null for these rows (panels degrade gracefully). No UK/JP/CA/AU/CH/SE policy-rate rows: no verified keyless daily FRED series for BoE/BoJ/BoC/RBA/SNB/Riksbank.

## 3. Credit Spreads — OAS (16)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| BAMLC0A0CM | US IG OAS | D | PULSE stress, HY/IG ratio |
| BAMLH0A0HYM2 | US HY OAS | D | PULSE stress, HY/IG ratio |
| BAMLHE00EHYIOAS | Euro HY OAS | D | Credit |
| BAMLC0A1CAAA | US AAA OAS | D | Credit ladder |
| BAMLC0A2CAA | US AA OAS | D | Credit ladder |
| BAMLC0A3CA | US A OAS | D | Credit ladder |
| BAMLC0A4CBBB | US BBB OAS | D | Credit ladder |
| BAMLH0A1HYBB | US BB OAS | D | Credit ladder |
| BAMLH0A2HYB | US B OAS | D | Credit ladder |
| BAMLH0A3HYC | US CCC & lower OAS | D | **PULSE stress KPI (new 2026-10-06)** |
| BAMLC1A0C13Y | US Corp 1–3Y OAS | D | Curve-by-tenor |
| BAMLC2A0C35Y | US Corp 3–5Y OAS | D | Curve-by-tenor |
| BAMLC3A0C57Y | US Corp 5–7Y OAS | D | Curve-by-tenor |
| BAMLC4A0C710Y | US Corp 7–10Y OAS | D | Curve-by-tenor |
| BAMLC7A0C1015Y | US Corp 10–15Y OAS | D | Curve-by-tenor |
| BAMLC8A0C15PY | US Corp 15Y+ OAS | D | Curve-by-tenor |

**Known limit:** ICE restricts FRED to **3 years** of BofA index history (all OAS/EY series). Deeper history requires ICE directly (licensed/paid).

## 4. Credit — Effective Yields (15)

| Mnemonic | Description | Freq |
|---|---|---|
| BAMLC0A0CMEY | US IG effective yield | D |
| BAMLH0A0HYM2EY | US HY effective yield | D |
| BAMLC0A1CAAAEY | US AAA yield | D |
| BAMLC0A2CAAEY | US AA yield | D |
| BAMLC0A3CAEY | US A yield | D |
| BAMLC0A4CBBBEY | US BBB yield | D |
| BAMLH0A1HYBBEY | US BB yield | D |
| BAMLH0A2HYBEY | US B yield | D |
| BAMLH0A3HYCEY | US CCC yield | D |
| BAMLC1A0C13YEY | US Corp 1–3Y yield | D |
| BAMLC2A0C35YEY | US Corp 3–5Y yield | D |
| BAMLC3A0C57YEY | US Corp 5–7Y yield | D |
| BAMLC4A0C710YEY | US Corp 7–10Y yield | D |
| BAMLC7A0C1015YEY | US Corp 10–15Y yield | D |
| BAMLC8A0C15PYEY | US Corp 15Y+ yield | D |

## 5. Labor (9)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| PAYEMS | Nonfarm payrolls (level; Δk shown) | M | **PULSE stress KPI**, briefing T8 |
| UNRATE | Unemployment rate | M | **PULSE stress KPI**, briefing T8 |
| U6RATE | U-6 underemployment | M | Labor depth |
| ICSA | Initial jobless claims | W | Labor |
| JTSJOL | JOLTS job openings | M | Labor |
| JTSQUR | Quits rate | M | Labor |
| LNS11300060 | Prime-age LFPR | M | Labor |
| CES0500000003 | Avg hourly earnings | M | Labor (YoY) |
| HTRUCKSSAAR | Heavy truck sales SAAR | M | Cycle proxy |

## 6. Inflation (7)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| CPIAUCSL | CPI (level; YoY + MoM shown) | M | **PULSE stress KPI**, briefing |
| CPILFESL | Core CPI | M | Inflation |
| PCEPI | PCE price index | M | Fed's target gauge |
| MICH | UMich 1Y inflation expectations | M | Expectations |
| UMCSENT | UMich consumer sentiment | M | Cycle |
| CP0000EZ19M086NEST | Eurozone HICP | M | Global macro |
| LRHUTTTTEZM156S | Eurozone unemployment | M | Global macro |

## 7. Housing (5)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| MORTGAGE30US | 30Y mortgage rate | W | **PULSE stress KPI** |
| HOUST | Housing starts | M | Housing |
| PERMIT | Building permits | M | Housing |
| EXHOSLUSM495S | Existing home sales | M | Housing |
| CSUSHPINSA | Case-Shiller home prices | M | Housing (YoY) |

## 8. Fed Balance Sheet / Money (10)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| WALCL | Fed total assets | W | Briefing T2 plumbing |
| WRESBAL | Reserve balances | W | Briefing T2 plumbing |
| RRPONTSYD | ON RRP take-up | D | Briefing T2, funding |
| WMTSECL1 | Foreign custody UST (H.4.1) | W | Foreign demand |
| WTREGEN | Treasury General Account | W/D | Briefing T2 plumbing |
| M2SL | M2 money supply | M | Money (YoY) |
| M2V | M2 velocity | Q | Money |
| SOFR | SOFR | D | Funding |
| OBFR | Overnight bank funding rate | D | Funding |
| IORB | Interest on reserves | D | Funding |

## 9. Fiscal (3)

| Mnemonic | Description | Freq |
|---|---|---|
| GFDEBTN | Federal debt outstanding | Q |
| FYFSD | Federal budget balance | M (FY) |
| CP | Corporate profits | Q (YoY) |

## 10. Credit Conditions / Stress (6)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| NFCI | Chicago Fed NFCI | W | Financial conditions |
| STLFSI4 | St. Louis Fed stress index | W | Financial conditions |
| LOANS | Bank loans & leases | W/M | Bank lending (YoY) |
| DRTSCILM | SLOOS tightening: C&I large/mid | Q | Lending standards |
| DRTSCIS | SLOOS tightening: C&I small | Q | Lending standards |
| DRCRELEXFACBS | CRE loan delinquency rate | Q | CRE stress |

## 11. Commercial Paper (5)

| Mnemonic | Description | Freq |
|---|---|---|
| DFINCP | CP: domestic financial | W |
| FFINCP | CP: foreign financial | W |
| ABCOMP | CP: asset-backed | W |
| NFINCP | CP: nonfinancial | W |
| OTHCOMP | CP: other | W |

Aggregates built in config: cp-total, cp-financial (=DFINCP+FFINCP).

## 12. Recession / Cycle Dummies & Other (12)

| Mnemonic | Description | Freq | Panel use |
|---|---|---|---|
| USREC | NBER recession indicator | M | Cycle overlays |
| SAHMREALTIME | Sahm Rule (0.50 trigger) | M | **PULSE stress KPI** |
| A191RL1Q225SBEA | Real GDP QoQ SAAR | Q | Growth |
| CLVMEURSCAB1GQEA19 | Eurozone real GDP QoQ | Q | Global macro |
| RSAFS | Retail sales | M | Consumption (MoM) |
| VIXCLS | VIX close | D | Vol |
| VXNCLS | VXN (Nasdaq vol) | D | Vol |
| DTWEXBGS | USD broad index | D | FX |
| DEXUSEU | EUR/USD | D | FX |
| DEXJPUS | USD/JPY | D | FX |
| DCOILWTICO | WTI crude | D | Energy |
| DCOILBRENTEU | Brent crude | D | Energy |

## Gaps worth knowing (not pulled)

- **HY/IG CDS indices** (CDX) — no free FRED series; Markit licensed.
- **SOFR term rates / futures** — covered via Yahoo (SR3* futures), not FRED.
- **TIPS breakeven by tenor beyond 5Y/10Y** — FRED has them (e.g. T2YIE); not configured.
- **Bank reserves breakdown / discount window** — available on FRED; not configured.
- **State-level labor/housing** — available; national-only currently.
