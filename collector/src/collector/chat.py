"""In-dashboard analyst chat: grounded Q&A over the terminal's own data.

The chat endpoint assembles a context block from the collector's SQLite store
(latest values, the stored insights digest, and 2y history for any series the
question mentions) and sends it to Gemini. Secrets come from env, never
from code — GEMINI_API_KEY is required, CHAT_MODEL is optional.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import httpx

from collector.changes import apply_transform
from collector.config import Config
from collector.store import Store

SYSTEM_PROMPT = """You are the analyst inside the os-bloom macro terminal — a \
Bloomberg-ASK-style desk covering equities, fixed income, and economics.
Rules:
- Ground EVERY factual claim in the data provided below. Never invent a \
price, yield, level, or date.
- If the data you need is missing, say so plainly and offer the closest \
available proxy.
- Answer like a desk analyst: direct, terse, numbers first, then the read. \
No throat-clearing.
- When asked about anomalies or movers, lead with the signal, then the \
likely driver, then what would confirm or refute it.
- Keep answers short enough to read on a phone. Use a few tight bullets \
when listing.
- You always receive a MARKET SNAPSHOT pack (regime, stress scores, vol, \
single-stock movers, country risk, hyperscaler issuance, macro calendar, \
headlines). Use it; don't ask for data it already contains.
- For a single stock ticker you get its sigma-move stats from the weekly \
movers run. You have NO earnings/estimates/fundamentals feed — when asked \
for EPS, earnings, or valuation, say price/positioning only and name the \
gap."""

GEMINI_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
DEFAULT_MODEL = "gemini-flash-latest"
MAX_HISTORY_POINTS = 200

_STOPWORDS = {
    "the", "and", "for", "with", "from", "what", "how", "why", "are", "is",
    "was", "were", "has", "have", "had", "this", "that", "will", "would",
    "can", "about", "into", "over", "under", "between", "their", "there",
    "which", "when", "where", "than", "then", "been", "does", "did", "out",
    "off", "per", "its", "our", "your", "all", "any", "each", "more",
    "most", "such", "rate", "rates", "yield", "yields", "index", "data",
    "level", "levels", "change", "changes", "trend", "price", "prices",
}


def _sig_tokens(text: str) -> list[str]:
    return [
        t for t in re.findall(r"[a-z0-9]+", text.lower())
        if len(t) > 2 and t not in _STOPWORDS
    ]


def match_series(cfg: Config, message: str) -> list[tuple[str, object]]:
    """Fuzzy-match series configs against a user message.

    Returns [(kind, series_cfg)] where kind is "macro" or "cycle"
    (the store-id prefix used for that series). Hidden cycle series
    are skipped. Capped at 6 matches to bound prompt size.
    """
    msg = message.lower()
    msg_tokens = set(re.findall(r"[a-z0-9]+", msg))
    candidates = [("macro", s) for s in cfg.series]
    candidates += [("cycle", s) for s in cfg.cycle_series if not s.hidden]
    matched: list[tuple[str, object]] = []
    for kind, s in candidates:
        if s.id in msg:  # exact id mention, e.g. "us-cpi-yoy"
            matched.append((kind, s))
            continue
        id_tokens = [t for t in s.id.split("-") if len(t) > 2]
        if any(t in msg_tokens for t in id_tokens):
            matched.append((kind, s))
            continue
        name_tokens = _sig_tokens(s.name)
        hits = sum(1 for t in name_tokens if t in msg_tokens)
        if hits >= 2 or (hits == 1 and len(name_tokens) == 1):
            matched.append((kind, s))
    seen: set[str] = set()
    out: list[tuple[str, object]] = []
    for kind, s in matched:
        if s.id in seen:
            continue
        seen.add(s.id)
        out.append((kind, s))
        if len(out) >= 6:
            break
    return out


def _fmt(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "px":
        return f"{value:,.1f}"
    if unit in {"k", "m", "$m", "idx", "pts", "ratio", "fx"}:
        return f"{value:,.2f}"
    if unit == "$bn":
        return f"${value:,.2f}bn"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


def _latest_line(store: Store, store_id: str, name: str, unit: str,
                 transform: str = "none") -> str | None:
    points = apply_transform(store.points(store_id), transform)
    if not points:
        return None
    latest = max(points)
    return f"{name}: {_fmt(points[latest], unit)} ({latest.isoformat()})"


def _doc(store: Store, key: str) -> dict:
    doc = store.doc(key)
    return doc.payload if doc else {}


def build_analyst_pack(store: Store) -> str:
    """Compact live context pack injected before every market question.

    Regime, stress scores, vol highlights, top sigma-movers, country-risk
    extremes, hyperscaler issuance, this week's macro calendar, headlines.
    Budget: well under 3k tokens (~4-6k chars).
    """
    lines = []
    risk = _doc(store, "risk_summary")
    comp = risk.get("components", {}) if risk else {}
    lines.append("MARKET SNAPSHOT:")
    lines.append(
        f"Regime: {risk.get('regime', 'UNKNOWN')}. "
        f"{risk.get('verdict', 'no verdict yet')}"
    )
    basis = (comp.get("basis_stress") or {}).get("value")
    auction = (comp.get("auction_stress") or {}).get("value")
    rec = (comp.get("recession_prob") or {}).get("value")
    lines.append(
        f"Stress — basis-trade: {basis if basis is not None else 'n/a'}/100, "
        f"auction: {auction if auction is not None else 'n/a'}/100, "
        f"recession prob: {rec if rec is not None else 'n/a'}%."
    )

    vd = _doc(store, "voldash")
    if vd.get("rows"):
        rows = [r for r in vd["rows"] if r.get("pctile_1y") is not None]
        if rows:
            richest = max(rows, key=lambda r: r["pctile_1y"])
            cheapest = min(rows, key=lambda r: r["pctile_1y"])
            lines.append(
                f"Vol — {vd.get('regime', 'n/a')}. Richest: {richest['ticker']} "
                f"{richest['implied']:.1f} ({richest['pctile_1y']:.0f}th %ile); "
                f"cheapest: {cheapest['ticker']} {cheapest['implied']:.1f} "
                f"({cheapest['pctile_1y']:.0f}th %ile)."
            )

    mv = _doc(store, "movers")
    for idx_id in ("spx", "ndx"):
        idx = (mv.get("indexes") or {}).get(idx_id) or {}
        label = idx.get("label", idx_id.upper())

        def _ext(win: str, side: str) -> str:
            rows = ((idx.get(win) or {}).get(side)) or []
            if not rows:
                return "n/a"
            r = rows[0]
            return f"{r['symbol']} {r['z']:+.2f}\u03c3 ({r['ret_pct']:+.1f}%)"

        if idx:
            lines.append(
                f"Movers {label} — 5d up: {_ext('win5d', 'up')}, down: "
                f"{_ext('win5d', 'down')}; 20d up: {_ext('win20d', 'up')}, "
                f"down: {_ext('win20d', 'down')}."
            )

    cr = _doc(store, "country_risk")
    countries = cr.get("countries") or []
    red = [c["code"] for c in countries if c.get("bucket") == "red"]
    green = [c["code"] for c in countries if c.get("bucket") == "green"]
    if countries:
        lines.append(
            f"Country risk — red zone: {', '.join(red) if red else 'none'}; "
            f"stable: {', '.join(green) if green else 'none'} "
            f"({len(countries)} scored)."
        )

    hyper = _doc(store, "hyper")
    iss = hyper.get("issuances") or []
    if iss:
        bits = []
        for e in iss[:3]:
            tranches = e.get("tranches") or []
            if tranches:
                t0 = tranches[0]
                size = (f"${t0['principal_usd'] / 1e9:.1f}B" if t0.get("principal_usd")
                        else "")
                bits.append(
                    f"{e['issuer']} {e['form']} {e['filing_date']}: "
                    f"{t0['coupon_pct']:.3f}% {t0.get('maturity_year') or 'n/a'} "
                    f"{size}".strip()
                )
            else:
                bits.append(f"{e['issuer']} {e['form']} {e['filing_date']}")
        lines.append("Hyperscaler issuance (90d): " + "; ".join(bits) + ".")
    else:
        lines.append("Hyperscaler issuance (90d): none filed.")

    cal = _doc(store, "macro_calendar")
    releases = (cal.get("releases") or [])[:5]
    if releases:
        lines.append("Macro this week: " + "; ".join(
            f"{r.get('country', '')} {r.get('event', '')} "
            f"({str(r.get('time', ''))[:16]})".strip()
            for r in releases
        ) + ".")

    news = _doc(store, "news")
    items = (news.get("items") or [])[:5]
    if items:
        lines.append("Headlines: " + " | ".join(
            str(i.get("title", ""))[:90] for i in items))
    return "\n".join(lines)


def _constituents() -> set[str]:
    """SPX + NDX tickers from the vendored lists (same source as movers)."""
    try:
        from importlib.resources import files
        out: set[str] = set()
        for fn in ("sp500.txt", "ndx100.txt"):
            text = files("collector.data").joinpath(fn).read_text()
            out.update(line.strip().upper()
                       for line in text.splitlines() if line.strip())
        return out
    except Exception:  # noqa: BLE001 — missing data degrades lookup only
        return set()


def company_card(store: Store, message: str) -> str | None:
    """Single-stock Q&A: sigma-move stats + vol context for a ticker.

    Returns None when the message names no known constituent. Never invents
    fundamentals — price/positioning only.
    """
    tickers = _constituents()
    if not tickers:
        return None
    tokens = re.findall(r"\b[A-Z]{1,5}\b", message.upper())
    symbol = next((t for t in tokens if t in tickers), None)
    if symbol is None:
        return None
    mv = _doc(store, "movers")
    uni = (mv.get("all") or {}).get(symbol)
    lines = [f"COMPANY: {symbol}"]
    if uni:
        lines.append(
            f"Weekly movers run ({mv.get('asof', 'n/a')}): 5d move "
            f"{uni['z5']:+.2f}\u03c3 ({uni['ret5']:+.1f}%), 20d move "
            f"{uni['z20']:+.2f}\u03c3 ({uni['ret20']:+.1f}%). "
            f"Universe: {uni['idx'].upper()}."
        )
    else:
        lines.append("Not in the latest weekly movers run (may have been "
                     "skipped or the run hasn't completed).")
    vd = _doc(store, "voldash")
    if vd.get("regime"):
        lines.append(f"Vol context: {vd['regime']}.")
    if re.search(r"\b(eps|earnings|estimate|p/e|valuation|revenue)\b",
                 message.lower()):
        lines.append("No earnings/estimates feed — price and positioning "
                     "only; fundamentals not available in this terminal.")
    return "\n".join(lines)


def build_context(store: Store, cfg: Config, message: str) -> tuple[str, list[str]]:
    """Assemble the grounded context block for a chat question.

    Returns (context_text, series_ids_used). The digest comes from the
    stored insights doc (built by the insights scheduler job), never
    recomputed here. A compact analyst pack (regime, stress, vol, movers,
    country risk, issuance, calendar, headlines) is always prepended; a
    company card is appended when the message names a known constituent.
    """
    lines = [build_analyst_pack(store), ""]
    lines.append("LATEST VALUES (terminal data):")
    for s in cfg.series:
        line = _latest_line(store, f"macro:{s.id}", s.name, s.unit, s.transform)
        if line:
            lines.append(line)
    for s in cfg.cycle_series:
        if s.hidden:
            continue
        line = _latest_line(store, f"cycle:{s.id}", s.name, s.unit, s.transform)
        if line:
            lines.append(line)
    for i in cfg.indexes:
        line = _latest_line(store, f"idx:{i.symbol}", i.name, "px")
        if line:
            lines.append(line)
    for b in cfg.bonds:
        line = _latest_line(store, f"yield:{b.country}{b.tenor}",
                            f"{b.country} {b.tenor} yield", "%")
        if line:
            lines.append(line)

    lines.append("\nAUTOMATED DIGEST:")
    doc = store.doc("insights")
    if doc is None:
        lines.append("no digest generated yet")
    else:
        payload = doc.payload
        lines.append(f"generated: {payload.get('generated_at')}")
        for alert in payload.get("alerts", [])[:8]:
            lines.append(
                f"ANOMALY: {alert['name']}: {alert['summary']} "
                f"(as of {alert.get('as_of')})"
            )
        for trend in payload.get("trends", [])[:8]:
            lines.append(
                f"TREND: {trend['name']}: {trend['summary']} "
                f"(as of {trend.get('as_of')})"
            )
        newsletter = payload.get("newsletter", {})
        if newsletter.get("headline"):
            lines.append(f"HEADLINE: {newsletter['headline']}")
        for bullet in newsletter.get("bullets", [])[:6]:
            lines.append(f"- {bullet}")

    matched = match_series(cfg, message)
    series_ids: list[str] = []
    two_years_ago = date.today() - timedelta(days=730)
    for kind, s in matched:
        store_id = f"{kind}:{s.id}"
        points = apply_transform(store.points(store_id, since=two_years_ago),
                                 s.transform)
        series_ids.append(s.id)
        lines.append(f"\nHISTORY 2Y — {s.name} ({s.unit}, native frequency):")
        if not points:
            lines.append("no data")
            continue
        ordered = sorted(points.items())
        step = max(1, len(ordered) // MAX_HISTORY_POINTS)
        for d, v in ordered[::step]:
            lines.append(f"{d.isoformat()}: {v:.4g}")
    card = company_card(store, message)
    if card:
        lines.append(f"\n{card}")
    return "\n".join(lines), series_ids


def ask_gemini(api_key: str, model: str, system: str,
               messages: list[dict], timeout: int = 60) -> str:
    """Call the Gemini generateContent API; return the concatenated text.

    `messages` uses chat roles ("user"/"assistant"); assistant turns are
    mapped to Gemini's "model" role. Raises RuntimeError with a clear
    message on transport or API errors.
    """
    contents = [
        {
            "role": "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
    ]
    try:
        resp = httpx.post(
            GEMINI_URL_TEMPLATE.format(model=model),
            headers={
                "x-goog-api-key": api_key,
                "content-type": "application/json",
            },
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {"maxOutputTokens": 1024},
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"gemini request failed: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"gemini HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"gemini returned invalid JSON: {exc}") from exc
    texts = [
        part.get("text", "")
        for cand in data.get("candidates", [])
        for part in cand.get("content", {}).get("parts", [])
        if part.get("text")
    ]
    if not texts:
        raise RuntimeError(f"gemini returned no text content: {str(data)[:200]}")
    return "".join(texts).strip()
