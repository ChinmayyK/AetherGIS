"""AetherGIS - FastAPI application entry point — PRODUCTION GRADE."""
from __future__ import annotations

from contextlib import asynccontextmanager

import redis as redis_sync
import torch
import json
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text

from backend.app.config import get_settings
from backend.app.db import SessionLocal
from backend.app.models.schemas import HealthResponse
from backend.app.services.persistence import ensure_demo_session, init_database
from backend.app.services.interpolation import get_engine
from backend.app.utils.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info('AetherGIS API starting up', version='2.0.0')
    settings.ensure_dirs()
    init_database()
    ensure_demo_session()

    # Ensure production data directories exist
    for sub in ["runs", "audit", "checkpoints", "metrics"]:
        (settings.data_dir / sub).mkdir(parents=True, exist_ok=True)

    yield
    logger.info('AetherGIS API shut down')


app = FastAPI(
    title='AetherGIS API',
    description=(
        'AI-Based Temporal Enhancement & WebGIS Visualization System — Production Grade.\n\n'
        '**Version 2.0** adds: async job queue, reproducibility manifests, multi-source '
        'satellite providers, smart tile cache, uncertainty maps, change detection, anomaly '
        'detection, geo-region queries, metric aggregation, SSE streaming, security '
        'middleware, failure recovery, and performance monitoring.\n\n'
        '_All interpolated frames are visual approximations and are NOT suitable for '
        'scientific measurement or quantitative analysis._'
    ),
    version='2.0.0',
    lifespan=lifespan,
    docs_url='/api/docs',
    redoc_url='/api/redoc',
    openapi_url='/api/openapi.json',
)

# ── CORS ───────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── Compression ───────────────────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ── Security + Rate Limiting (MODULE 12) ──────────────────────────────────────
try:
    from backend.app.middleware.security import SecurityMiddleware
    app.add_middleware(SecurityMiddleware)
    logger.info("Security middleware loaded")
except Exception as exc:
    logger.warning("Security middleware unavailable", error=str(exc))

# ── Request ID middleware ──────────────────────────────────────────────────────
@app.middleware('http')
async def add_request_id(request: Request, call_next) -> Response:
    import uuid
    import structlog
    request_id = str(uuid.uuid4())[0:8]
    structlog.contextvars.bind_contextvars(request_id=request_id)
    response = await call_next(request)
    response.headers['X-Request-ID'] = request_id
    structlog.contextvars.clear_contextvars()
    return response

