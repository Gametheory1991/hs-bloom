# Local deployment and alerts

This fork already contains the upstream [cleyfe/os-bloom](https://github.com/cleyfe/os-bloom)
collector and UI. The upstream MIT license is retained in `LICENSE`.

## 1. Start with Docker

Install Docker Engine/Desktop with Compose, Git, and obtain a free
[FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html).

```bash
git clone https://github.com/Gametheory1991/os-bloom.git
cd os-bloom
cp .env.example .env
```

Edit `.env` and set `FRED_API_KEY`. Never commit this file. Email and webhooks
are optional; the in-dashboard alerts work without notification credentials.

```bash
docker compose up --build -d
docker compose logs -f collector
curl http://localhost:8080/healthz
```

Open <http://localhost:8080>. Fetchers run at startup and on the cadences in
`config.yaml`; the browser refreshes the dashboard every minute. Data-source
failures are reported per fetcher by `/healthz`. Panels populate as requests
finish, and statistical alerts need enough historical observations.

If port 8080 is occupied, use `UI_PORT=9090 docker compose up --build -d`.
The UI proxies API calls to the collector; no separate API port needs exposing.
To stop: `docker compose down`. The `bloom-data` volume preserves series,
threshold updates, anomaly history, and delivery state. **Do not use
`docker compose down -v` unless you intend to delete this data.**
Back up the volume with the collector stopped before upgrades.

## 2. Open on a phone or tablet

Connect both devices to the same trusted Wi-Fi. Find the computer's LAN IP
(`hostname -I` on Linux, `ipconfig` on Windows, or network settings on macOS).
Open `http://<lan-ip>:8080/` on your phone, allowing the chosen port through the
computer's firewall for your local network only. `localhost` on the phone
refers to the phone, not your computer.

- Tap tabs or swipe left/right on the tab strip, not on tables.
- Scroll wide tables horizontally inside their panels.
- Tap a series, drag across its chart to read values, and tap **Close chart**.
- Rotate the device: charts adapt without reopening.
- Set `DASHBOARD_URL` to the reachable dashboard address for email links.

The PWA manifest supports installation where the browser permits it. Browser
notifications require a secure context (HTTPS or localhost) and the **ENABLE
ALERTS** permission button. They rely on polling while the page is open; this
is not a background Web Push subscription. LAN HTTP is sufficient for viewing
the dashboard, but use server-side email/webhooks for alerts with the page closed.
For public access, use an HTTPS reverse proxy and access control rather than
forwarding an unprotected local port.

## 3. Configure event notifications and daily newsletters

In `.env`, enable SMTP using your provider's settings. For Gmail:

```dotenv
SMTP_ENABLED=1
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USE_SSL=1
SMTP_STARTTLS=0
SMTP_USERNAME=yourname@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=yourname@gmail.com
SMTP_TO=recipient@example.com
DASHBOARD_URL=http://192.168.1.25:8080
```

Use an app password, not your account password. For port 587, set
`SMTP_USE_SSL=0` and `SMTP_STARTTLS=1`. Keep TLS enabled when sending credentials.
SMTP enables both individual event emails and one newsletter per UTC day.
Set `ALERT_EMAIL_ENABLED=0` for newsletter-only email, or
`NEWSLETTER_ENABLED=0` for event-only email; both default to enabled and
require `SMTP_ENABLED=1`. These flags do not disable webhook delivery.
Newsletter delivery is attempted after insights refreshes, not at a fixed
wall-clock hour; failed attempts are retried on later scheduled runs.
At startup it waits for populated time-series data rather than sending an
empty digest that would consume the day's newsletter (`waiting_for_data` state).
The dashboard's newsletter delivery state is also available at `/api/insights`.

For a webhook, set `ALERT_WEBHOOK_URL` to an HTTP(S) endpoint you control
(prefer HTTPS) and optionally `ALERT_WEBHOOK_TOKEN`. A delivery is a JSON POST
with `{"event": {...}}`, an `Idempotency-Key` event ID header, and optional
bearer authorization using the configured webhook token. The endpoint must return a successful HTTP status.
Redirects are not followed. URLs and notification credentials are environment-only,
never accepted by the threshold API or returned in its configuration.

Detection/delivery runs on `cadences.insights` (default 1800 seconds).
Event notifications are sent on detection, not held for the daily newsletter;
they may therefore lag the latest collection by up to this interval.
SQLite deduplicates each series/observation-date/event-kind/direction across
restarts. Each channel records success or error independently and retries
failures on subsequent runs. Delivery states are `pending`, `delivering`,
`error`, `sent`, and `cancelled`. Disabling a series or event kind cancels queued
deliveries without deleting event history; re-enabling does not replay cancelled
events. Already in-flight sends may finish. An interrupted send can be retried; receivers
should honor `Idempotency-Key` to avoid duplicates. Email cannot guarantee
exactly-once delivery after a process crash.
Enabling a notification channel can deliver currently active latest-reading
signals and resume unsent work; it does not rescan every historical data point.

After editing `.env`, recreate the collector:
`docker compose up -d --force-recreate collector`.
After editing YAML, restart it: `docker compose restart collector`.

## 4. Detection logic and thresholds

`collector/src/collector/anomalies.py` examines the latest finite reading in
every configured time-series family (macro, cycle, equity, bonds, policy rates,
and rate references). It applies each series' configured transform first, so
thresholds use the displayed units. Document-only feeds such as news and
calendar releases contribute digest coverage, not numerical anomaly signals.

- **Z-score:** use up to `window` prior observations, excluding the latest.
  After `min_history` observations, compute the population mean/std dev and
  `(latest - mean) / max(std dev, min_std)`. Flag `abs(z) >= z_threshold`.
  The std-dev floor allows constant baselines without division by zero.
- **Range:** compare against explicit `range_min`/`range_max` when provided.
  Otherwise use the historical min/max expanded by `range_margin` standard
  deviations. Historical ranges require `min_history`; explicit bounds do not.
- **Reversal:** compare slopes over two consecutive `reversal_window`
  observation windows. Their signs must oppose, the newest step must agree
  with the new direction, and both legs must move at least `reversal_z`
  baseline standard deviations.
- **Trend summaries:** retain the existing one-month change analysis, using
  configurable `trend_z`.

Windows count observations, not calendar days. Only latest readings are
evaluated; importing history does not send a historical alert flood.
Signals are statistical flags, not forecasts or investment advice. Stale
feeds and sparse history should be checked before acting.

Example `config.yaml` section (merge into the existing section):

```yaml
alerting_config:
  defaults:
    enabled: true
    z_threshold: 3.0
    window: 180
    min_history: 20
    min_std: 0.01
    range_enabled: true
    range_margin: 1.0
    reversal_enabled: true
    reversal_window: 5
    reversal_z: 1.15
    trend_z: 1.15
  series:
    SPX:
      z_threshold: 3.5
    vix:
      range_min: 0
      range_max: 40
    us-cpi-yoy:
      enabled: false
```

Overrides use public series IDs, not SQLite prefixes. Boolean flags must be
booleans. Window sizes are integers 2–5000; `min_history <= window` and two
reversal windows must fit inside `window`. Scores are positive and at most
100; the std floor is between 1e-9 and 100; range margin is 0–100; explicit lower bounds must be below upper
bounds. Unspecified fields inherit defaults.

Optional `ALERT_*` environment defaults override YAML defaults; names and
examples are in `.env.example`. Persistent API updates take precedence over
both. To remove a per-series API override, submit an empty object for that ID.

## 5. Alert APIs

Read-only endpoints are available without a token:

| Endpoint | Response |
| --- | --- |
| `GET /api/anomalies` | Persistent `items`, `total`, `limit`, `offset`, with per-channel delivery state |
| `GET /api/alerts/config` | Effective `defaults` and per-series `series` overrides; no secrets |
| `GET /api/digest` | Current digest plus today's UTC `daily_summary` of persisted detections |
| `GET /api/insights` | Existing dashboard digest and newsletter delivery state |

Anomaly filters: `series_id`, `kind` (`anomaly`, `range`, `reversal`),
`direction` (`up`, `down`), observation-date `since`/`until` (`YYYY-MM-DD`),
`limit` (1–200, default 50), and `offset` (0–10000). Daily summary returns up
to 200 events and the full event count.

```bash
curl 'http://localhost:8080/api/anomalies?series_id=SPX&limit=20'
curl http://localhost:8080/api/digest
```

`POST /api/alerts/config` is disabled until you set a strong random
`ALERT_CONFIG_TOKEN` in `.env`. Generate one with `openssl rand -hex 32`,
store it securely, recreate the collector, and use it as a bearer token.
Never place this token in browser JavaScript or URLs. Use HTTPS off localhost.

```bash
read -r -s -p 'Alert config token: ' ALERT_CONFIG_TOKEN; echo
curl -X POST http://localhost:8080/api/alerts/config \
  --oauth2-bearer "$ALERT_CONFIG_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"defaults":{"z_threshold":3.0},"series":{"SPX":{"z_threshold":3.5}}}'
unset ALERT_CONFIG_TOKEN
```

This merges a threshold-only patch, persists it in SQLite, and refreshes
dashboard insights. Unknown series, unknown fields, invalid bounds, and
non-finite numbers are rejected. Missing/invalid authentication returns 401;
disabled writes return 503.

## 6. Run without Docker / develop

Python 3.12+ is required. From the repository root:

```bash
python3 -m venv collector/.venv
collector/.venv/bin/pip install -e './collector[dev]'
set -a; . ./.env; set +a
mkdir -p data
DB_PATH=./data/bloom.db CONFIG_PATH=./config.yaml SERVE_UI=1 \
  collector/.venv/bin/python -m collector.main
```

Open <http://localhost:8000> (or `http://<lan-ip>:8000`).
The UI is plain ES modules with a vendored uPlot library; no npm build is
needed. Use `make test` for existing collector tests and `make smoke` against
a running deployment. For mobile checks, use browser device emulation plus
an actual phone: check 320px/375px, tablet and desktop widths, swipe tabs,
scroll tables, and rotate an open chart.

The root `Dockerfile` can also serve UI/API in one container on port 10000.
For persistent standalone storage:

```bash
docker build -t os-bloom .
docker run --env-file .env -e DB_PATH=/data/bloom.db \
  -p 10000:10000 -v bloom-data:/data os-bloom
```

See the README for Render deployment. Render's default free-service
`/tmp/bloom.db` is ephemeral; losing it also resets alert deduplication and
saved thresholds. Use persistent storage for durable alerts.
