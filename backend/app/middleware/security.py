"""AetherGIS — Security + Rate Limiting Middleware (MODULE 12)."""
from __future__ import annotations

import time
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.config import get_settings
from backend.app.utils.logging import get_logger
from backend.app.services.auth_service import verify_session_token

settings = get_settings()
logger = get_logger(__name__)

# Rate limit config
RATE_LIMIT_REQUESTS = getattr(settings, "rate_limit_requests_per_minute", 1000)
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_BURST = 200 # Allow more concurrent telemetry requests
MAX_BODY_SIZE_MB = 50

# Paths that bypass rate limiting
RATE_LIMIT_EXEMPT_EXACT = {
    "/api/v1/health",
    "/api/v1/",
    "/",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
}

RATE_LIMIT_EXEMPT_CONTAINS = {
    "/frames/",
    "/video/",
    "/pipeline/",
    "/archive/",
}

# Paths that require API key or Session
API_KEY_REQUIRED_PREFIXES = [
    "/api/v1/jobs",
    "/api/v1/pipeline",
    "/api/v1/region",
    "/api/v1/metrics",
    "/api/v1/system",
]

API_KEY_EXEMPT_EXACT = {
    "/api/v1/system/config",
    "/api/v1/system/providers",
    "/api/v1/system/session/status",
    "/api/v1/system/session/heartbeat",
    "/api/v1/system/session/release",
}


def _get_redis():
    try:
        import redis as redis_sync
        r = redis_sync.from_url(settings.redis_url, socket_connect_timeout=1, decode_responses=True)
        r.ping()
        return r
    except Exception:
        return None


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit_redis(ip: str, r) -> tuple[bool, int, int]:
    key = f"aethergis:ratelimit:{ip}"
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {str(now): now})
    pipe.zcard(key)
    pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS + 5)
    results = pipe.execute()

    count = results[2]
    allowed = count <= RATE_LIMIT_REQUESTS + RATE_LIMIT_BURST
    retry_after = RATE_LIMIT_WINDOW_SECONDS if not allowed else 0
    return allowed, count, retry_after


_mem_rate: dict[str, list[float]] = {}


def _check_rate_limit_memory(ip: str) -> tuple[bool, int, int]:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    history = _mem_rate.get(ip, [])
    history = [t for t in history if t > window_start]
    history.append(now)
    _mem_rate[ip] = history[-500:]
    count = len(history)
    allowed = count <= RATE_LIMIT_REQUESTS + RATE_LIMIT_BURST
    retry_after = RATE_LIMIT_WINDOW_SECONDS if not allowed else 0
    return allowed, count, retry_after


def _check_auth(request: Request) -> bool:
    """Validate API key or internal JWT session."""
    configured_keys = settings.api_keys_list
    
    # 1. Check Session Cookie (JWT)
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        payload = verify_session_token(token)
        if payload:
            return True

    # 2. Check API Keys (if configured)
    if configured_keys:
        provided = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
        )
        if provided in configured_keys:
            return True

    # 3. Development Mode Bypass (only if no API keys are set)
    if settings.aether_mode == 'development' and not configured_keys:
        return True

    return False


class SecurityMiddleware(BaseHTTPMiddleware):
    """Combined security middleware: CSRF + rate limiting + auth + security headers."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        ip = _get_client_ip(request)

        # ── CSRF Protection (Double-Submit Header) ─────────────────────────────
        # Only enforce in production or for authenticated session requests
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # Simple header-based check for AJAX requests
            # If the session cookie is present, we MUST see the CSRF header
            if request.cookies.get(settings.session_cookie_name):
                csrf_header = request.headers.get(settings.csrf_header_name.lower())
                if not csrf_header:
                    logger.warning("CSRF attempt blocked", ip=ip, path=path)
                    return JSONResponse(
                        status_code=403,
                        content={"detail": f"CSRF protection: Missing {settings.csrf_header_name} header."}
                    )

        # ── Rate limiting ─────────────────────────────────────────────────────
        if settings.aether_mode == 'production':
            is_exempt = (
                path in RATE_LIMIT_EXEMPT_EXACT or 
                any(sub in path for sub in RATE_LIMIT_EXEMPT_CONTAINS)
            )
            
            if not is_exempt:
                r = _get_redis()
                if r:
                    allowed, count, retry_after = _check_rate_limit_redis(ip, r)
                else:
                    allowed, count, retry_after = _check_rate_limit_memory(ip)

                if not allowed:
                    logger.warning("Rate limit exceeded", ip=ip, count=count, path=path)
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": "Too many requests. Please slow down.",
                            "retry_after_seconds": retry_after,
                        },
                        headers={
                            "Retry-After": str(retry_after),
                            "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                            "X-RateLimit-Remaining": "0",
                        },
                    )

        # ── Auth check (API Key or JWT) ───────────────────────────────────────
        requires_auth = any(path.startswith(prefix) for prefix in API_KEY_REQUIRED_PREFIXES) and path not in API_KEY_EXEMPT_EXACT
        if requires_auth and not _check_auth(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid authentication. Please login."},
            )

        # ── Request size validation ───────────────────────────────────────────
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_SIZE_MB * 1_048_576:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large. Max {MAX_BODY_SIZE_MB}MB."},
            )

        # ── Process request ───────────────────────────────────────────────────
        response = await call_next(request)

        # ── Inject security headers ───────────────────────────────────────────
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)

        if settings.aether_mode == 'production':
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
            # Allow OSM and common basemap providers
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "img-src 'self' data: blob: https://*.nasa.gov https://*.gov.in https://*.jaxa.jp https://*.openstreetmap.org https://*.tile.openstreetmap.org https://*.basemaps.cartocdn.com; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "connect-src 'self' https://*.nasa.gov; "
            )

        return response