# ── Error Handling ────────────────────────────────────────────────────────────
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return Response(
        content=json.dumps({
            "success": False,
            "error": str(exc.detail),
            "status_code": exc.status_code
        }),
        status_code=exc.status_code,
        media_type="application/json"
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return Response(
        content=json.dumps({
            "success": False,
            "error": "Validation failed",
            "details": exc.errors(),
            "status_code": 422
        }),
        status_code=422,
        media_type="application/json"
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", error=str(exc), path=request.url.path)
    return Response(
        content=json.dumps({
            "success": False,
            "error": "Internal Server Error",
            "status_code": 500
        }),
        status_code=500,
        media_type="application/json"
    )
from backend.app.api.routes import layers, pipeline  # noqa: E402

# Existing routes (backward-compat)
app.include_router(pipeline.router, prefix='/api/v1')
app.include_router(layers.router, prefix='/api/v1')
from backend.app.api.routes.sessions import router as sessions_router
app.include_router(sessions_router, prefix='/api/v1')
from backend.app.api.routes.auth import router as auth_router
app.include_router(auth_router, prefix='/api/v1')

# New production routes (MODULE 1 + 2 + 15)
from backend.app.api.routes.jobs import router as jobs_router
app.include_router(jobs_router, prefix='/api/v1')

# Analytics routes (MODULE 4-11 + 14)
from backend.app.api.routes.analytics import (
    cache_router, models_router, analytics_router,
    region_router, metrics_router, system_router, stream_router,
)
app.include_router(cache_router, prefix='/api/v1')
app.include_router(models_router, prefix='/api/v1')
app.include_router(analytics_router, prefix='/api/v1')
app.include_router(region_router, prefix='/api/v1')
app.include_router(metrics_router, prefix='/api/v1')
app.include_router(system_router, prefix='/api/v1')
app.include_router(stream_router, prefix='/api/v1')

# ── Health ─────────────────────────────────────────────────────────────────────
# Module-level connection pool for health checks (reuses connections)
_health_redis_pool: redis_sync.Redis | None = None

def _get_health_redis() -> redis_sync.Redis | None:
    """Get or create Redis connection for health checks."""
    global _health_redis_pool
    if _health_redis_pool is None:
        try:
            _health_redis_pool = redis_sync.from_url(
                settings.redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                max_connections=5,
                decode_responses=True
            )
        except Exception:
            return None
    return _health_redis_pool


@app.get('/api/v1/health', response_model=HealthResponse, tags=['System'])
async def health_check() -> HealthResponse:
    """Health check endpoint with efficient connection reuse."""
    redis_ok = False
    db_ok = False
    
    # Check Redis (with connection reuse)
    try:
        redis_client = _get_health_redis()
        if redis_client:
            redis_client.ping()
            redis_ok = True
    except Exception:
        redis_ok = False
        # Reset pool on failure to force reconnection next time
        global _health_redis_pool
        _health_redis_pool = None
    
    # Check Database (with proper session handling)
    db = None
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass

    # Check GPU availability (cached, non-blocking)
    gpu_ok = torch.cuda.is_available() or (hasattr(torch.backends, 'mps') and torch.backends.mps.is_available())
    gpu_device_name = None
    if gpu_ok:
        if torch.cuda.is_available():
            try:
                gpu_device_name = torch.cuda.get_device_name(0)
            except Exception:
                gpu_device_name = "CUDA"
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            gpu_device_name = "Apple Metal (MPS)"

    # Check model loading status (cached)
    try:
        rife_loaded = get_engine('rife').is_loaded
        film_loaded = get_engine('film').is_loaded
    except Exception:
        rife_loaded = False
        film_loaded = False

    # cpu_fallback_mode: DL weights absent but infra is healthy — LK optical flow active.
    # This is the expected state on OCI ARM64 (no CUDA). Pipeline output is valid.
    cpu_fallback = (not film_loaded and not rife_loaded) and redis_ok and db_ok

    return HealthResponse(
        status='healthy' if redis_ok and db_ok else 'degraded',
        redis_connected=redis_ok,
        db_connected=db_ok,
        gpu_available=gpu_ok,
        gpu_device_name=gpu_device_name,
        rife_model_loaded=rife_loaded,
        film_model_loaded=film_loaded,
        cpu_fallback_mode=cpu_fallback,
    )


# Advanced analytics routes (Modules 1–15)
from backend.app.api.routes.advanced import router as advanced_router
app.include_router(advanced_router, prefix='/api/v1')


@app.get('/', tags=['System'])
async def root() -> dict:
    return {
        'service': 'AetherGIS API',
        'version': '2.0.0',
        'docs': '/api/docs',
        'new_in_v2': [
            'POST /api/v1/jobs — async job queue with priority',
            'GET /api/v1/jobs/{id}/status — ETA + stage tracking',
            'GET /api/v1/jobs/{id}/logs — structured log stream',
            'GET /api/v1/jobs/{id}/reproduce — reproducibility manifest',
            'GET /api/v1/jobs/{id}/audit — full audit trail',
            'GET /api/v1/jobs/{id}/confidence_map/{frame} — uncertainty maps',
            'GET /api/v1/jobs/{id}/change_map/{frame} — change detection',
            'GET /api/v1/jobs/{id}/change_stats — change statistics',
            'GET /api/v1/jobs/{id}/anomaly_report — anomaly detection report',
            'GET /api/v1/jobs/{id}/stream — SSE real-time frame delivery',
            'GET /api/v1/cache/status — tile cache metrics',
            'POST /api/v1/cache/clear — cache invalidation',
            'GET /api/v1/models — interpolation model registry',
            'POST /api/v1/region/query — geo-region spatial statistics',
            'GET /api/v1/metrics/summary — global aggregated metrics',
            'GET /api/v1/system/performance — GPU/CPU/RAM monitoring',
            'GET /api/v1/system/providers — satellite provider list',
        ],
        'disclaimer': 'All interpolated frames are visual approximations and are NOT suitable for scientific measurement.',
    }
