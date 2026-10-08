"""Passphrase login gate for the os-bloom collector dashboard.

Standalone module: stdlib + fastapi only, no new dependencies, no per-user
accounts, no password-reset flow.

Environment:
    SITE_PASSPHRASE     shared passphrase. NEVER hardcode it, log it, or
                        write it to any file. If unset, the gate stays OPEN
                        (fail-open) and a single startup warning is logged.
    SITE_COOKIE_SECRET  secret used to HMAC-sign the auth cookie. If unset,
                        the gate stays OPEN (fail-open) and warns once.
    SITE_GATE_ENABLED   set to "false" to disable the gate (kill switch).

Hook-up (applied by the coordinator after WS1's api.py changes land):
    from collector.gate import GateMiddleware, add_login_routes
    app.add_middleware(GateMiddleware)
    add_login_routes(app)

Rate limiting on POST /login is an in-memory dict of timestamps per IP and
resets on process restart — acceptable for this gate.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections import defaultdict
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)

COOKIE_NAME = "osb_auth"
COOKIE_MAX_AGE = 365 * 24 * 3600  # one year, seconds
EXEMPT_PATHS = frozenset({"/healthz", "/api/health", "/login"})
LOGIN_PATH = "/login"
RATE_LIMIT_MAX = 10          # attempts
RATE_LIMIT_WINDOW = 60.0     # seconds

# In-memory per-IP attempt timestamps for POST /login. Resets on restart.
_attempts: dict[str, list[float]] = defaultdict(list)


def _getenv(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def _gate_enabled() -> bool:
    return os.environ.get("SITE_GATE_ENABLED", "").lower() != "false"


def _sign(expiry: int, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"{COOKIE_NAME}|{expiry}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def make_auth_cookie(expiry: int, secret: str) -> str:
    """Build the cookie value: ``<expiry>.<hex-signature>``."""
    return f"{expiry}.{_sign(expiry, secret)}"


def verify_auth_cookie(value: str, secret: str) -> bool:
    """Constant-time check of a cookie value against the shared secret."""
    try:
        expiry_s, sig = value.split(".", 1)
        expiry = int(expiry_s)
    except (ValueError, AttributeError):
        return False
    if expiry <= int(time.time()):
        return False
    return hmac.compare_digest(sig, _sign(expiry, secret))


def _safe_next(raw: str | None) -> str:
    """Allow only same-origin relative redirects; fall back to '/'."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _attempts[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(recent) >= RATE_LIMIT_MAX:
        _attempts[ip] = recent
        return True
    recent.append(now)
    _attempts[ip] = recent
    return False


def _request_authenticated(
    request: Request, passphrase: str, cookie_secret: str
) -> bool:
    """True when the request carries a valid cookie, bearer header, or token."""
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and verify_auth_cookie(cookie, cookie_secret):
        return True
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        if hmac.compare_digest(auth[7:].strip(), passphrase):
            return True
    token = request.query_params.get("token")
    if token and hmac.compare_digest(token, passphrase):
        return True
    return False


class GateMiddleware(BaseHTTPMiddleware):
    """Passphrase gate. Exempts /healthz, /api/health and /login."""

    _warned: set[str] = set()

    def __init__(self, app):  # noqa: ANN001, ANN204 — Starlette middleware signature
        super().__init__(app)
        self.enabled = _gate_enabled()
        self.passphrase = _getenv("SITE_PASSPHRASE")
        self.cookie_secret = _getenv("SITE_COOKIE_SECRET")
        if not self.enabled:
            log.info("passphrase gate disabled via SITE_GATE_ENABLED=false")
        elif self.passphrase is None:
            self._warn_once(
                "no-passphrase",
                "SITE_PASSPHRASE is not set — passphrase gate is OPEN",
            )
        elif self.cookie_secret is None:
            self._warn_once(
                "no-cookie-secret",
                "SITE_COOKIE_SECRET is not set — passphrase gate is OPEN",
            )

    @classmethod
    def _warn_once(cls, key: str, message: str) -> None:
        if key not in cls._warned:
            cls._warned.add(key)
            log.warning(message)

    def _authenticated(self, request: Request) -> bool:
        return _request_authenticated(request, self.passphrase, self.cookie_secret)

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        path = request.url.path
        if path in EXEMPT_PATHS:
            return await call_next(request)
        if not self.enabled or self.passphrase is None or self.cookie_secret is None:
            return await call_next(request)
        if self._authenticated(request):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        return RedirectResponse(
            url=f"{LOGIN_PATH}?next={quote(path, safe='')}", status_code=302
        )


