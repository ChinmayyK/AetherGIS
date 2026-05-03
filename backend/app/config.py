"""AetherGIS Backend - Configuration (pydantic-settings)"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    api_host: str = '127.0.0.1'
    api_port: int = 8000
    log_level: str = 'INFO'
    
    # ── Aether Mode Switch ────────────────────────────────────────────────────
    # 'development' = Local Demo Mode (No Auth, No Queue)
    # 'production'  = Cloud SaaS Mode (Google Auth + Queuing Enabled)
    aether_mode: str = 'development'
    dev_preview_enabled: bool = False
    
    # Changed to str to prevent Pydantic from auto-parsing as JSON list
    cors_origins: str = 'http://localhost:5173,http://localhost:5174,http://localhost:3000'

    @property
    def cors_origins_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(',') if item.strip()]

    nasa_gibs_base_url: str = 'https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi'
    nasa_gibs_wmts_url: str = 'https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/wmts.cgi'
    nasa_gibs_api_key: str = ''
    
    # Google Auth (Required for production)
    google_client_id: str = ''
    google_client_secret: str = ''
    google_callback_url: str = 'http://localhost:8000/api/v1/auth/callback'
    session_cookie_name: str = 'aether_session'
    session_cookie_secure: bool = False
    session_cookie_samesite: str = 'lax'

    # MODULE 12 — Security & JWT
    jwt_secret_key: str = 'aether-dev-secret-change-me-in-production'
    jwt_algorithm: str = 'HS256'
    session_expiry_hours: int = 24
    csrf_header_name: str = 'X-Aether-CSRF'

    bhuvan_wms_url: str = 'https://bhuvan-ras2.nrsc.gov.in/bhuvan/wms'
    bhuvan_api_key: str = ''

    redis_url: str = 'redis://localhost:6379/0'
    celery_broker_url: str = 'redis://localhost:6379/0'
    celery_result_backend: str = 'redis://localhost:6379/1'
    database_url: str | None = None

    _BACKEND_ROOT: Path = Path(__file__).resolve().parent.parent
    _PROJECT_ROOT: Path = _BACKEND_ROOT.parent

    data_dir: Path = _BACKEND_ROOT / 'data'
    exports_dir: Path = _BACKEND_ROOT / 'data' / 'exports'
    cache_dir: Path = _BACKEND_ROOT / 'data' / 'cache'
    ai_models_dir: Path = _BACKEND_ROOT / 'app' / 'ai_models'

    cuda_device: str = 'mps'
    rife_model_path: Path = Path(__file__).parent.parent / 'app' / 'ai_models' / 'rife' / 'flownet.pkl'
    film_model_path: Path = Path(__file__).parent.parent / 'app' / 'ai_models' / 'film' / 'film_net_fp32.pt'
    inference_timeout_seconds: int = 60

    wms_rate_limit_delay: float = 1.0
    wms_max_retries: int = 3
    wms_timeout_seconds: int = 30
    rate_limit_requests_per_minute: int = 600
    max_frames_per_session: int = 48
    max_image_resolution: int = 2048
    max_active_runs: int = 1
    max_queued_runs: int = 1
    run_artifact_ttl_hours: int = 24

    flow_consistency_threshold: float = 0.15
    flow_rejection_threshold: float = 0.40
    large_diff_threshold: float = 0.30
    min_bbox_area: float = 0.01

    cs_weight_flow: float = 0.40
    cs_weight_mad: float = 0.35
    cs_weight_gap: float = 0.25

    high_confidence_threshold: float = 0.75
    medium_confidence_threshold: float = 0.45

    gap_short_max: float = 15.0
    gap_medium_max: float = 30.0
    gap_large_max: float = 60.0

    # ── Production v2 settings ────────────────────────────────────────────────
    # MODULE 12 — Security
    api_keys: str = ''

    @property
    def api_keys_list(self) -> list[str]:
        return [item.strip() for item in self.api_keys.split(',') if item.strip()]
    security_hsts_enabled: bool = False
    
    # MODULE 4 — Cache
    tile_cache_ttl_seconds: int = 3600
    tile_cache_max_memory_items: int = 256

    # MODULE 8 — Anomaly detection
    anomaly_threshold: float = 0.50

    # MODULE 3 — Providers
    himawari_wms_url: str = 'https://www.eorc.jaxa.jp/ptree/himawari-8/api'
    mosdac_wms_url: str = 'https://mosdac.gov.in/live/wms'
    mosdac_api_key: str = ''          

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'aethergis.db').as_posix()}"

    def ensure_dirs(self) -> None:
        for directory in [
            self.data_dir, self.exports_dir, self.cache_dir, self.ai_models_dir,
            self.data_dir / "runs",
            self.data_dir / "audit",
            self.data_dir / "checkpoints",
            self.data_dir / "metrics",
        ]:
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