_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>os-bloom &mdash; sign in</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #171410; color: #e8dcc8;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  .card {{
    width: min(380px, 92vw); padding: 32px 28px; border-radius: 12px;
    background: #211c16; border: 1px solid #3a3129;
  }}
  h1 {{ margin: 0 0 6px; font-size: 20px; color: #e8c96a; font-weight: 600; }}
  p.sub {{ margin: 0 0 20px; font-size: 13px; color: #9a8f7d; }}
  label {{ display: block; font-size: 12px; color: #9a8f7d; margin-bottom: 6px; }}
  input[type=password] {{
    width: 100%; padding: 10px 12px; border-radius: 8px; font-size: 15px;
    background: #171410; color: #e8dcc8; border: 1px solid #3a3129; outline: none;
  }}
  input[type=password]:focus {{ border-color: #e8c96a; }}
  button {{
    margin-top: 16px; width: 100%; padding: 10px; border: 0; border-radius: 8px;
    background: #e8c96a; color: #171410; font-size: 15px; font-weight: 600;
    cursor: pointer;
  }}
  button:hover {{ filter: brightness(1.08); }}
  .error {{
    margin-top: 14px; font-size: 13px; color: #e89a6a;
    background: #2a1f18; border: 1px solid #5a3a28; border-radius: 8px;
    padding: 8px 10px;
  }}
</style>
</head>
<body>
  <div class="card">
    <h1>os-bloom</h1>
    <p class="sub">Sign in to continue.</p>
    <form method="post" action="{login_path}">
      <input type="hidden" name="next" value="{next}">
      <label for="passphrase">Passphrase</label>
      <input type="password" id="passphrase" name="passphrase"
             autocomplete="current-password" autofocus>
      <button type="submit">Sign in</button>
    </form>
    {error_block}
  </div>
</body>
</html>
"""


def _render_login_page(next_url: str, error: bool) -> HTMLResponse:
    error_block = (
        '<div class="error">Incorrect passphrase.</div>' if error else ""
    )
    html = _LOGIN_PAGE.format(
        login_path=LOGIN_PATH, next=_safe_next(next_url), error_block=error_block
    )
    return HTMLResponse(html)


def add_login_routes(app: FastAPI) -> None:
    """Register GET/POST /login on the given app."""

    @app.get(LOGIN_PATH, response_class=HTMLResponse)
    async def login_get(request: Request, next: str | None = None):  # noqa: ANN201
        passphrase = _getenv("SITE_PASSPHRASE")
        cookie_secret = _getenv("SITE_COOKIE_SECRET")
        if (
            passphrase
            and cookie_secret
            and _request_authenticated(request, passphrase, cookie_secret)
        ):
            return RedirectResponse(url=_safe_next(next), status_code=302)
        return _render_login_page(next or "/", error=False)

    @app.post(LOGIN_PATH)
    async def login_post(request: Request):  # noqa: ANN201
        passphrase = _getenv("SITE_PASSPHRASE")
        cookie_secret = _getenv("SITE_COOKIE_SECRET")
        form = await request.form()
        next_url = _safe_next(form.get("next") if isinstance(form.get("next"), str) else None)
        ip = request.client.host if request.client else "unknown"
        if _rate_limited(ip):
            return JSONResponse(
                {"detail": "too many login attempts, try again later"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        candidate = form.get("passphrase")
        if (
            passphrase is None
            or cookie_secret is None
            or not isinstance(candidate, str)
            or not hmac.compare_digest(candidate, passphrase)
        ):
            # Generic message only — no hints about what failed or why.
            return _render_login_page(next_url, error=True)
        expiry = int(time.time()) + COOKIE_MAX_AGE
        response = RedirectResponse(url=next_url, status_code=302)
        response.set_cookie(
            COOKIE_NAME,
            make_auth_cookie(expiry, cookie_secret),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response
